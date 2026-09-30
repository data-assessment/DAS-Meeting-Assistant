"""DAS Meeting Assistant — desktop meeting companion.

Architecture:
  - Python engine: pystray tray + meeting state machine + Graph presence poll.
  - FastAPI on localhost: serves the React popup and a WebSocket state stream.
  - pywebview: hosts the React UI in the Win11 WebView2 runtime; shown on
    meeting start, hidden on meeting end.

Threading model:
  - main thread runs webview.start() (required by pywebview).
  - uvicorn (FastAPI + WS) and pystray each run in their own daemon thread.
  - the poll loop is an asyncio task inside uvicorn's event loop.
"""
import asyncio
import ctypes
from ctypes import wintypes
import datetime
import json
import os
import re
import sys
import threading
import time
import webbrowser
import wave
from contextlib import asynccontextmanager

import pystray
import uvicorn
import webview
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw

import config
import paths
from engine import choice_memory, graph_auth, settings_store, suggest
from engine.meeting import write_transcript
from engine.meeting_notes import Notes
from engine.local_ui import LocalUI, StartupError, INSTANCE_HEADER, validate_assets
from engine import notes_i18n
from engine.notes_i18n import t

FRONTEND_DIST = paths.resource_path("frontend", "dist")
ICON_PATH = paths.resource_path("favicon.ico")
DOCS_DIR = paths.resource_path("docs", "app")
PRESENCE_ACTIVE_TRIGGER = "presence active"
TEAMS_TRANSCRIPT_SOURCE = "Teams transcript"
APP_DISPLAY_NAME = paths.current_profile().display_name

_SINGLE_INSTANCE_MUTEX = "Local\\VoiceTranscriber." + paths.current_profile().data_namespace + ".SingleInstance"
_ERROR_ALREADY_EXISTS = 183


# --------------------------------------------------------------------------- #
# Shared state
# --------------------------------------------------------------------------- #
class AppState:
    def __init__(self) -> None:
        self.active = False
        self.title = ""
        self.started_at: datetime.datetime | None = None
        self.phase = "idle"                   # idle | meeting — the RECORDING dimension only.
        #                                       Background finalize work lives in self.jobs and
        #                                       can run while a new meeting is recording.
        self.jobs: dict[str, "ProcessingJob"] = {}  # id -> job: meetings being finalized now
        self.job_seq = 0                      # monotonic id source for jobs
        # Live transcript is an optional overlay (toggleable any time, incl. mid-meeting);
        # the batch recording/transcript runs regardless. Seeded from STT_MODE.
        self.live_on = config.STT_MODE == "realtime"
        self.auto_start = True
        self.language = config.STT_LANGUAGE  # "" = auto-detect; set to force (e.g. "de")
        self.stt_engine = config.default_stt_engine()  # active batch STT engine
        self.realtime_engine = config.default_realtime_engine()  # active live (realtime) model
        self.live_translated = False         # True when the live model emits already-translated text
        self.translate_to = ""               # "" = no translation; else target lang for live + saved transcript
        self.health = "ok"                    # ok | offline | auth | error (presence reachability)
        self.last_presence: dict | None = None
        self.clients: set[WebSocket] = set()
        self.window = None                    # pywebview window
        self.popup_visible = False
        self.popup_content_height = 320
        self.popup_applied_size: tuple[int, int] | None = None
        # The idle popup floats above other windows as a presence reminder; the
        # export dialogs drop that so they behave like ordinary windows.
        self.popup_on_top = True
        self.popup_hwnd = 0
        self.settings_window = None           # separate settings dialog window
        self.transcript_window = None         # separate live transcript window
        self.docs_window = None               # separate documentation window
        self.notes_window = None
        self.notes_visible = False
        self.notes_view = "meeting"
        self.notes_view_request = 0
        self.notes_width = 680
        self.notes_compact_width = 680
        self.notes_expanded = False
        self.notes_size = None
        self.notes_content_height = 700
        self.notes_starting = False
        self.recorder = None                  # engine.audio.MeetingRecorder while active
        self.background_transcription = None  # rolling batch STT session while active
        self.meeting_detected = False
        self.detected_at: datetime.datetime | None = None
        self.meeting_candidates: list[dict] = []
        self.selected_meeting_candidate_id: str | None = None
        self.meeting_candidate_status = "idle"
        self.meeting_candidate_note: str | None = None
        self.meeting_mapping_rejected = False
        self.realtime = None                  # engine.realtime.RealtimeSession (STT_MODE=realtime)
        self.live_final = ""                  # completed realtime segments
        self.live_partial = ""                # in-progress realtime delta
        self.win_w = 360                      # popup window size, persisted across launches
        self.win_h = 300
        self.last_meeting: dict | None = None  # latest transcript for explicit export actions
        self.onenote_notebooks_cache: dict | None = None
        self.onenote_sections_cache: dict[str, dict] = {}
        self.onenote_choices: dict = {"lastNotebookId": "", "sections": {}}
        # Manual override (UI Start/Stop), to intervene when presence detection goes wrong.
        self.manual_recording = False         # True = recording is user-controlled; presence won't end it
        self.autostart_suppressed = False     # True after a manual Stop while presence is still "active",
        #                                       so the poll loop doesn't immediately re-start; cleared when
        #                                       presence next reads idle.
        self.stop_suggestion: dict | None = None
        self.audio_silence_prompted = False
        self.tray: pystray.Icon | None = None
        self.local_ui: LocalUI | None = None
        self.server: uvicorn.Server | None = None
        self.loop: asyncio.AbstractEventLoop | None = None  # server event loop (for cross-thread broadcasts)
        self.quitting = False                 # set true only on a real Quit


def _saved_ui_language():
    options = settings_store.load().get("meeting_notes_options")
    return options.get("uiLanguage") if isinstance(options, dict) else None


STATE = AppState()
# Before Notes loads the history, so its messages are already in the saved app language.
notes_i18n.set_language(_saved_ui_language())
NOTES = Notes(os.path.join(paths.data_dir(), "Meeting-Notizen"))
_NOTES_WINDOW_LOCK = threading.RLock()


class _SingleInstance:
    """Own a per-user Windows mutex for the lifetime of the app process."""

    def __init__(self) -> None:
        self._handle = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX)
        if not handle:
            raise OSError(kernel32.GetLastError(), "CreateMutexW failed")
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        kernel32 = ctypes.windll.kernel32
        kernel32.ReleaseMutex(self._handle)
        kernel32.CloseHandle(self._handle)
        self._handle = None


def _candidate_payload(candidate: dict) -> dict:
    return {
        "id": candidate.get("id"),
        "subject": candidate.get("subject"),
        "start": candidate.get("start"),
        "end": candidate.get("end"),
        "organizer": candidate.get("organizer"),
        "inviteeCount": len(candidate.get("invitees") or []),
    }


def state_payload() -> dict:
    return {
        "type": "state",
        "notesEnabled": NOTES.enabled,
        "notesError": NOTES.error,
        "notesCount": len(NOTES.reviews),
        "active": STATE.active,
        "title": STATE.title,
        "startedAt": STATE.started_at.isoformat() if STATE.started_at else None,
        "phase": STATE.phase,
        "jobs": [job.payload() for job in STATE.jobs.values()],
        "backgroundTranscriptionStatus": (
            STATE.background_transcription.status
            if STATE.background_transcription is not None else ""
        ),
        "liveOn": STATE.live_on,
        "autoStart": STATE.auto_start,
        "meetingDetected": STATE.meeting_detected,
        "meetingCandidates": [_candidate_payload(c) for c in STATE.meeting_candidates],
        "selectedMeetingCandidateId": STATE.selected_meeting_candidate_id,
        "meetingCandidateStatus": STATE.meeting_candidate_status,
        "meetingCandidateNote": STATE.meeting_candidate_note,
        "language": STATE.language,
        "translateTo": STATE.translate_to,
        "health": STATE.health,
        "version": config.VERSION,
        "sttEngine": STATE.stt_engine,
        "realtimeEngine": STATE.realtime_engine,
        "realtimeEngines": config.realtime_engine_options(),
        "liveTranslated": STATE.live_translated,
        "usageEnabled": bool(config.AI_MODE == "gateway" and config.USAGE_SERVICE_ENDPOINT),
        "manual": STATE.manual_recording,
        "stopSuggestion": STATE.stop_suggestion,
        "transcriptsCanOpen": not NOTES.enabled and _has_transcripts(),
        "recordingsCanRetry": not NOTES.enabled and _has_recordings(),
        "lastTranscriptReady": bool(STATE.last_meeting),
        "oneNoteCanSave": bool(config.ONENOTE_ENABLED and not NOTES.enabled and _has_transcripts()),
        "oneNoteCanOpen": bool(
            config.ONENOTE_ENABLED
            and (STATE.last_meeting or {}).get("onenoteUrl")
        ),
    }


def _has_transcripts() -> bool:
    try:
        if not os.path.isdir(config.TRANSCRIPT_DIR):
            return False
        return any(
            name.lower().endswith((".md", ".txt"))
            for name in os.listdir(config.TRANSCRIPT_DIR)
        )
    except Exception:
        return False


def _has_recordings() -> bool:
    try:
        if not os.path.isdir(config.AUDIO_DIR):
            return False
        return any(
            name.lower().endswith(".wav") and ".part" not in name.lower()
            for name in os.listdir(config.AUDIO_DIR)
        )
    except Exception:
        return False


async def _delete_recording_after_success(audio_path: str | None) -> None:
    if not audio_path:
        return
    try:
        await asyncio.to_thread(os.remove, audio_path)
    except FileNotFoundError:
        pass
    except Exception as exc:
        print("could not delete WAV after successful transcription:", exc)


def _reset_meeting_mapping() -> None:
    STATE.detected_at = None
    STATE.meeting_candidates = []
    STATE.selected_meeting_candidate_id = None
    STATE.meeting_candidate_status = "idle"
    STATE.meeting_candidate_note = None
    STATE.meeting_mapping_rejected = False


def _selected_meeting_candidate() -> dict | None:
    if STATE.meeting_mapping_rejected or not STATE.selected_meeting_candidate_id:
        return None
    return next(
        (
            candidate for candidate in STATE.meeting_candidates
            if candidate.get("id") == STATE.selected_meeting_candidate_id
        ),
        None,
    )


def _calendar_candidate_dict(candidate, index: int) -> dict:
    return {
        "id": f"calendar-{index}",
        "joinUrl": candidate.join_url,
        "subject": candidate.subject or "(no subject)",
        "start": candidate.start,
        "end": candidate.end,
        "organizer": candidate.organizer,
        "invitees": candidate.invitees,
        # Carried into the transcript so the export dialogs can recognise the
        # next occurrence of this meeting.
        "seriesId": getattr(candidate, "series_id", "") or "",
        "iCalUId": getattr(candidate, "ical_uid", "") or "",
    }


def _meeting_meta() -> dict:
    """Identity of the meeting just recorded, for the transcript's meta block.

    Only present when the recording was mapped to a calendar event; an ad-hoc
    call has nothing stable to key on and falls back to attendees and title.
    """
    candidate = _selected_meeting_candidate() or {}
    return {
        "seriesId": str(candidate.get("seriesId") or ""),
        "iCalUId": str(candidate.get("iCalUId") or ""),
        "organizer": str(candidate.get("organizer") or ""),
    }


def _parse_calendar_time(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    raw = value.strip().rstrip("Z")
    if "." in raw:
        head, frac = raw.split(".", 1)
        raw = f"{head}.{frac[:6]}"
    try:
        dt = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def _candidate_overlaps_anchor(candidate: dict, anchor: datetime.datetime) -> bool:
    start = _parse_calendar_time(candidate.get("start"))
    end = _parse_calendar_time(candidate.get("end"))
    if start is None or end is None:
        return False
    target = anchor.astimezone(datetime.timezone.utc)
    return start <= target <= end


def _candidate_lookup_is_current(anchor: datetime.datetime) -> bool:
    return anchor == STATE.started_at or anchor == STATE.detected_at


async def _lookup_meeting_candidates(anchor: datetime.datetime) -> None:
    if not (config.FETCH_ATTENDEES or config.USE_TEAMS_TRANSCRIPT):
        return
    if not _candidate_lookup_is_current(anchor):
        return
    STATE.meeting_candidate_status = "loading"
    STATE.meeting_candidate_note = None
    STATE.meeting_mapping_rejected = False
    await broadcast()
    from engine.attendance import graph_headers, list_calendar_candidates
    try:
        headers = await asyncio.to_thread(graph_headers, False)
        candidates = await asyncio.to_thread(
            list_calendar_candidates,
            headers,
            anchor,
            anchor + datetime.timedelta(minutes=1),
        )
    except Exception as exc:
        if not _candidate_lookup_is_current(anchor):
            return
        STATE.meeting_candidates = []
        STATE.selected_meeting_candidate_id = None
        STATE.meeting_candidate_status = "error"
        STATE.meeting_candidate_note = f"calendar lookup failed: {exc}"
        await broadcast()
        return
    if not _candidate_lookup_is_current(anchor):
        return
    STATE.meeting_candidates = [
        _calendar_candidate_dict(candidate, index)
        for index, candidate in enumerate(candidates)
    ]
    if STATE.meeting_candidates:
        current = [
            candidate for candidate in STATE.meeting_candidates
            if _candidate_overlaps_anchor(candidate, anchor)
        ]
        if len(current) > 1:
            STATE.selected_meeting_candidate_id = None
            STATE.meeting_mapping_rejected = True
            STATE.meeting_candidate_note = "choose which current meeting this is"
        else:
            selected = current[0] if len(current) == 1 else STATE.meeting_candidates[0]
            STATE.selected_meeting_candidate_id = selected["id"]
            STATE.meeting_mapping_rejected = False
            count = len(STATE.meeting_candidates)
            STATE.meeting_candidate_note = (
                f"{count} possible calendar meeting{'s' if count != 1 else ''}"
            )
        STATE.meeting_candidate_status = "matched"
    else:
        STATE.selected_meeting_candidate_id = None
        STATE.meeting_candidate_status = "none"
        STATE.meeting_candidate_note = "no matching calendar meeting found"
    await broadcast()


# --------------------------------------------------------------------------- #
# Persisted UI settings (survive restart + reinstall via the data dir).
# To remember a new user-facing setting: add it to BOTH _persistable_settings()
# (read from STATE) and _apply_settings() (validate + write to STATE), then call
# _save_settings() wherever it changes. That's the whole contract.
# --------------------------------------------------------------------------- #
def _persistable_settings() -> dict:
    # The active batch/live model is now the top of each list in Settings (not a
    # separate runtime pick), and language/translate are per-session popup controls —
    # so only the Live toggle and the window size are remembered here.
    return {
        "meeting_notes_enabled": NOTES.enabled,
        "meeting_notes_setup_complete": NOTES.setup_complete,
        "meeting_notes_onboarding_complete": NOTES.onboarding_complete,
        "meeting_notes_access_mode": config.AI_MODE,
        "meeting_notes_options": {k: v for k, v in NOTES.options.items() if not k.endswith("Key")},
        "live_on": STATE.live_on,
        "auto_start": STATE.auto_start,
        "win_w": STATE.win_w,
        "win_h": STATE.win_h,
        "onenote_choices": STATE.onenote_choices,
    }


def _apply_settings(data: dict) -> None:
    """Apply saved settings onto STATE at startup."""
    NOTES.setup_complete = data.get("meeting_notes_setup_complete") is True
    if config.AI_MODE == "entra" and data.get("meeting_notes_access_mode") != "entra":
        NOTES.setup_complete = False
    # Existing signed-in installations already completed the old setup flow.
    NOTES.onboarding_complete = NOTES.setup_complete and data.get(
        "meeting_notes_onboarding_complete", True) is True
    if isinstance(data.get("meeting_notes_enabled"), bool):
        try:
            NOTES.configure({**(data.get("meeting_notes_options") or {}), "enabled": data["meeting_notes_enabled"]})
        except Exception:
            NOTES.enabled = data["meeting_notes_enabled"]  # never fall back to file recording
    # Independent of the other options: an invalid saved option must not reset the language.
    options = data.get("meeting_notes_options")
    saved_language = options.get("uiLanguage") if isinstance(options, dict) else None
    if saved_language in notes_i18n.LANGUAGES:
        NOTES.set_ui_language(saved_language)
    NOTES.load_credentials()
    if isinstance(data.get("live_on"), bool):
        STATE.live_on = data["live_on"]
    if isinstance(data.get("auto_start"), bool):
        STATE.auto_start = data["auto_start"]
    if isinstance(data.get("win_w"), int) and data["win_w"] > 0:
        STATE.win_w = data["win_w"]
    if isinstance(data.get("win_h"), int) and data["win_h"] > 0:
        STATE.win_h = data["win_h"]
    if isinstance(data.get("onenote_choices"), dict):
        choices = data["onenote_choices"]
        sections = choices.get("sections")
        STATE.onenote_choices = {
            "lastNotebookId": str(choices.get("lastNotebookId") or ""),
            "sections": sections if isinstance(sections, dict) else {},
        }


def _restore_settings() -> None:
    """Load saved settings over the env-seeded defaults, once at startup."""
    data = settings_store.load()
    if data:
        _apply_settings(data)
        print("restored settings:", _persistable_settings())


def _save_settings(*, strict: bool = False) -> None:
    """Snapshot the current user settings to disk. Call after any change."""
    settings_store.save(_persistable_settings(), strict=strict)
    _refresh_tray_language()


_tray_language = None


def _refresh_tray_language() -> None:
    """The Windows tray builds its menu once; rebuild it when the app language changed."""
    global _tray_language
    language = notes_i18n.language()
    if not STATE.tray or language == _tray_language:
        return
    _tray_language = language
    try:
        STATE.tray.update_menu()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# FastAPI app (UI host + WebSocket state stream)
# --------------------------------------------------------------------------- #
async def broadcast() -> None:
    dead = []
    for ws in list(STATE.clients):
        try:
            await ws.send_json(state_payload())
        except Exception:
            dead.append(ws)
    for ws in dead:
        STATE.clients.discard(ws)


class ProcessingJob:
    """A meeting being finalized in the background. Several can run at once — e.g. a
    new meeting is already recording while the previous one is still transcribing — so
    each carries its own status line instead of sharing one global 'processing' state."""

    def __init__(self, title: str, started_at, ended_at) -> None:
        STATE.job_seq += 1
        self.id = f"job{STATE.job_seq}"
        self.title = title or "Meeting"
        self.started_at = started_at
        if started_at:
            self.duration_seconds = max(
                0, int((ended_at - started_at).total_seconds())
            )
        else:
            self.duration_seconds = 0
        self.detail = "Saving recording…"

    def payload(self) -> dict:
        title = self.title
        if title in ("Recording", "Teams-Meeting", "Meeting"):
            title = "Meeting transcript"
        return {"id": self.id, "title": title, "detail": self.detail,
                "startedAt": (
                    self.started_at.isoformat() if self.started_at else None
                ),
                "durationSeconds": self.duration_seconds}


class BackgroundTranscriptionSession:
    """Pre-transcribe closed audio slices while recording continues.

    The final WAV remains the source of truth. If any slice fails, finalization falls
    back to transcribing that full WAV so the saved transcript does not have gaps.
    """

    def __init__(self, recorder, stt_engine: str, language: str) -> None:
        self.recorder = recorder
        self.stt_engine = stt_engine
        self.language = language
        self.chunk_seconds = max(60.0, config.BACKGROUND_TRANSCRIBE_CHUNK_SECONDS)
        self.poll_seconds = max(5.0, config.BACKGROUND_TRANSCRIBE_POLL_SECONDS)
        self.next_start = 0.0
        self.next_index = 0
        self.results = []
        self.errors: list[str] = []
        self.status = ""
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def request_stop(self) -> None:
        self._stop.set()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                await self._transcribe_ready(final=False)

    async def finish(self, job: "ProcessingJob"):
        self.request_stop()
        if self._task is not None:
            try:
                await self._task
            except Exception as exc:
                self.errors.append(f"background worker failed: {exc}")
        await self._transcribe_ready(final=True, job=job)
        if self.errors:
            return None, "; ".join(self.errors)
        if not self.results:
            return None, "no rolling chunks completed"
        from engine.transcribe import merge_chunk_results
        return merge_chunk_results(self.results), None

    async def _transcribe_ready(
        self,
        final: bool,
        job: ProcessingJob | None = None,
    ) -> None:
        duration = await asyncio.to_thread(self.recorder.captured_seconds)
        while True:
            if final:
                if duration <= self.next_start + 0.5:
                    return
                end = duration
            else:
                end = self.next_start + self.chunk_seconds
                if duration < end:
                    return
            await self._transcribe_chunk(self.next_start, end, job)
            self.next_start = end
            self.next_index += 1
            if final:
                return

    async def _transcribe_chunk(
        self,
        start: float,
        end: float,
        job: ProcessingJob | None,
    ) -> None:
        from engine.transcribe import TranscriptChunkResult, transcribe_wav
        index = self.next_index
        if job is not None:
            await _job_status(
                job,
                f"Transcribing remaining audio chunk {index + 1}…",
            )
        else:
            print(f"background transcription: chunk {index + 1} queued")
            self.status = f"Transcribing audio chunk {index + 1}…"
            await broadcast()
        path = None
        try:
            path = await asyncio.to_thread(
                self.recorder.write_segment_wav,
                start,
                end,
                index,
            )
            if not path:
                raise RuntimeError("audio chunk could not be written")
            providers = config.stt_providers_for(self.stt_engine)
            result = await asyncio.to_thread(
                transcribe_wav,
                path,
                None,
                self.language,
                None,
                None,
                providers,
            )
            if result.error:
                self.errors.append(f"chunk {index + 1}: {result.error}")
                if job is None:
                    self.status = f"Audio chunk {index + 1} could not be transcribed"
            else:
                self.results.append(TranscriptChunkResult(index, start, result))
                print(f"background transcription: chunk {index + 1} done")
                if job is None:
                    self.status = f"Audio chunk {index + 1} transcribed"
        except Exception as exc:
            self.errors.append(f"chunk {index + 1}: {exc}")
            if job is None:
                self.status = f"Audio chunk {index + 1} could not be transcribed"
        finally:
            if path:
                try:
                    os.remove(path)
                except Exception:
                    pass
            if job is None:
                await broadcast()


async def _job_status(job: "ProcessingJob", detail: str) -> None:
    """Update one job's status line and push to the popup."""
    job.detail = detail
    await broadcast()


async def broadcast_transcript(text: str) -> None:
    """Push the live (realtime) transcript to the popup."""
    payload = {"type": "transcript", "text": text}
    for ws in list(STATE.clients):
        try:
            await ws.send_json(payload)
        except Exception:
            STATE.clients.discard(ws)


async def _start_realtime() -> None:
    """Open a realtime transcription session and tap the recorder's frames into it."""
    from engine.realtime import RealtimeSession
    ok, why = RealtimeSession.available()
    if not ok:
        print("realtime unavailable:", why)
        return

    # Bind the callback to THIS session so a previous meeting's session (still closing
    # while the next one already records) can't leak its live text into the new one.
    holder: dict = {}

    def on_delta(text: str, final: bool) -> None:
        if STATE.realtime is not holder.get("session"):
            return
        # Each completed utterance is a VAD turn (bounded by silence) — render it as
        # its own bulleted line so turn/speaker changes are visible (pause-based, not
        # a guaranteed speaker change).
        if final:
            STATE.live_final += "• " + text.strip() + "\n"
            STATE.live_partial = ""
        else:
            STATE.live_partial += text
        tail = ("• " + STATE.live_partial) if STATE.live_partial else ""
        asyncio.create_task(broadcast_transcript(STATE.live_final + tail))

    # The translate model needs an output language. If the UI Translate dropdown
    # is Off, use the optional configured fallback target.
    target = STATE.translate_to or config.STT_REALTIME_TRANSLATE_TO
    if config.realtime_engine_kind(STATE.realtime_engine) == "translate" and not target:
        print("realtime translate model needs a Translate target or STT_REALTIME_TRANSLATE_TO")
        return
    session = RealtimeSession(on_delta, STATE.realtime_engine, STATE.language, target)
    # Mark this as the current session BEFORE start(), so deltas arriving during
    # startup pass the identity guard above instead of being dropped.
    holder["session"] = session
    STATE.realtime = session
    try:
        await session.start()
        if STATE.recorder is not None:
            STATE.recorder.set_frame_sink(session.feed)
        STATE.live_translated = session.translated
        await broadcast()
    except Exception as exc:
        print("realtime start failed:", exc)
        STATE.realtime = None
        STATE.live_translated = False


async def _stop_realtime() -> None:
    """Tear down the live transcript session (recorder + batch keep running)."""
    session, STATE.realtime = STATE.realtime, None
    STATE.live_translated = False
    if STATE.recorder is not None:
        STATE.recorder.set_frame_sink(None)
    if session is not None:
        try:
            await session.stop()
        except Exception as exc:
            print("realtime stop failed:", exc)


def _job_status_threadsafe(loop, job: "ProcessingJob", detail: str) -> None:
    """Update a job's status from a worker thread (e.g. the transcription progress
    callback) by scheduling the broadcast back on the event loop."""
    def _do():
        job.detail = detail
        asyncio.create_task(broadcast())
    loop.call_soon_threadsafe(_do)


def _classify_health(exc: Exception) -> str:
    m = str(exc)
    if any(s in m for s in ("getaddrinfo", "Failed to resolve", "Read timed out",
                            "Connection aborted", "Max retries", "ConnectionError")):
        return "offline"
    if any(s in m for s in ("GRAPH_CLIENT_ID", "AADSTS", "interaction", "token",
                            "401", "invalid_grant", "sign-in")):
        return "auth"
    return "error"


def _tray_notify(title: str, message: str) -> None:
    try:
        if STATE.tray is not None:
            STATE.tray.notify(message, title)
    except Exception:
        pass


def _event_time() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _log_event(name: str, **fields) -> None:
    data = {"observed_at": _event_time(), **fields}
    body = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    print(
        f"event {name}:",
        body,
    )


def _presence_context() -> dict:
    return {"presence": STATE.last_presence} if STATE.last_presence else {}


def _presence_summary(snapshot) -> dict:
    return {
        "observedAt": snapshot.observed_at,
        "availability": snapshot.availability,
        "activity": snapshot.activity,
        "active": snapshot.active,
    }


async def _suggest_stop_recording(
    source: str,
    reason: str,
    detail: str,
) -> None:
    """Surface uncertain end signals without stopping automatically.

    Local Teams UI/audio heuristics should call this instead of ending the
    recording. The user keeps the final say via Stop or Keep recording.
    """
    if not STATE.active:
        return
    suggestion_id = _event_time()
    STATE.stop_suggestion = {
        "id": suggestion_id,
        "source": source,
        "reason": reason,
        "detail": detail,
        "observedAt": suggestion_id,
    }
    _log_event(
        "stop_suggested",
        source=source,
        reason=reason,
        detail=detail,
        **_presence_context(),
    )
    _show_window()
    _tray_notify("Still recording?", reason)
    await broadcast()


async def _clear_stop_suggestion(action: str) -> None:
    suggestion = STATE.stop_suggestion
    if suggestion is None:
        return
    STATE.stop_suggestion = None
    _log_event(
        "stop_suggestion_resolved",
        action=action,
        source=suggestion.get("source", ""),
        reason=suggestion.get("reason", ""),
    )
    await broadcast()


def _audio_signal_summary(snapshot: dict) -> dict:
    return {
        "silentForSeconds": snapshot.get("silentForSeconds"),
        "thresholdDbfs": snapshot.get("thresholdDbfs"),
        "hasFrames": snapshot.get("hasFrames"),
        "sources": snapshot.get("sources", {}),
    }


async def _check_audio_silence(presence_active: bool) -> None:
    threshold = config.AUDIO_SILENCE_PROMPT_SECONDS
    if threshold <= 0 or not STATE.active or not presence_active:
        return
    if STATE.recorder is None or STATE.stop_suggestion is not None:
        return
    snapshot = STATE.recorder.activity_snapshot()
    silent_for = float(snapshot.get("silentForSeconds") or 0)
    if silent_for < threshold:
        if STATE.audio_silence_prompted:
            STATE.audio_silence_prompted = False
            summary = _audio_signal_summary(snapshot)
            _log_event(
                "audio_activity_resumed",
                **summary,
                **_presence_context(),
            )
        return
    if STATE.audio_silence_prompted:
        return
    STATE.audio_silence_prompted = True
    _log_event(
        "audio_silence_detected",
        **_audio_signal_summary(snapshot),
        **_presence_context(),
    )
    seconds = int(silent_for)
    await _suggest_stop_recording(
        "audio",
        "No meeting audio detected",
        f"Mic and speaker audio have been quiet for {seconds} seconds. "
        "Graph still reports an active call, so please confirm whether "
        "to stop.",
    )


async def _set_health(state: str, detail: str = "") -> None:
    """Update presence health and notify the popup — but only on change, so the
    log isn't spammed and the UI updates once per transition."""
    if STATE.health == state:
        return
    STATE.health = state
    if state != "ok":
        print("presence:", state, "-", detail[:200])
    if state == "auth":
        # Grab attention: a background browser is easy to miss, so surface it.
        _show_window()
        if not NOTES.enabled:
            _tray_notify("Sign-in required", f"Open {APP_DISPLAY_NAME} and click Sign in.")
    await broadcast()


async def detect_active() -> bool:
    from engine.presence import get_presence_snapshot
    try:
        snapshot = await asyncio.to_thread(get_presence_snapshot)
        STATE.last_presence = _presence_summary(snapshot)
        await _set_health("ok")
        return snapshot.active
    except Exception as exc:  # keep previous state on transient failures
        await _set_health(_classify_health(exc), str(exc))
        return STATE.active


async def _begin_meeting(
    title: str | None = None,
    manual: bool = False,
    trigger: str = PRESENCE_ACTIVE_TRIGGER,
    keep_meeting_mapping: bool = False,
) -> None:
    """Start a recording (auto from presence, or manual from the UI). The title is a
    placeholder during recording; the real Teams meeting subject (if any) replaces it
    at finalize. Manual recordings default to "Recording" since they're often phone
    calls or self-memos with no Teams meeting at all."""
    if title is None:
        selected = _selected_meeting_candidate() if keep_meeting_mapping else None
        title = selected.get("subject") if selected else None
    if title is None:
        title = "Recording" if manual else "Teams-Meeting"
    if STATE.active or STATE.notes_starting:
        return
    if NOTES.managed and not NOTES.ready:
        NOTES.error = t("notes.errors.setupFirst")
        STATE.autostart_suppressed = True
        _show_notes_window("settings", activate=True)
        await broadcast()
        return
    if NOTES.enabled:
        if any(r.status == "Azure-Abschluss" and r.busy for r in NOTES.reviews.values()):
            NOTES.error = t("notes.errors.waitForPreviousAudio")
            await broadcast()
            return
        STATE.notes_starting = True
        try:
            from engine.speech.capture import devices
            selection = await asyncio.to_thread(devices)
            review = await asyncio.to_thread(NOTES.start, title, datetime.datetime.now().isoformat(), selection)
            from engine.notes_calls import schedule_people
            schedule_people(NOTES, review)
        except Exception:
            NOTES.error = t("notes.errors.startFailedManaged") if NOTES.managed else t("notes.errors.startFailedDirect")
            STATE.autostart_suppressed = True
            _show_notes_window(activate=True)
            await broadcast()
            return
        finally:
            STATE.notes_starting = False
    if not keep_meeting_mapping:
        _reset_meeting_mapping()
    STATE.active = True
    STATE.started_at = datetime.datetime.now()
    STATE.title = title
    STATE.phase = "meeting"
    STATE.meeting_detected = False
    STATE.live_final = ""          # reset live transcript only at meeting start,
    STATE.live_partial = ""        # so toggling live off/on mid-meeting appends
    STATE.translate_to = ""        # live translation starts Off each new recording
    STATE.manual_recording = manual
    STATE.autostart_suppressed = False
    STATE.stop_suggestion = None
    STATE.audio_silence_prompted = False
    STATE.last_meeting = None       # a new recording supersedes the previous one's
                                    # Keep the completed transcript available for export
    _log_event(
        "meeting_started",
        title=title,
        manual=manual,
        trigger=trigger,
        **_presence_context(),
    )
    if NOTES.current is None:
        _start_recording()
        _start_background_transcription()
        if STATE.live_on and STATE.recorder is not None:
            await _start_realtime()
    else:
        STATE.live_on = False
    if NOTES.current:
        _show_notes_window(activate=True)
    else:
        _show_window()
    _refresh_tray()
    await broadcast()
    if not keep_meeting_mapping and NOTES.current is None:
        asyncio.create_task(_lookup_meeting_candidates(STATE.started_at))


async def _end_meeting(reason: str = "") -> None:
    """Stop the active recording and kick off finalize off the poll loop (so it
    doesn't stall polling). Keep the window up showing progress; the finalize task
    hides it when done. No-op if nothing is active."""
    if not STATE.active:
        return
    if NOTES.current is not None:
        review, NOTES.current = NOTES.current, None
        if not review.calendar_selected:
            NOTES.set_meeting_title(review, STATE.title)
        STATE.active = False
        STATE.started_at = None
        STATE.manual_recording = False
        STATE.meeting_detected = False
        STATE.detected_at = None
        STATE.stop_suggestion = None
        STATE.audio_silence_prompted = False
        STATE.phase = "idle"
        review.busy, review.status = True, "Azure-Abschluss"
        asyncio.create_task(_finish_notes(review))
        _refresh_tray()
        # Update the existing view; do not resurrect a dismissed window.
        await broadcast()
        return
    if reason:
        _log_event("meeting_ending", reason=reason, **_presence_context())
        print("ending meeting:", reason)
    ended = datetime.datetime.now()
    title, started = STATE.title, STATE.started_at
    selected_candidate = _selected_meeting_candidate()
    skip_calendar = STATE.meeting_mapping_rejected
    recorder, STATE.recorder = STATE.recorder, None
    background_transcription, STATE.background_transcription = STATE.background_transcription, None
    if background_transcription is not None:
        background_transcription.request_stop()
    realtime, STATE.realtime = STATE.realtime, None
    # Capture the per-meeting settings now, so a NEW meeting that starts (and changes
    # the engine/language/translate target) while this one finalizes can't alter it.
    stt_engine, language, translate_to = STATE.stt_engine, STATE.language, STATE.translate_to
    STATE.active = False
    STATE.started_at = None
    STATE.manual_recording = False
    STATE.meeting_detected = False
    STATE.detected_at = None
    STATE.stop_suggestion = None
    STATE.audio_silence_prompted = False
    STATE.phase = "idle"
    _refresh_tray()  # recording stopped — drop the red dot even though processing continues
    job = ProcessingJob(title, started, ended)
    STATE.jobs[job.id] = job
    asyncio.create_task(_finalize_meeting(
        job, title, started, ended, recorder, realtime, stt_engine, language,
        translate_to, selected_candidate, skip_calendar, background_transcription))
    await broadcast()


async def _finish_notes(review) -> None:
    try:
        await NOTES.finish(review)
    except Exception:
        review.error = t("notes.errors.completionFailed")
        review.busy = False
        review.clear_raw()
    await broadcast()


async def _notes_watchdog() -> None:
    while True:
        NOTES.tick(_own_domains())
        if NOTES.current and NOTES.current.session.finished.is_set() and STATE.active:
            STATE.autostart_suppressed = True
            await _end_meeting("Audio-Stream beendet")
        await asyncio.sleep(1)


async def _set_meeting_detected(detected: bool) -> None:
    """Show an on-demand prompt while presence is active but recording is not."""
    if STATE.meeting_detected == detected:
        return
    STATE.meeting_detected = detected
    if detected:
        STATE.detected_at = datetime.datetime.now()
        STATE.title = "Teams-Meeting"
        _show_window()
        asyncio.create_task(_lookup_meeting_candidates(STATE.detected_at))
    elif not STATE.active:
        STATE.title = ""
        _reset_meeting_mapping()
    _refresh_tray()
    await broadcast()


def _over_max_duration() -> bool:
    if not (config.MAX_RECORDING_MINUTES > 0 and STATE.started_at):
        return False
    elapsed = (datetime.datetime.now() - STATE.started_at).total_seconds()
    return elapsed > config.MAX_RECORDING_MINUTES * 60


async def poll_loop() -> None:
    while True:
        try:
            active = await detect_active()
            # Presence has gone idle: clear the post-manual-Stop latch so a genuinely
            # new meeting can auto-start again.
            if not active:
                STATE.autostart_suppressed = False
                await _set_meeting_detected(False)

            if active and not STATE.active:
                # Don't auto-start right after a manual Stop while presence is still
                # stuck "active" (the very case that motivated manual control).
                if not STATE.autostart_suppressed:
                    if STATE.auto_start:
                        await _begin_meeting()
                    else:
                        await _set_meeting_detected(True)
            elif STATE.active:
                if _over_max_duration():
                    # Safety net: presence stuck "in a meeting" (e.g. calendar-derived
                    # after a call ended, or never dropped between back-to-back calls).
                    minutes = config.MAX_RECORDING_MINUTES
                    reason = f"max duration {minutes} min reached"
                    await _end_meeting(
                        reason
                    )
                elif not active and not STATE.manual_recording:
                    await _end_meeting("presence idle")
                else:
                    await _check_audio_silence(active)
        except Exception as exc:
            print("poll error:", exc)
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)


def _start_recording() -> None:
    """Begin capturing meeting audio (best-effort; never blocks meeting start)."""
    STATE.recorder = _open_recorder(STATE.title, STATE.started_at)


def _start_background_transcription() -> None:
    if not (config.BACKGROUND_TRANSCRIBE and STATE.recorder is not None):
        return
    if config.STT_BACKEND == "none":
        return
    session = BackgroundTranscriptionSession(
        STATE.recorder,
        STATE.stt_engine,
        STATE.language,
    )
    STATE.background_transcription = session
    session.start()


def _open_recorder(title: str, started_at: datetime.datetime):
    if not config.RECORD_AUDIO:
        return None
    try:
        from engine.audio import MeetingRecorder
        rec = MeetingRecorder(title, started_at)
        if rec.start():
            return rec
        else:
            print("audio capture unavailable:", rec.error)
    except Exception as exc:
        print("audio capture failed to start:", exc)
    return None


def _segments_have_speakers(segments: list[dict] | None) -> bool:
    return bool(
        segments and any(segment.get("speaker") for segment in segments)
    )


def _segments_text(segments: list[dict] | None) -> str:
    if not segments:
        return ""
    return "\n".join(
        (segment.get("text") or "").strip()
        for segment in segments
        if (segment.get("text") or "").strip()
    )


def _merge_note(*parts: str | None) -> str | None:
    merged = "; ".join(part for part in parts if part)
    return merged or None


async def _cleanup_unspeakered_transcript(
    job: "ProcessingJob",
    text: str | None,
    segments: list[dict] | None,
    note: str | None,
    attendees: list[dict] | None,
) -> tuple[str | None, list[dict] | None, str | None]:
    if not config.CLEAN_TRANSCRIPT or _segments_have_speakers(segments):
        return text, segments, note
    cleanup_input = text or _segments_text(segments)
    if not cleanup_input:
        return text, segments, note
    await _job_status(job, "Cleaning up transcript…")
    from engine.summarize import clean_transcript
    try:
        cleaned, cleanup_note = await asyncio.to_thread(
            clean_transcript, cleanup_input, attendees
        )
    except Exception as exc:
        print("transcript cleanup failed:", exc)
        return text, segments, note
    if cleanup_note:
        print("transcript cleanup:", cleanup_note)
    if cleaned:
        return cleaned, None, _merge_note(note, cleanup_note)
    return text, segments, _merge_note(note, cleanup_note)


async def _finalize_meeting(
    job: "ProcessingJob",
    title: str,
    started_at: datetime.datetime,
    ended_at: datetime.datetime,
    recorder=None,
    realtime=None,
    stt_engine: str = "",
    language: str = "",
    translate_to: str = "",
    meeting_candidate: dict | None = None,
    skip_calendar_mapping: bool = False,
    background_transcription: BackgroundTranscriptionSession | None = None,
) -> None:
    """Stop recording, look up attendees, transcribe and summarize, then write the
    transcript — pushing status to this job's line at each step. Runs as its own task,
    concurrently with any newer meeting's recording; any failure degrades to a
    transcript with an explanatory note. Settings (engine/language/translate target)
    are passed in as captured at stop time, never read live off STATE."""
    loop = asyncio.get_running_loop()
    audio_path: str | None = None
    if recorder is not None:
        try:
            audio_path = await asyncio.to_thread(recorder.stop)
            print("Wrote recording:", audio_path) if audio_path else None
        except Exception as exc:
            print("audio capture failed to finalize:", exc)

    # Close the realtime session (the live text was the preview; the final saved
    # transcript comes from the Teams transcript or batch STT below).
    if realtime is not None:
        try:
            await realtime.stop()
        except Exception as exc:
            print("realtime stop failed:", exc)

    attendees: list[dict] | None = None
    note: str | None = None
    attendees_source = "Teams attendance report"
    meeting_id: str | None = None

    if config.FETCH_ATTENDEES:
        await _job_status(job, "Looking up attendees…")
        from engine.attendance import collect_attendees
        try:
            result = await asyncio.to_thread(
                collect_attendees,
                started_at,
                ended_at,
                meeting_candidate,
                skip_calendar_mapping,
            )
            attendees, note, meeting_id = result.attendees, result.note, result.meeting_id
            attendees_source = result.source
            # Upgrade the generic placeholder to the real Teams meeting subject when a
            # calendar event matched. No match (phone call / self-memo / ad-hoc) keeps
            # the placeholder. Drives the .md filename + heading and the OneNote page.
            if result.subject:
                title = result.subject
                job.title = title  # reflect the real name on the processing line too
        except Exception as exc:  # never let attendee lookup lose the transcript
            note = f"attendee lookup failed: {exc}"
    else:
        note = "attendee lookup disabled (FETCH_ATTENDEES=false)"

    # Transcript: keep the local STT and Teams versions when both are available.
    transcript_text: str | None = None
    transcript_note: str | None = None
    transcript_segments: list[dict] | None = None
    transcript_source: str | None = None
    transcript_versions: list[dict] = []
    teams_segments: list[dict] | None = None
    teams_note: str | None = None
    local_text: str | None = None
    local_note: str | None = None
    local_segments: list[dict] | None = None
    local_source: str | None = None

    if config.USE_TEAMS_TRANSCRIPT and meeting_id:
        await _job_status(job, "Fetching Teams transcript…")
        from engine.teams_transcript import fetch_transcript_segments
        try:
            teams_segments, teams_note = await asyncio.to_thread(
                fetch_transcript_segments,
                meeting_id,
                started_at,
                ended_at,
            )
        except Exception as exc:
            teams_segments = None
            teams_note = f"Teams transcript failed: {exc}"
        if not teams_segments:
            print("Teams transcript unavailable:", teams_note)

    if audio_path:
        stt = None
        background_note: str | None = None
        if background_transcription is not None:
            await _job_status(job, "Finalizing background transcription…")
            try:
                stt, background_note = await background_transcription.finish(job)
            except Exception as exc:
                background_note = f"background transcription failed: {exc}"
            if background_note:
                print("background transcription:", background_note)

        from engine.transcribe import transcribe_wav

        def on_progress(done: int, total: int) -> None:
            _job_status_threadsafe(
                loop, job, f"Transcribing chunk {done}/{total}…"
            )

        try:
            if stt is None:
                await _job_status(job, "Transcribing…")
                providers = config.stt_providers_for(stt_engine)
                stt = await asyncio.to_thread(
                    transcribe_wav,
                    audio_path,
                    None,
                    language,
                    None,
                    on_progress,
                    providers,
                )
            local_text = stt.text or None
            local_segments = stt.segments or None
            local_note = _merge_note(stt.error or stt.note, background_note)
            mode = "background" if background_transcription and not background_note else "full recording"
            local_source = f"Local speech-to-text ({stt_engine}, {mode})"
        except Exception as exc:  # never let STT lose the transcript file
            local_note = f"transcription failed: {exc}"
    else:
        local_note = "no audio recorded"

    cleanup = await _cleanup_unspeakered_transcript(
        job,
        local_text,
        local_segments,
        local_note,
        attendees,
    )
    local_text, local_segments, local_note = cleanup

    if local_segments or local_text:
        transcript_text = local_text
        transcript_segments = local_segments
        transcript_note = local_note
        transcript_source = local_source
        if teams_segments:
            transcript_versions.append({
                "title": TEAMS_TRANSCRIPT_SOURCE,
                "segments": teams_segments,
                "note": teams_note,
                "source": TEAMS_TRANSCRIPT_SOURCE,
            })
    elif teams_segments:
        transcript_segments = teams_segments
        transcript_note = teams_note
        transcript_source = TEAMS_TRANSCRIPT_SOURCE
    else:
        transcript_note = local_note or teams_note or "no transcript available"

    # Summary (optional post-step) via a deployed chat model.
    summary: str | None = None
    summary_note: str | None = None
    local_labels = False
    if config.SUMMARIZE:
        from engine.meeting import is_diarize_label, seg_label
        summary_input: str | None = None
        if transcript_segments:
            # Only diarization labels (A/B) are chunk-local and need part markers.
            diarized = any(is_diarize_label(s.get("speaker")) for s in transcript_segments)
            local_labels = diarized and len({s.get("chunk", 0) for s in transcript_segments}) > 1
            lines, cur = [], None
            for s in transcript_segments:
                if local_labels:
                    ch = s.get("chunk", 0)
                    if ch != cur:
                        lines.append(f"--- part {ch + 1} ---")
                        cur = ch
                lbl = seg_label(s.get("speaker"))
                lines.append(f"{lbl}: {s['text']}" if lbl else s["text"])
            summary_input = "\n".join(lines)
        else:
            summary_input = transcript_text
        if summary_input:
            await _job_status(job, "Summarizing…")
            from engine.summarize import summarize
            try:
                res = await asyncio.to_thread(summarize, summary_input, attendees, None, local_labels)
                summary, summary_note = res.text or None, res.error
            except Exception as exc:
                summary_note = f"summary failed: {exc}"
        else:
            summary_note = "no transcript to summarize"

    # Translate the saved transcript into the chosen target (same dropdown as the
    # live view). Plain-text path only; summary above stays in SUMMARY_LANGUAGE.
    if translate_to and transcript_text and transcript_segments is None:
        await _job_status(job, f"Translating transcript → {translate_to}…")
        from engine.summarize import transform
        instr = (f"Translate the following meeting transcript into {translate_to}. "
                 "Keep any 'Name:' speaker prefixes and the <unintelligible> placeholders. "
                 "One line per utterance. Output only the translation.")
        try:
            out, terr = await asyncio.to_thread(transform, transcript_text, instr)
            if out and not terr:
                transcript_text = out
                transcript_source = (transcript_source or "speech-to-text") + f" · translated → {translate_to}"
        except Exception as exc:
            print("transcript translate failed:", exc)

    path = await asyncio.to_thread(
        write_transcript, title, started_at, ended_at, attendees, note, attendees_source,
        meeting_id, audio_path, transcript_text, transcript_note, transcript_segments,
        transcript_source, {
            "transcript_versions": transcript_versions,
            "summary": summary,
            "summary_note": summary_note,
            "meta": _meeting_meta(),
        },
    )
    print("Wrote transcript:", path)

    # Remember this transcript for post-recording export actions.
    STATE.last_meeting = {
        "title": title,
        "startedAt": started_at,
        "summary": summary or "",
        "emails": [a.get("email") for a in (attendees or []) if a.get("email")],
        "path": path,
        "onenoteUrl": None,
        "onenotePayload": {
            "title": title,
            "startedAt": started_at,
            "endedAt": ended_at,
            "attendees": attendees,
            "attendeesNote": note,
            "attendeesSource": attendees_source,
            "summary": summary,
            "summaryNote": summary_note,
            "transcriptText": transcript_text,
            "transcriptNote": transcript_note,
            "transcriptSegments": transcript_segments,
            "transcriptSource": transcript_source,
        },
    }

    if audio_path and (local_text or local_segments):
        await _delete_recording_after_success(audio_path)

    # Briefly show "done" on this job's line, then drop the job. Keep the popup open
    # so the user can explicitly export it after recording ends.
    await _job_status(job, "Done — transcript saved")
    await asyncio.sleep(4)
    STATE.jobs.pop(job.id, None)
    _refresh_tray()
    await broadcast()


def _recording_started_at(path: str) -> datetime.datetime:
    name = os.path.basename(path)
    match = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{4})_", name)
    if match:
        try:
            return datetime.datetime.strptime(
                "".join(match.groups()), "%Y-%m-%d%H%M"
            )
        except ValueError:
            pass
    return datetime.datetime.fromtimestamp(os.path.getmtime(path))


def _recording_title(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    match = re.match(r"\d{4}-\d{2}-\d{2}_\d{4}_(.+)", stem)
    title = match.group(1) if match else stem
    return title.replace("_", " ") or "Recording"


def _recording_duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as wav:
            rate = wav.getframerate() or config.AUDIO_SAMPLE_RATE
            return wav.getnframes() / rate if rate else 0.0
    except Exception:
        return 0.0


async def _retry_transcription_job(
    job: "ProcessingJob",
    audio_path: str,
    title: str,
    started_at: datetime.datetime,
    ended_at: datetime.datetime,
    stt_engine: str,
    language: str,
) -> None:
    loop = asyncio.get_running_loop()
    transcript_text: str | None = None
    transcript_segments: list[dict] | None = None
    transcript_note: str | None = None
    transcript_source: str | None = None
    summary: str | None = None
    summary_note: str | None = None
    try:
        await _job_status(job, "Retrying transcription...")
        from engine.transcribe import transcribe_wav

        def on_progress(done: int, total: int) -> None:
            _job_status_threadsafe(
                loop, job, f"Retrying chunk {done}/{total}..."
            )

        stt = await asyncio.to_thread(
            transcribe_wav,
            audio_path,
            None,
            language,
            None,
            on_progress,
            config.stt_providers_for(stt_engine),
        )
        transcript_text = stt.text or None
        transcript_segments = stt.segments or None
        transcript_note = stt.error or stt.note
        transcript_source = f"Local speech-to-text retry ({stt_engine})"
        if stt.error and not (transcript_text or transcript_segments):
            raise RuntimeError(stt.error)

        cleanup = await _cleanup_unspeakered_transcript(
            job,
            transcript_text,
            transcript_segments,
            transcript_note,
            None,
        )
        transcript_text, transcript_segments, transcript_note = cleanup

        if config.SUMMARIZE:
            summary_input = (
                _segments_text(transcript_segments) or transcript_text
            )
            if summary_input:
                await _job_status(job, "Summarizing retry...")
                from engine.summarize import summarize
                res = await asyncio.to_thread(summarize, summary_input, None)
                summary, summary_note = res.text or None, res.error
            else:
                summary_note = "no transcript to summarize"

        path = await asyncio.to_thread(
            write_transcript,
            title,
            started_at,
            ended_at,
            None,
            "not available for retry",
            "Saved recording retry",
            None,
            audio_path,
            transcript_text,
            transcript_note,
            transcript_segments,
            transcript_source,
            {"summary": summary, "summary_note": summary_note},
        )
        STATE.last_meeting = {
            "title": title,
            "startedAt": started_at,
            "summary": summary or "",
            "emails": [],
            "path": path,
            "onenoteUrl": None,
            "onenotePayload": {
                "title": title,
                "startedAt": started_at,
                "endedAt": ended_at,
                "attendees": None,
                "attendeesNote": "not available for retry",
                "attendeesSource": "Saved recording retry",
                "summary": summary,
                "summaryNote": summary_note,
                "transcriptText": transcript_text,
                "transcriptNote": transcript_note,
                "transcriptSegments": transcript_segments,
                "transcriptSource": transcript_source,
            },
        }
        if transcript_text or transcript_segments:
            await _delete_recording_after_success(audio_path)
        await _job_status(job, "Done - transcript saved")
        await asyncio.sleep(4)
    except Exception as exc:
        await _job_status(job, f"Retry failed: {exc}")
        await asyncio.sleep(8)
    finally:
        STATE.jobs.pop(job.id, None)
        _refresh_tray()
        await broadcast()


@asynccontextmanager
async def lifespan(_: FastAPI):
    STATE.loop = asyncio.get_running_loop()
    task = asyncio.create_task(poll_loop())
    warm_task = asyncio.create_task(_warm_onenote_at_startup())
    # A restart after a crash arrives here with a meeting still to write out.
    rescue_task = asyncio.create_task(_finalize_rescued())
    notes_task = asyncio.create_task(_notes_watchdog())
    yield
    notes_task.cancel()
    NOTES.shutdown()
    task.cancel()
    warm_task.cancel()
    rescue_task.cancel()


api = FastAPI(lifespan=lifespan)

@api.middleware("http")
async def _local_instance_header(request, call_next):
    response = await call_next(request)
    if STATE.local_ui is not None:
        response.headers[INSTANCE_HEADER] = STATE.local_ui.instance
    return response

from engine.notes_api import install as _install_notes_api
_install_notes_api(sys.modules[__name__])

# uvicorn runs at log_level="warning", so nothing shows which requests arrive.
# Set VT_LOG_REQUESTS=1 to trace them: a request that logs "->" but never "<-"
# pins down a call blocking the event loop and stalling everything behind it.
_LOG_REQUESTS = os.getenv("VT_LOG_REQUESTS", "0").lower() in (
    "1", "true", "yes",
)


@api.middleware("http")
async def _log_requests(request, call_next):
    if not _LOG_REQUESTS:
        return await call_next(request)
    path = request.url.path
    started = time.monotonic()
    print(f"[req] -> {request.method} {path}", flush=True)
    try:
        response = await call_next(request)
    except Exception as exc:
        print(f"[req] !! {path} raised {type(exc).__name__}: {exc}",
              flush=True)
        raise
    took = time.monotonic() - started
    print(f"[req] <- {path} {response.status_code} in {took:.2f}s", flush=True)
    return response


@api.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    STATE.clients.add(ws)
    await ws.send_json(state_payload())
    try:
        while True:
            await ws.receive_text()  # keepalive; content ignored
    except WebSocketDisconnect:
        pass
    finally:
        STATE.clients.discard(ws)


async def _set_live_mode(enabled: bool) -> None:
    if NOTES.enabled or NOTES.current:
        STATE.live_on = False
        await broadcast()
        return
    STATE.live_on = enabled
    if STATE.live_on:
        if STATE.active and STATE.recorder is not None and STATE.realtime is None:
            await _start_realtime()
    else:
        await _stop_realtime()
    _save_settings()
    await broadcast()


@api.post("/api/live/{state}")
async def set_live(state: str) -> dict:
    """Toggle the live transcript overlay — works any time, including mid-meeting.
    Does not affect the batch recording/transcript."""
    if state not in ("on", "off"):
        return {"ok": False, "error": "invalid state"}
    await _set_live_mode(state == "on")
    return {"ok": True, "liveOn": STATE.live_on}


@api.post("/api/auto-start/{state}")
async def set_auto_start(state: str) -> dict:
    """Toggle whether detected meetings start recording immediately."""
    if state not in ("on", "off"):
        return {"ok": False, "error": "invalid state"}
    STATE.auto_start = state == "on"
    _save_settings()
    if STATE.auto_start and STATE.meeting_detected and not STATE.active:
        await _begin_meeting(trigger="auto-start enabled")
    else:
        await broadcast()
    return {"ok": True, "autoStart": STATE.auto_start}


@api.post("/api/meeting-candidate/{candidate_id}")
async def select_meeting_candidate(candidate_id: str) -> dict:
    """Choose which calendar event should provide meeting metadata."""
    if candidate_id in ("reject", "none", "adhoc"):
        STATE.selected_meeting_candidate_id = None
        STATE.meeting_mapping_rejected = True
        STATE.meeting_candidate_status = "rejected"
        STATE.meeting_candidate_note = "calendar meeting rejected; treating as ad-hoc"
        if STATE.active or STATE.meeting_detected:
            STATE.title = "Teams-Meeting"
        await broadcast()
        return {"ok": True}
    if not any(c.get("id") == candidate_id for c in STATE.meeting_candidates):
        return {"ok": False, "error": "unknown candidate"}
    STATE.selected_meeting_candidate_id = candidate_id
    STATE.meeting_mapping_rejected = False
    STATE.meeting_candidate_status = "matched"
    STATE.meeting_candidate_note = "calendar meeting selected"
    selected = _selected_meeting_candidate()
    if selected and (STATE.active or STATE.meeting_detected):
        STATE.title = selected.get("subject") or STATE.title
    await broadcast()
    return {"ok": True}


@api.post("/api/translate-to/{lang}")
async def set_translate_to(lang: str) -> dict:
    """Target language for live translation AND the saved batch transcript
    ('off' = none). Driven by the popup's Translate dropdown.

    With the translate live model the target is the model's output language, so a
    running session is restarted to apply it (transcript kept). With the other live
    models the target only drives the saved transcript + client-side live translation,
    so no restart is needed."""
    STATE.translate_to = "" if lang in ("off", "auto", "") else lang
    if (STATE.realtime is not None and STATE.active
            and config.realtime_engine_kind(STATE.realtime_engine) == "translate"):
        await _stop_realtime()
        if STATE.live_on and STATE.recorder is not None:
            await _start_realtime()
    _save_settings()
    await broadcast()
    return {"ok": True, "translateTo": STATE.translate_to}


@api.post("/api/start")
async def manual_start() -> dict:
    """Manually start recording, overriding presence detection. The recording is
    then user-controlled: presence will NOT end it — only a manual Stop (or the
    max-duration backstop) does. For when presence detection misses a meeting."""
    if STATE.active:
        return {"ok": False, "error": "already recording"}
    # Processing the previous meeting no longer blocks a new recording — they run in
    # parallel (the finalize task owns its own captured recorder/settings).
    if STATE.meeting_detected:
        await _begin_meeting(
            manual=False,
            trigger="on-demand start",
            keep_meeting_mapping=True,
        )
    else:
        await _begin_meeting(manual=True, trigger="manual start")
    return {"ok": STATE.active, "error": NOTES.error if not STATE.active else ""}


@api.post("/api/stop")
async def manual_stop() -> dict:
    """Manually stop recording and finalize now, regardless of presence. For when a
    call has ended but presence is stuck 'in a meeting' (e.g. back-to-back meetings)."""
    if not STATE.active:
        return {"ok": False, "error": "not recording"}
    if STATE.stop_suggestion is not None:
        await _clear_stop_suggestion("stop")
    # Presence may still report "active" (the stuck case) — latch off auto-restart
    # until presence next reads idle, so we don't immediately resume recording.
    STATE.autostart_suppressed = True
    await _end_meeting("manual stop")
    return {"ok": True}


@api.post("/api/keep-recording")
async def keep_recording() -> dict:
    """Dismiss an uncertain stop suggestion and continue recording."""
    await _clear_stop_suggestion("keep_recording")
    return {"ok": True}


@api.get("/api/settings")
async def get_settings() -> dict:
    """Schema + current values for the Settings window (secrets never sent)."""
    from engine import settings_schema
    return settings_schema.public_state()


@api.post("/api/settings")
async def save_settings(payload: dict) -> dict:
    """Persist Settings to the per-user .env and apply them live via config.reload().
    Returns which changed keys (if any) still need a restart (HOST/PORT)."""
    from engine import settings_schema
    updates = payload.get("values", {})
    if not isinstance(updates, dict):
        return {"ok": False, "error": "invalid payload"}
    try:
        changed = await asyncio.to_thread(settings_schema.write_env, updates)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    config.reload()
    if any(k.startswith(("GRAPH_", "ONENOTE_")) for k in changed):
        STATE.onenote_notebooks_cache = None
        STATE.onenote_sections_cache.clear()
    # The active model is the top of each list in Settings, so re-sync to the new
    # defaults after a save (reorder in the UI == change the active model).
    STATE.stt_engine = config.default_stt_engine()
    STATE.realtime_engine = config.default_realtime_engine()
    _save_settings()
    await broadcast()
    restart = sorted(set(changed) & config.RESTART_ONLY_KEYS)
    print(f"settings saved: {len(changed)} changed{' (restart: ' + ', '.join(restart) + ')' if restart else ''}")
    return {"ok": True, "changed": changed, "restartNeeded": bool(restart), "restartKeys": restart}


@api.post("/api/settings-window/close")
async def close_settings_window() -> dict:
    """Close the standalone settings dialog from its Save/Cancel buttons."""
    _close_settings_window()
    return {"ok": True}


@api.post("/api/transcript-window/show")
async def show_transcript_window() -> dict:
    """Open the live transcript in its own resizable window."""
    STATE.live_on = True
    if STATE.active and STATE.recorder is not None and STATE.realtime is None:
        await _start_realtime()
    _save_settings()
    _show_transcript_window()
    await broadcast()
    return {"ok": True}


@api.post("/api/transcript-window/close")
async def close_transcript_window() -> dict:
    await _set_live_mode(False)
    _close_transcript_window()
    return {"ok": True}


@api.post("/api/docs-window/close")
async def close_docs_window() -> dict:
    """Close the standalone documentation window from its Close button."""
    _close_docs_window()
    return {"ok": True}


def _docs_index() -> list[dict]:
    try:
        docs = json.loads((DOCS_DIR / "index.json").read_text(encoding="utf-8"))
    except Exception as exc:
        print("could not read documentation index:", exc)
        return []
    return [
        {
            "id": str(item.get("id") or ""),
            "title": str(item.get("title") or ""),
            "summary": str(item.get("summary") or ""),
        }
        for item in docs
        if isinstance(item, dict) and item.get("id") and item.get("title")
    ]


@api.get("/api/docs")
async def docs_index() -> dict:
    docs = _docs_index()
    if not docs:
        return {"ok": False, "error": "documentation unavailable"}
    return {"ok": True, "docs": docs}


@api.get("/api/docs/{doc_id}")
async def docs_page(doc_id: str) -> dict:
    allowed = {item["id"] for item in _docs_index()}
    if doc_id not in allowed:
        return {"ok": False, "error": "document not found"}
    try:
        content = (DOCS_DIR / f"{doc_id}.md").read_text(encoding="utf-8")
    except Exception as exc:
        print("could not read documentation page:", doc_id, exc)
        return {"ok": False, "error": "document unavailable"}
    return {"ok": True, "id": doc_id, "content": content}


@api.post("/api/popup-window/fit")
async def fit_popup_window(payload: dict) -> dict:
    """Resize the fixed popup to the currently visible content height."""
    height = int(payload.get("height") or 0)
    # Window calls reach across to the GUI thread and block until it
    # responds, so they must never run on the event loop: a stalled resize
    # would hold up every other request, including the create call the user
    # is waiting on.
    await asyncio.to_thread(_fit_popup_window, height)
    return {"ok": True}


@api.post("/api/popup-window/on-top")
async def set_popup_on_top(payload: dict) -> dict:
    """Float the idle popup; let export dialogs sit as ordinary windows."""
    on_top = bool(payload.get("onTop", True))
    await asyncio.to_thread(_set_popup_on_top, on_top)
    return {"ok": True}


@api.post("/api/sign-in")
async def sign_in() -> dict:
    """User-initiated Microsoft sign-in (opens the browser in response to a click)."""
    from engine.graph_auth import get_token
    try:
        token = await asyncio.to_thread(get_token, interactive=True)
        if token and config.AI_MODE in {"gateway", "entra"}:
            from engine.ai_auth import cloud_token
            await asyncio.to_thread(cloud_token, interactive=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if token:
        await _set_health("ok")
        return {"ok": True}
    return {"ok": False, "error": "sign-in did not complete"}


@api.post("/api/graph/sharepoint-sign-in")
async def graph_sharepoint_sign_in() -> dict:
    """User-initiated Graph sign-in for SharePoint site notebook discovery."""
    from engine.graph_auth import get_token
    try:
        token = await asyncio.to_thread(
            get_token,
            interactive=True,
            include_onenote=True,
            include_sharepoint_sites=True,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if token:
        return {"ok": True}
    return {"ok": False, "error": "sign-in did not complete"}


@api.get("/api/graph/status")
async def graph_status() -> dict:
    """Whether the cached Microsoft token satisfies the current Graph scopes."""
    configured = bool(config.GRAPH_CLIENT_ID)
    if not configured:
        return {"configured": False, "connected": False, "reason": "Graph Client ID is not configured."}
    from engine.graph_auth import get_token
    try:
        token = await asyncio.to_thread(get_token, interactive=False)
        sharepoint_token = None
        if config.ONENOTE_ENABLED and config.ONENOTE_SITE_PATHS:
            sharepoint_token = await asyncio.to_thread(
                get_token,
                interactive=False,
                include_onenote=True,
                include_sharepoint_sites=True,
            )
    except Exception as exc:
        return {"configured": True, "connected": False, "reason": str(exc)}
    cloud_connected = True
    cloud_reason = ""
    if config.AI_MODE in {"gateway", "entra"}:
        try:
            from engine.ai_auth import cloud_token
            cloud_connected = bool(await asyncio.to_thread(
                cloud_token, interactive=False
            ))
        except Exception as exc:
            cloud_connected = False
            cloud_reason = str(exc)
    return {
        "configured": True,
        "connected": bool(token),
        "aiMode": config.AI_MODE,
        "cloudConnected": cloud_connected,
        "cloudReason": cloud_reason,
        "sharePointSitesConfigured": bool(config.ONENOTE_ENABLED and config.ONENOTE_SITE_PATHS),
        "sharePointConnected": bool(sharepoint_token),
        "reason": (
            "Sign in again to grant the current Microsoft Graph permissions."
            if not token else cloud_reason
        ),
    }


@api.get("/api/usage/summary")
async def usage_summary(period: str = "") -> dict:
    """Current signed-in user's metadata-only managed-service usage."""
    if config.AI_MODE != "gateway" or not config.USAGE_SERVICE_ENDPOINT:
        return {"ok": False, "enabled": False, "error": "Usage reporting is not configured."}
    from engine import usage
    try:
        result = await asyncio.to_thread(usage.summary, period)
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "enabled": True, "error": str(exc)}


_CHOICE_MEMORY = choice_memory.ChoiceMemory(
    paths.data_dir() / "choice-memory.json"
)


def _own_domains() -> frozenset[str]:
    """Mail domains that are ours rather than a customer's.

    Configured domains win; otherwise the signed-in account's own domain, which
    is correct for a normal install and avoids baking one company's domain into
    the product.
    """
    configured = {
        domain.lower().strip().lstrip("@")
        for domain in config.OWN_EMAIL_DOMAINS if domain.strip()
    }
    if configured:
        return frozenset(configured)
    username = graph_auth.signed_in_username()
    if "@" in username:
        return frozenset({username.split("@")[-1].lower().strip()})
    return frozenset()


def _remember_choice(scope: str, transcript_id: str, value: str) -> None:
    """Attach a chosen target to the meeting it was chosen for."""
    if not value:
        return
    record = _select_transcript(transcript_id)
    if not record:
        return
    _CHOICE_MEMORY.remember(scope, _meeting_context(record).keys(), value)


def _meeting_context(record: dict) -> suggest.MeetingContext:
    """The signals this transcript offers, for scoring export targets."""
    meta = record.get("meta") or {}
    attendees = [
        suggest.Attendee(
            name=str(person.get("name") or ""),
            email=str(person.get("email") or ""),
            role=str(person.get("role") or ""),
        )
        for person in (record.get("attendees") or [])
    ]
    if not attendees:
        # Older transcripts have no attendee section; fall back to addresses.
        attendees = [
            suggest.Attendee(email=email)
            for email in (record.get("emails") or [])
        ]
    return suggest.MeetingContext(
        title=str(record.get("title") or ""),
        series_id=str(meta.get("seriesId") or meta.get("iCalUId") or ""),
        attendees=attendees,
        own_domains=_own_domains(),
    )


def _transcript_id(path: str) -> str:
    return os.path.basename(path)


def _parse_transcript(path: str) -> dict:
    title = os.path.splitext(os.path.basename(path))[0]
    started_at = None
    ended_at = None
    summary = ""
    emails: list[str] = []
    attendees: list[dict] = []
    meta: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        first = next((line for line in text.splitlines() if line.strip()), "")
        if first.startswith("# "):
            title = first[2:].strip()
        match = re.search(r"\| \*\*Start\*\* \| ([^|]+) \|", text)
        if match:
            try:
                started_at = datetime.datetime.strptime(
                    match.group(1).strip(), "%Y-%m-%d %H:%M:%S"
                )
            except ValueError:
                started_at = None
        match = re.search(r"\| \*\*End\*\* \| ([^|]+) \|", text)
        if match:
            try:
                ended_at = datetime.datetime.strptime(
                    match.group(1).strip(), "%Y-%m-%d %H:%M:%S"
                )
            except ValueError:
                ended_at = None
        match = re.search(r"## Summary\s+(.+?)(?:\n## |\Z)", text, re.S)
        if match:
            summary = match.group(1).strip()
        # Prefer the recorded attendee list over scanning the whole file: a
        # regex over the transcript body also picks up any address that was
        # merely spoken or quoted, which would then steer the suggestions.
        parsed, _note, _source = _parse_transcript_attendees(text)
        attendees = parsed or []
        emails = sorted({
            person["email"].strip() for person in attendees
            if person.get("email")
        })
        if not emails:
            emails = sorted(set(re.findall(r"[\w.+-]+@[\w.-]+\.\w+", text)))
        meta = _parse_transcript_meta(text)
    except Exception as exc:
        print("could not parse transcript:", path, exc)
    return {
        "id": _transcript_id(path),
        "title": title,
        "startedAt": started_at,
        "endedAt": ended_at,
        "summary": summary,
        "emails": emails,
        "attendees": attendees,
        "meta": meta,
        "path": path,
    }


def _parse_transcript_meta(text: str) -> dict:
    """Read back the hidden identity block; absent on older transcripts."""
    match = re.search(r"<!--\s*vt-meta\s+(\{.*?\})\s*-->", text, re.S)
    if not match:
        return {}
    try:
        value = json.loads(match.group(1))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _list_transcripts() -> list[dict]:
    try:
        paths = [
            os.path.join(config.TRANSCRIPT_DIR, name)
            for name in os.listdir(config.TRANSCRIPT_DIR)
            if name.lower().endswith((".md", ".txt"))
        ]
    except Exception:
        return []
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return [_parse_transcript(path) for path in paths[:50]]


def _list_recordings() -> list[dict]:
    try:
        paths = [
            os.path.join(config.AUDIO_DIR, name)
            for name in os.listdir(config.AUDIO_DIR)
            if name.lower().endswith(".wav") and ".part" not in name.lower()
        ]
    except Exception:
        return []
    paths.sort(key=lambda item: os.path.getmtime(item), reverse=True)
    recordings = []
    for path in paths[:50]:
        started = _recording_started_at(path)
        duration = _recording_duration(path)
        recordings.append({
            "id": os.path.basename(path),
            "title": _recording_title(path),
            "startedAt": started.isoformat(),
            "durationSeconds": round(duration) if duration else None,
            "sizeBytes": os.path.getsize(path),
        })
    return recordings


def _select_transcript(transcript_id: str = "") -> dict | None:
    records = _list_transcripts()
    if transcript_id:
        match = next((r for r in records if r["id"] == transcript_id), None)
        if match:
            return match
    if STATE.last_meeting:
        path = STATE.last_meeting.get("path")
        record = next((r for r in records if r["path"] == path), None)
        if record:
            record.update({
                "summary": STATE.last_meeting.get("summary") or record["summary"],
                "emails": STATE.last_meeting.get("emails") or record["emails"],
                "startedAt": STATE.last_meeting.get("startedAt") or record["startedAt"],
            })
            return record
    return records[0] if records else None


def _onenote_page_title_for_transcript(record: dict) -> str:
    started = record.get("startedAt")
    if started:
        return f"{started:%Y-%m-%d %H:%M} · {record['title']}"
    return record["title"]


def _markdown_section(text: str, heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\s+(.+?)(?=^## |\Z)"
    match = re.search(pattern, text, re.S | re.M)
    return match.group(1).strip() if match else ""


def _read_transcript_text(record: dict) -> tuple[str, str | None]:
    try:
        with open(record["path"], "r", encoding="utf-8") as f:
            return f.read(), None
    except Exception as exc:
        return "", f"could not read transcript: {exc}"


def _parse_transcript_attendees(text: str) -> tuple[list[dict] | None, str | None, str]:
    section = _markdown_section(text, "Attendees")
    if not section:
        return None, "not available in saved transcript", "Saved transcript"
    source_match = re.search(r"\*Source:\s*(.+?)\.\*", section)
    source = source_match.group(1).strip() if source_match else "Saved transcript"
    attendees = []
    for line in section.splitlines():
        match = re.match(r"- \*\*(.+?)\*\*(?: <([^>]+)>)?(?: — (.+))?", line.strip())
        if match:
            attendees.append({
                "name": match.group(1),
                "email": match.group(2) or "",
                "role": match.group(3) or "",
                "seconds": 0,
            })
    if attendees:
        return attendees, None, source
    note = section.replace("_", "").strip() or "no attendees available"
    return None, note, source


def _onenote_payload_for_transcript(record: dict) -> dict | None:
    if STATE.last_meeting and STATE.last_meeting.get("path") == record.get("path"):
        payload = STATE.last_meeting.get("onenotePayload")
        if payload:
            return dict(payload)
    try:
        with open(record["path"], "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as exc:
        print("could not read transcript for OneNote:", record.get("path"), exc)
        return None
    started = record.get("startedAt")
    if not started:
        started = datetime.datetime.fromtimestamp(os.path.getmtime(record["path"]))
    ended = record.get("endedAt") or started
    attendees, attendees_note, attendees_source = _parse_transcript_attendees(text)
    transcript_text = _markdown_section(text, "Transcript") or text
    return {
        "title": record["title"],
        "startedAt": started,
        "endedAt": ended,
        "attendees": attendees,
        "attendeesNote": attendees_note,
        "attendeesSource": attendees_source,
        "summary": record.get("summary") or None,
        "summaryNote": None,
        "transcriptText": transcript_text,
        "transcriptNote": None,
        "transcriptSegments": None,
        "transcriptSource": "Saved transcript",
    }




ONENOTE_SCOPE = "onenote.target"


def _onenote_section_scope(notebook_id: str) -> str:
    """Sections live inside a notebook, so remember them per notebook."""
    return f"onenote.section:{notebook_id}"


def _suggest_onenote_notebook(
    record: dict,
    notebooks: list[dict],
) -> tuple[str, str]:
    """Which notebook this meeting's page belongs in, and why.

    Notebooks are named after teams and customers far more often than they are
    named after email domains, so a domain resemblance is a real signal here —
    but still ranked under what the user chose for this meeting before.
    """
    context = _meeting_context(record)
    allowed = {str(book.get("id") or "") for book in notebooks} - {""}
    configured = next(
        (
            book["id"] for book in notebooks
            if str(book.get("name") or "").casefold()
            == config.ONENOTE_NOTEBOOK.casefold()
        ),
        "",
    )
    candidates = [
        suggest.learned(_CHOICE_MEMORY.recall(ONENOTE_SCOPE, context.keys())),
        *suggest.by_domain(context, notebooks),
        suggest.configured(configured, "ONENOTE_NOTEBOOK"),
        suggest.last_choice(str(
            STATE.onenote_choices.get("lastNotebookId") or ""
        )),
    ]
    winner = suggest.best(candidates, allowed=allowed)
    if winner:
        return winner.value, winner.reason
    return (notebooks[0]["id"] if notebooks else ""), ""


def _suggest_onenote_section(
    record: dict,
    notebook_id: str,
    sections: list[dict],
) -> dict:
    """Section within the chosen notebook, as {name, reason}.

    Returned separately from the notebook because the frontend loads sections
    after a notebook is picked, and re-picks one on every notebook change.
    """
    if not notebook_id or not sections:
        return {"name": "", "reason": ""}
    context = _meeting_context(record)
    by_name = {str(s.get("name") or ""): s for s in sections}
    allowed = set(by_name) - {""}
    sections_by_book = STATE.onenote_choices.get("sections") or {}
    saved = sections_by_book.get(notebook_id) or {}
    candidates = [
        suggest.learned(
            _CHOICE_MEMORY.recall(
                _onenote_section_scope(notebook_id), context.keys()
            )
        ),
        suggest.configured(
            next(
                (
                    name for name in allowed
                    if name.casefold() == config.ONENOTE_SECTION.casefold()
                ),
                "",
            ),
            "ONENOTE_SECTION",
        ),
        suggest.last_choice(str(saved.get("sectionName") or "")),
    ]
    winner = suggest.best(candidates, allowed=allowed)
    if winner:
        return {"name": winner.value, "reason": winner.reason}
    return {"name": "", "reason": ""}


@api.post("/api/language/{lang}")
async def set_language(lang: str) -> dict:
    """Set the transcription language ('auto' = detect, else an ISO code like 'de').
    Disables auto-detect for live when set. Applied immediately to a running live
    session (restarted, transcript kept) and to the next batch pass."""
    STATE.language = "" if lang == "auto" else lang
    if STATE.realtime is not None and STATE.active:
        await _stop_realtime()
        if STATE.live_on and STATE.recorder is not None:
            await _start_realtime()
    _save_settings()
    await broadcast()
    return {"ok": True, "language": STATE.language}


@api.post("/api/transform")
async def transform_text(payload: dict) -> dict:
    """Translate or correct the supplied text via the chat model (popup buttons)."""
    text = payload.get("text", "")
    mode = payload.get("mode")
    lang = payload.get("lang", "")
    if mode == "translate":
        instr = (f"Translate the following meeting transcript into {lang}. Preserve any "
                 "speaker labels and timestamps. Output only the translation, no preamble.")
    elif mode == "correct":
        instr = ("The following is a raw speech-to-text transcript with likely recognition "
                 "errors. Fix obvious errors, punctuation and casing; keep the original "
                 "language and meaning; do not add or remove content. Output only the "
                 "corrected transcript.")
    else:
        return {"ok": False, "error": "invalid mode"}
    from engine.summarize import transform
    out, err = await asyncio.to_thread(transform, text, instr)
    return {"ok": err is None, "text": out, "error": err}


@api.get("/api/retry-transcription/recordings")
async def retry_transcription_recordings() -> dict:
    return {"ok": True, "recordings": _list_recordings()}


@api.post("/api/retry-transcription/start")
async def retry_transcription_start(payload: dict) -> dict:
    recording_id = os.path.basename(payload.get("recordingId", ""))
    if not recording_id.lower().endswith(".wav"):
        return {"ok": False, "error": "Choose a WAV recording"}
    audio_path = os.path.join(config.AUDIO_DIR, recording_id)
    if not os.path.isfile(audio_path):
        return {"ok": False, "error": "Recording not found"}
    started_at = _recording_started_at(audio_path)
    duration = _recording_duration(audio_path)
    ended_at = started_at + datetime.timedelta(seconds=duration or 0)
    title = _recording_title(audio_path)
    job = ProcessingJob(f"Retry: {title}", started_at, ended_at)
    job.detail = "Queued retry transcription..."
    STATE.jobs[job.id] = job
    asyncio.create_task(_retry_transcription_job(
        job,
        audio_path,
        title,
        started_at,
        ended_at,
        STATE.stt_engine,
        STATE.language,
    ))
    await broadcast()
    return {"ok": True, "jobId": job.id}


_ONENOTE_SCAN_LOCK = asyncio.Lock()


async def _load_onenote_notebooks(refresh: bool = False) -> tuple[list[dict], str | None]:
    from engine import onenote
    cache_key = tuple(config.ONENOTE_SITE_PATHS)
    cache = STATE.onenote_notebooks_cache or {}
    if not refresh and cache.get("key") == cache_key:
        return cache.get("notebooks", []), cache.get("error")
    # Single-flight. A bare tenant host makes this a full SharePoint walk, and
    # the startup warm-up was still running when the dialog asked for the same
    # thing, so both scanned every site at once. Waiters re-check the cache
    # after the holder finishes instead of repeating the walk.
    async with _ONENOTE_SCAN_LOCK:
        if not refresh:
            # Whoever held the lock may have just filled the cache we wanted.
            cache = STATE.onenote_notebooks_cache or {}
            if cache.get("key") == cache_key:
                return cache.get("notebooks", []), cache.get("error")
        # `refresh` only bypasses the cache above; it must not become the
        # `interactive` argument, or hitting Refresh starts a blocking sign-in
        # inside the request the dialog is waiting on. Listing stays silent and
        # reports "sign-in required" instead.
        notebooks, err = await asyncio.to_thread(
            onenote.list_notebooks, False
        )
        STATE.onenote_notebooks_cache = {
            "key": cache_key,
            "notebooks": notebooks,
            "error": err,
        }
        return notebooks, err


async def _load_onenote_sections(
    notebook_id: str,
    sections_url: str = "",
    refresh: bool = False,
) -> tuple[list[dict], str | None]:
    from engine import onenote
    cache_key = f"{notebook_id}\n{sections_url or ''}"
    cache = STATE.onenote_sections_cache.get(cache_key) or {}
    if not refresh and cache:
        return cache.get("sections", []), cache.get("error")
    # Same as above: `refresh` is a cache flag, not the `interactive` argument.
    sections, err = await asyncio.to_thread(
        onenote.list_sections,
        notebook_id,
        sections_url or None,
        False,
    )
    STATE.onenote_sections_cache[cache_key] = {
        "sections": sections,
        "error": err,
    }
    return sections, err


async def _warm_onenote_cache() -> None:
    if not config.ONENOTE_ENABLED:
        return
    notebooks, err = await _load_onenote_notebooks(refresh=True)
    if err and not notebooks:
        print("OneNote cache warm-up skipped:", err)
        return
    selected = next(
        (item for item in notebooks if item.get("id") == STATE.onenote_choices.get("lastNotebookId")),
        None,
    ) or next(
        (item for item in notebooks if item.get("name", "").casefold() == config.ONENOTE_NOTEBOOK.casefold()),
        None,
    ) or (notebooks[0] if notebooks else None)
    if not selected:
        return
    _, section_err = await _load_onenote_sections(
        selected.get("id", ""),
        selected.get("sectionsUrl") or "",
        refresh=True,
    )
    if section_err:
        print("OneNote section cache warm-up failed:", section_err)


async def _warm_onenote_at_startup() -> None:
    """Warm OneNote without making a cloud failure prevent startup."""
    try:
        await _warm_onenote_cache()
    except Exception as exc:
        print("OneNote cache warm-up skipped:", exc)


@api.get("/api/onenote/targets")
async def onenote_targets(transcript: str = "", refresh: bool = False) -> dict:
    if not config.ONENOTE_ENABLED:
        return {"ok": False, "error": "OneNote is disabled"}
    records = _list_transcripts()
    selected = _select_transcript(transcript)
    if not selected:
        return {"ok": False, "error": "no transcript to save to OneNote"}
    notebooks, err = await _load_onenote_notebooks(refresh)
    if err and not notebooks:
        return {"ok": False, "error": err}
    notebook_id, notebook_reason = _suggest_onenote_notebook(
        selected, notebooks,
    )
    selected_notebook = next(
        (book for book in notebooks if book.get("id") == notebook_id),
        None,
    ) or (notebooks[0] if notebooks else None)
    selected_sections: list[dict] = []
    section_warning = None
    if selected_notebook:
        selected_sections, section_warning = await _load_onenote_sections(
            selected_notebook.get("id", ""),
            selected_notebook.get("sectionsUrl") or "",
            refresh,
        )
    options = [{
        "id": record["id"],
        "title": record["title"],
        "startedAt": record["startedAt"].isoformat()
        if record.get("startedAt") else None,
    } for record in records]
    uploaded_url = None
    if STATE.last_meeting and STATE.last_meeting.get("path") == selected.get("path"):
        uploaded_url = STATE.last_meeting.get("onenoteUrl")
    return {
        "ok": True,
        "defaultNotebook": config.ONENOTE_NOTEBOOK,
        "defaultSection": config.ONENOTE_SECTION,
        "defaultNotebookId": notebook_id,
        "notebookReason": notebook_reason,
        "sectionSuggestion": _suggest_onenote_section(
            selected, (selected_notebook or {}).get("id", ""),
            selected_sections,
        ),
        "transcriptTitle": selected["title"],
        "selectedTranscriptId": selected["id"],
        "pageTitle": _onenote_page_title_for_transcript(selected),
        "uploadedUrl": uploaded_url,
        "transcripts": options,
        "notebooks": notebooks,
        "warning": err,
        "lastNotebookId": STATE.onenote_choices.get("lastNotebookId", ""),
        "selectedNotebookId": (selected_notebook or {}).get("id", ""),
        "selectedNotebookSections": selected_sections,
        "selectedNotebookSectionWarning": section_warning,
        "sectionChoices": STATE.onenote_choices.get("sections", {}),
    }


@api.get("/api/onenote/sections")
async def onenote_sections(
    notebookId: str = "",
    sectionsUrl: str = "",
    refresh: bool = False,
) -> dict:
    if not config.ONENOTE_ENABLED:
        return {"ok": False, "error": "OneNote is disabled"}
    if not notebookId:
        return {"ok": False, "error": "notebookId is required"}
    sections, err = await _load_onenote_sections(notebookId, sectionsUrl, refresh)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "sections": sections}


@api.post("/api/onenote/choice")
async def onenote_choice(payload: dict) -> dict:
    if not config.ONENOTE_ENABLED:
        return {"ok": False, "error": "OneNote is disabled"}
    notebook_id = (payload.get("notebookId") or "").strip()
    if not notebook_id:
        return {"ok": False, "error": "notebookId is required"}
    sections = STATE.onenote_choices.setdefault("sections", {})
    STATE.onenote_choices["lastNotebookId"] = notebook_id
    current = sections.get(notebook_id, {}) if isinstance(sections, dict) else {}
    sections[notebook_id] = {
        **current,
        "notebookName": payload.get("notebookName", ""),
        "sectionsUrl": payload.get("sectionsUrl", ""),
        "sectionId": payload.get("sectionId", current.get("sectionId", "")),
        "sectionName": payload.get("sectionName", current.get("sectionName", "")),
    }
    _save_settings()
    # Tie notebook and section to this meeting, so a recurring one lands in the
    # same place next time without the user re-picking it.
    transcript_id = str(payload.get("transcriptId") or "")
    _remember_choice(ONENOTE_SCOPE, transcript_id, notebook_id)
    section_name = str(payload.get("sectionName") or "")
    if section_name:
        _remember_choice(
            _onenote_section_scope(notebook_id), transcript_id, section_name,
        )
    return {"ok": True}


@api.post("/api/onenote/upload")
async def onenote_upload(payload: dict) -> dict:
    action = payload.get("action", "")
    if not config.ONENOTE_ENABLED:
        return {"ok": False, "error": "OneNote is disabled"}
    selected_record = _select_transcript(payload.get("transcriptId", ""))
    if not selected_record:
        return {"ok": False, "error": "No finished transcript is ready for OneNote"}
    data = _onenote_payload_for_transcript(selected_record)
    if not data:
        return {"ok": False, "error": "Could not read the selected transcript"}
    if action != "custom":
        return {"ok": False, "error": "invalid action"}
    target = None
    selected = payload.get("target") or {}
    if not selected.get("notebookId"):
        return {"ok": False, "error": "Choose a OneNote notebook"}
    if not (selected.get("sectionId") or selected.get("sectionName")):
        return {"ok": False, "error": "Choose or name a OneNote section"}
    target = {
        "notebookId": selected.get("notebookId", ""),
        "notebookName": selected.get("notebookName", ""),
        "sectionsUrl": selected.get("sectionsUrl", ""),
        "sectionId": selected.get("sectionId", ""),
        "sectionName": selected.get("sectionName", ""),
    }
    from engine.onenote import save_to_onenote
    page_title = (payload.get("pageTitle") or "").strip()
    if not page_title:
        page_title = _onenote_page_title_for_transcript(selected_record)
    page_url, onenote_note = await asyncio.to_thread(
        save_to_onenote,
        data["title"], data["startedAt"], data["endedAt"],
        data["attendees"], data["attendeesNote"], data["attendeesSource"],
        data["summary"], data["summaryNote"],
        data["transcriptText"], data["transcriptNote"],
        data["transcriptSegments"], data["transcriptSource"],
        target, page_title,
    )
    if not page_url:
        return {"ok": False, "error": onenote_note or "OneNote upload failed"}
    if STATE.last_meeting and STATE.last_meeting.get("path") == selected_record.get("path"):
        STATE.last_meeting["onenoteUrl"] = page_url
    STATE.onenote_sections_cache.clear()
    await broadcast()
    return {"ok": True, "url": page_url}


@api.post("/api/open-transcripts")
async def open_transcripts() -> dict:
    """Open the transcripts folder in the OS file explorer (local desktop app)."""
    return await open_location("folder")


@api.post("/api/open/{target}")
async def open_location(target: str) -> dict:
    """Navigation helper for the popup's 'Open…' combobox. Opens one of the transcript
    destinations: the local folder or the OneNote notebook."""
    try:
        if target == "folder":
            os.makedirs(config.TRANSCRIPT_DIR, exist_ok=True)
            os.startfile(config.TRANSCRIPT_DIR)  # Windows: opens Explorer
            return {"ok": True}
        if target == "onenote":
            from engine import onenote
            url = await asyncio.to_thread(onenote.notebook_web_url)
            if not url:
                return {"ok": False, "error": "OneNote notebook not found (sign in / record once first)"}
            webbrowser.open(url)
            return {"ok": True}
        if target == "last-onenote":
            url = (STATE.last_meeting or {}).get("onenoteUrl")
            if not url:
                from engine import onenote
                url = await asyncio.to_thread(onenote.notebook_web_url)
            if not url:
                return {"ok": False, "error": "OneNote page not available"}
            webbrowser.open(url)
            return {"ok": True}
        return {"ok": False, "error": f"unknown target '{target}'"}
    except Exception as exc:
        print(f"open '{target}' failed:", exc)
        return {"ok": False, "error": str(exc)}


# Serve the built Vite UI. Mounted AFTER /ws so the WebSocket route wins.
if FRONTEND_DIST.joinpath("index.html").is_file():
    api.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="ui")
else:
    @api.get("/")
    async def _needs_build() -> HTMLResponse:
        return HTMLResponse(
            "<body style='font-family:sans-serif;background:#1f1f1f;color:#eee;padding:2rem'>"
            "<h2>UI not built yet</h2>"
            "<p>Run <code>npm install &amp;&amp; npm run build</code> in "
            "<code>frontend/</code>, then restart <code>app.py</code>.</p></body>"
        )


# --------------------------------------------------------------------------- #
# Window helpers (safe to call from the poll loop thread)
# --------------------------------------------------------------------------- #
def _ui_url(query: str = "") -> str:
    base = STATE.local_ui.url if STATE.local_ui else f"http://{config.HOST}:{config.PORT}/"
    return base + query


def _show_window() -> None:
    if NOTES.enabled or (NOTES.managed and not NOTES.ready):
        _hide_window()
        if not STATE.notes_window:
            _show_notes_window(activate=False)
        return
    if STATE.window:
        STATE.popup_visible = True
        STATE.window.show()
        _apply_popup_size()
        # Re-assert the float state: hiding and re-showing must not resurrect
        # always-on-top while an export dialog is still open.
        _set_popup_on_top(STATE.popup_on_top, force=True)
        # Un-minimize / bring to front so it actually pops when recording starts.
        try:
            STATE.window.restore()
        except Exception:
            pass


def _hide_window() -> None:
    if STATE.window:
        STATE.popup_visible = False
        STATE.window.hide()


_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
# "Posts the request to the thread that owns the window... prevents the calling
# thread from blocking its execution while other threads process the request."
_SWP_ASYNCWINDOWPOS = 0x4000


def _popup_hwnd() -> int:
    """Window handle of the popup, resolved once and remembered."""
    if STATE.popup_hwnd:
        return STATE.popup_hwnd
    if not STATE.window:
        return 0
    try:
        from webview.platforms.winforms import BrowserView
        form = BrowserView.instances.get(STATE.window.uid)
        STATE.popup_hwnd = int(form.Handle.ToInt32()) if form else 0
    except Exception as exc:
        print("popup handle lookup failed:", exc)
        STATE.popup_hwnd = 0
    return STATE.popup_hwnd


def _set_window_topmost(hwnd: int, on_top: bool) -> None:
    ctypes.windll.user32.SetWindowPos(
        hwnd,
        _HWND_TOPMOST if on_top else _HWND_NOTOPMOST,
        0, 0, 0, 0,
        _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE | _SWP_ASYNCWINDOWPOS,
    )


def _set_popup_on_top(on_top: bool, force: bool = False) -> None:
    # Repeat requests are dropped so a chatty client cannot keep reaching
    # across to the GUI thread; `force` is for re-asserting after show(),
    # where the native flag may no longer match what we last set.
    changed = STATE.popup_on_top != on_top
    STATE.popup_on_top = on_top
    if not STATE.window or not (changed or force):
        return
    # pywebview would assign the WinForms TopMost property, which from any
    # thread but the GUI one blocks until that thread pumps the message. A
    # dialog opening triggers the auto-fit resize at the same moment, so two
    # cross-thread window calls raced and wedged each other, freezing the app.
    # Posting the Z-order change instead means nothing here ever waits.
    hwnd = _popup_hwnd()
    if not hwnd:
        return
    try:
        _set_window_topmost(hwnd, on_top)
    except Exception as exc:
        print("popup on_top toggle failed:", exc)


def _fit_popup_window(content_height: int) -> None:
    if content_height <= 0:
        return
    STATE.popup_content_height = content_height
    if not STATE.popup_visible:
        return
    _apply_popup_size()


def _apply_popup_size() -> None:
    if not STATE.window:
        return
    # pywebview resize uses the outer window, so add room for the native title bar
    # and border. Without this, the web content fits mathematically but clips.
    height = max(260, min(STATE.popup_content_height + 80, 700))
    # SetWindowPos on a window owned by the GUI thread blocks the caller
    # until that thread pumps the message, and each resize forces a WebView2
    # relayout. Dropping no-op resizes keeps a chatty client from stalling
    # the GUI thread.
    if STATE.popup_applied_size == (380, height):
        return
    STATE.popup_applied_size = (380, height)
    try:
        STATE.window.resize(380, height)
    except Exception as exc:
        print("popup resize failed:", exc)


def _destroy_window(window) -> None:
    if window is None:
        return
    try:
        window.destroy()
    except Exception:
        pass


def _present_notes_window(activate: bool) -> None:
    window = STATE.notes_window
    if not window or not STATE.notes_visible:
        return
    try:
        from engine.notes_window import present
        present(window, activate)
        _fit_notes_window(STATE.notes_content_height)
    except Exception:
        NOTES.error = t("notes.errors.windowShowFailed")


def _show_notes_window(view: str = "meeting", activate: bool = True) -> None:
    if view not in ("meeting", "settings", "history"):
        return
    if NOTES.managed and not NOTES.ready:
        view = "settings"
    with _NOTES_WINDOW_LOCK:
        _hide_window()
        if STATE.settings_window:
            STATE.settings_window.hide()
        # Background health/presence updates must not reset an explicit settings
        # view or bring a dismissed window back. A new meeting does select itself.
        STATE.notes_view = view
        STATE.notes_view_request += 1
        STATE.notes_visible = True
        if STATE.notes_window:
            _present_notes_window(activate)
            return
        try:
            window = webview.create_window(APP_DISPLAY_NAME,
                url=_ui_url("?view=notes&lang=" + notes_i18n.language()),
                width=680, height=750, min_size=(360, 400), resizable=True,
                hidden=True, on_top=False, focus=False, text_select=True)
            STATE.notes_window = window
            def loaded():
                _present_notes_window(activate)
            def closing():
                if STATE.quitting:
                    return True
                _hide_notes_window()
                return False
            def closed():
                STATE.notes_window = None
                STATE.notes_visible = False
            def resized(width, height):
                STATE.notes_width = max(360, int(width))
                STATE.notes_size = (STATE.notes_width, int(height))
                if not STATE.notes_expanded:
                    STATE.notes_compact_width = STATE.notes_width
            window.events.loaded += loaded
            window.events.closing += closing
            window.events.closed += closed
            window.events.resized += resized
        except Exception:
            NOTES.error = t("notes.errors.windowOpenFailed")


def _hide_notes_window() -> None:
    if STATE.notes_window:
        STATE.notes_window.hide()
    STATE.notes_visible = False


def _fit_notes_window(content_height: int, expanded: bool | None = None) -> None:
    STATE.notes_content_height = content_height
    if expanded is not None and expanded != STATE.notes_expanded:
        if expanded:
            STATE.notes_compact_width = STATE.notes_width
            STATE.notes_width = max(980, STATE.notes_width)
        else:
            STATE.notes_width = STATE.notes_compact_width
        STATE.notes_expanded = expanded
    window = STATE.notes_window
    if not window or not STATE.notes_visible:
        return
    height = min(1000 if STATE.notes_expanded else 750, max(400, ((content_height + 48 + 7) // 8) * 8))
    size = (STATE.notes_width, height)
    if size == STATE.notes_size:
        return
    from engine.notes_window import resize
    resize(window, *size)
    STATE.notes_size = size


def _show_settings_window() -> None:
    if NOTES.enabled or NOTES.managed:
        _show_notes_window("settings")
        return
    url = _ui_url("?view=settings")
    if STATE.settings_window:
        try:
            STATE.settings_window.show()
            STATE.settings_window.restore()
            return
        except Exception:
            STATE.settings_window = None
    try:
        win = webview.create_window(
            f"{APP_DISPLAY_NAME} · " + t("settings.title"),
            url=url,
            width=760,
            height=720,
            min_size=(520, 420),
            resizable=True,
        )
        STATE.settings_window = win

        def _closed() -> None:
            STATE.settings_window = None

        try:
            win.events.closed += _closed
        except Exception:
            pass
    except Exception as exc:
        print("settings window failed:", exc)


def _close_settings_window() -> None:
    window, STATE.settings_window = STATE.settings_window, None
    _destroy_window(window)


def _show_transcript_window() -> None:
    url = _ui_url("?view=transcript")
    if STATE.transcript_window:
        try:
            STATE.transcript_window.show()
            STATE.transcript_window.restore()
            return
        except Exception:
            STATE.transcript_window = None
    try:
        win = webview.create_window(
            "Live Transcript",
            url=url,
            width=720,
            height=640,
            min_size=(420, 320),
            resizable=True,
        )
        STATE.transcript_window = win

        def _closed() -> None:
            STATE.transcript_window = None
            if not STATE.live_on:
                return
            if STATE.loop and STATE.loop.is_running():
                STATE.loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(_set_live_mode(False))
                )

        try:
            win.events.closed += _closed
        except Exception:
            pass
    except Exception as exc:
        print("transcript window failed:", exc)


def _close_transcript_window() -> None:
    window, STATE.transcript_window = STATE.transcript_window, None
    _destroy_window(window)


def _show_docs_window() -> None:
    url = _ui_url("?view=docs")
    if STATE.docs_window:
        try:
            STATE.docs_window.show()
            STATE.docs_window.restore()
            return
        except Exception:
            STATE.docs_window = None
    try:
        win = webview.create_window(
            f"{APP_DISPLAY_NAME} · Hilfe",
            url=url,
            width=760,
            height=720,
            min_size=(520, 420),
            resizable=True,
        )
        STATE.docs_window = win

        def _closed() -> None:
            STATE.docs_window = None

        try:
            win.events.closed += _closed
        except Exception:
            pass
    except Exception as exc:
        print("documentation window failed:", exc)


def _close_docs_window() -> None:
    window, STATE.docs_window = STATE.docs_window, None
    _destroy_window(window)


# --------------------------------------------------------------------------- #
# Tray
# --------------------------------------------------------------------------- #
def _load_icon() -> Image.Image:
    return Image.open(ICON_PATH).convert("RGBA")


def _recording_icon() -> Image.Image:
    """Favicon with a glowing red 'tally light' dot — shown while recording."""
    img = _load_icon().copy()
    d = ImageDraw.Draw(img)
    w, h = img.size
    r = w * 0.22
    cx, cy = w - r - w * 0.06, h - r - h * 0.06
    d.ellipse([cx - r * 1.7, cy - r * 1.7, cx + r * 1.7, cy + r * 1.7], fill=(255, 40, 40, 70))   # glow
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(240, 30, 30, 255))                          # dot
    d.ellipse([cx - r * 0.45, cy - r * 0.55, cx + r * 0.1, cy], fill=(255, 160, 160, 230))        # highlight
    return img


def _refresh_tray() -> None:
    """Reflect current state in the tray. The red 'recording' overlay tracks an ACTIVE
    recording only — never lingers through post-meeting processing. Background finalize
    jobs are shown in the tooltip instead, so recording and processing read distinctly
    even when they overlap."""
    if not STATE.tray:
        return
    STATE.tray.icon = _recording_icon() if STATE.active else _load_icon()
    n = len(STATE.jobs)
    if STATE.active:
        status = "Recording…" + (f" · {n} processing" if n else "")
    elif n:
        status = f"Processing {n} transcript{'s' if n != 1 else ''}…"
    else:
        status = "Idle"
    STATE.tray.title = f"{APP_DISPLAY_NAME} ({status})"


def _on_show(icon, item) -> None:
    if NOTES.enabled or (NOTES.managed and not NOTES.ready):
        _show_notes_window()
    else:
        _show_window()


def _on_notes_history(icon, item) -> None:
    _show_notes_window("history")


def _on_settings(icon, item) -> None:
    _show_settings_window()


def _on_docs(icon, item) -> None:
    _show_docs_window()


def _noop(icon, item) -> None:
    pass


def _on_quit(icon, item) -> None:
    STATE.quitting = True
    NOTES.shutdown()
    if STATE.notes_window:
        _destroy_window(STATE.notes_window)
    icon.stop()
    if STATE.server:
        STATE.server.should_exit = True
    if STATE.settings_window:
        _close_settings_window()
    if STATE.transcript_window:
        _close_transcript_window()
    if STATE.docs_window:
        _close_docs_window()
    if STATE.window:
        STATE.window.destroy()


def run_tray() -> None:
    items = [
        # default + invisible: left-clicking the tray icon shows the window,
        # without a redundant "Show window" entry in the menu.
        pystray.MenuItem("Show window", _on_show, default=True, visible=False),
        pystray.MenuItem(f"{APP_DISPLAY_NAME} {config.VERSION}", _noop, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda item: t("tray.pastMeetings"), _on_notes_history,
                         visible=lambda item: NOTES.enabled),
        pystray.MenuItem(lambda item: t("tray.settings"), _on_settings),
        pystray.MenuItem("Documentation...", _on_docs, visible=lambda item: not NOTES.enabled),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _on_quit),
    ]
    STATE.tray = pystray.Icon(
        "transcriber",
        _load_icon(),
        f"{APP_DISPLAY_NAME} (Idle)",
        pystray.Menu(*items),
    )
    global _tray_language
    _tray_language = notes_i18n.language()
    STATE.tray.run()


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #
_MAX_SERVER_RESTARTS = 3
_SERVER_RESTART_DELAY_SECONDS = 2.0
# Meetings saved by the crash handler, waiting for a live loop to finish them.
_RESCUED: list[dict] = []


class _SavedRecording:
    """Stands in for a recorder whose WAV has already been written.

    _finalize_meeting stops the recorder to obtain the audio path. After a crash
    the stopping has already happened - it is what saved the audio - so this
    hands back the file that came out of it.
    """

    def __init__(self, path: str | None) -> None:
        self._path = path

    def stop(self) -> str | None:
        return self._path


def _rescue_recording() -> None:
    """Save an in-flight recording after the event loop died under it.

    Capture runs on its own threads, so when the loop dies the audio is still
    being collected - but nothing can stop it, and every further second adds to
    the memory pressure that most likely caused the crash. Writing the WAV here
    frees those buffers and turns the meeting into something the restarted loop
    can still finish into a transcript.
    """
    if not STATE.active:
        return
    if NOTES.current:
        NOTES.current.session.stop()
        NOTES.current.clear_raw()
        NOTES.current.error = "Verarbeitung unterbrochen; Rohtext verworfen."
        NOTES.current.status = "Abgebrochen"
        NOTES.current.ended = datetime.datetime.now().isoformat()
        NOTES.current.expires = time.monotonic() + 24 * 60 * 60
        NOTES.current.busy = False
        NOTES.current = None
        STATE.autostart_suppressed = True
        STATE.active = False
        STATE.started_at = None
        STATE.phase = "idle"
        NOTES.error = t("notes.errors.interrupted")
        return
    recorder, STATE.recorder = STATE.recorder, None
    # Both of these belong to the dead loop; touching them from this thread
    # would only raise. Dropping the background session costs its already
    # transcribed chunks, and finalize falls back to the whole WAV.
    STATE.background_transcription = None
    STATE.realtime = None
    rescue = {
        "title": STATE.title or "Recording",
        "started_at": STATE.started_at or datetime.datetime.now(),
        "ended_at": datetime.datetime.now(),
        "stt_engine": STATE.stt_engine,
        "language": STATE.language,
        "translate_to": STATE.translate_to,
        "audio_path": None,
    }
    STATE.active = False
    STATE.started_at = None
    STATE.manual_recording = False
    STATE.meeting_detected = False
    STATE.detected_at = None
    STATE.stop_suggestion = None
    STATE.audio_silence_prompted = False
    STATE.phase = "idle"
    if recorder is not None:
        try:
            rescue["audio_path"] = recorder.stop()
        except Exception as exc:
            print("rescue: the recording could not be saved:", exc)
    print("rescue: saved recording to", rescue["audio_path"] or "(nothing)")
    _log_event(
        "recording_rescued",
        title=rescue["title"],
        audio_saved=bool(rescue["audio_path"]),
    )
    _RESCUED.append(rescue)
    try:
        _refresh_tray()
    except Exception:
        pass


async def _finalize_rescued() -> None:
    """Finish anything the crash handler saved, now that there is a loop again."""
    if not _RESCUED:
        return
    while _RESCUED:
        rescue = _RESCUED.pop(0)
        job = ProcessingJob(
            rescue["title"], rescue["started_at"], rescue["ended_at"],
        )
        job.detail = "Recovering after an internal restart…"
        STATE.jobs[job.id] = job
        asyncio.create_task(_finalize_meeting(
            job,
            rescue["title"],
            rescue["started_at"],
            rescue["ended_at"],
            _SavedRecording(rescue["audio_path"]),
            None,
            rescue["stt_engine"],
            rescue["language"],
            rescue["translate_to"],
        ))
    await broadcast()


def _serve_once(*, background: bool = True) -> None:
    endpoint = STATE.local_ui
    if endpoint is None:
        raise StartupError(t("startup.errors.connectionNotPrepared"))
    cfg = uvicorn.Config(api, host=endpoint.host, port=endpoint.port, log_level="warning",
                         lifespan="auto" if background else "off")
    server = uvicorn.Server(cfg)
    server.install_signal_handlers = lambda: None  # not on main thread
    STATE.server = server
    # Uvicorn owns this duplicate. The original retains our port even across an
    # event-loop restart, preventing another process from taking its place.
    with endpoint.socket.dup() as listener:
        if os.name == "nt":
            # A Windows IOCP socket cannot attach to a second Proactor loop.
            # This socket-only HTTP/WS server uses its own Selector loop, so
            # restarts can retain the reserved socket without changing global
            # asyncio policy or affecting other threads.
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                runner.run(server.serve(sockets=[listener]))
        else:
            server.run(sockets=[listener])


def run_server() -> None:
    """Serve, and bring the server back if its event loop dies under us.

    An exception raised inside the loop's own machinery rather than inside a
    task unwinds run_forever() and ends this thread - a MemoryError from the
    proactor's poll is the case seen in the field. poll_loop's except clause
    never sees it, because the failure happens outside every task.

    Without this the thread simply disappears while the window stays up: Stop
    posts into a closed port, presence is no longer polled so the meeting never
    ends, and no transcript is written. The app looks alive and is not, and only
    Task Manager gets the user out of it. The browser side already retries the
    WebSocket every second, so a server that comes back is a UI that comes back.
    """
    for attempt in range(_MAX_SERVER_RESTARTS + 1):
        try:
            _serve_once()
        except BaseException as exc:  # MemoryError included, deliberately
            if STATE.quitting:
                return
            print(f"server loop died: {type(exc).__name__}: {exc}")
            _log_event(
                "server_crashed",
                error=f"{type(exc).__name__}: {exc}",
                attempt=attempt + 1,
            )
        else:
            if STATE.quitting:
                return
            print("server stopped without being asked to")
            _log_event("server_stopped", attempt=attempt + 1)
        _rescue_recording()
        if attempt >= _MAX_SERVER_RESTARTS:
            break
        print(f"restarting the server ({attempt + 1}/{_MAX_SERVER_RESTARTS})")
        time.sleep(_SERVER_RESTART_DELAY_SECONDS)
    _log_event("server_gave_up")
    print("the server could not be restarted; please restart the app")


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #
class _TimestampedStream:
    """Prefix every line with a local timestamp and fan it out to one or more streams,
    so all existing print()s become timestamped log entries. Used to tee dev output to
    both the console AND the logfile. Line-buffered: prefix at each line start."""

    def __init__(self, *streams) -> None:
        self._streams = [s for s in streams if s is not None]
        self._at_line_start = True

    def write(self, text: str) -> int:
        if not text:
            return 0
        for line in text.splitlines(keepends=True):
            prefix = ""
            if self._at_line_start and line not in ("\n", "\r\n", "\r"):
                prefix = "[" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "] "
            for s in self._streams:
                try:
                    s.write(prefix + line)
                except Exception:
                    pass
            self._at_line_start = line.endswith(("\n", "\r"))
        return len(text)

    def flush(self) -> None:
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass

    def __getattr__(self, name):  # delegate isatty/fileno/etc. to the first stream
        return getattr(self._streams[0], name)


def _setup_logging() -> None:
    """Timestamp all stdout/stderr and always write to the data-dir logfile. When
    frozen there's no console (built --windowed) so it's file-only; in dev we tee to
    both the console and the logfile (so the log is always inspectable)."""
    logfile = None
    try:
        logfile = open(paths.data_dir() / "transcriber.log", "a", encoding="utf-8", buffering=1)
    except Exception:
        logfile = None
    if paths.is_frozen():
        sys.stdout = _TimestampedStream(logfile or sys.stdout)
    else:
        sys.stdout = _TimestampedStream(sys.stdout, logfile)
    sys.stderr = sys.stdout


def on_start() -> None:
    # Runs after the GUI is ready; start the tray here.
    threading.Thread(target=run_tray, daemon=True).start()
    if NOTES.managed and not NOTES.ready:
        _show_notes_window("settings", activate=True)


def _on_closing() -> bool:
    if STATE.quitting:
        return True  # real Quit: allow the window to close
    _hide_window()   # X button: just hide; tray keeps running
    return False


def _on_resized(width, height) -> None:
    """Remember the window size so the next launch opens at the same dimensions."""
    try:
        STATE.win_w, STATE.win_h = int(width), int(height)
        _save_settings()
    except Exception:
        pass


def _run_app() -> None:
    _setup_logging()
    print(
        "config sources:",
        f"bundled_env={paths.resource_path('.env')}",
        f"bundled_exists={paths.resource_path('.env').exists()}",
        f"user_env={paths.data_dir() / '.env'}",
        f"user_exists={(paths.data_dir() / '.env').exists()}",
        f"stt_backend={config.STT_BACKEND}",
        f"stt_models={','.join(config.TRANSCRIBE_MODELS)}",
        f"primary_endpoint_set={bool(config.AOAI_ENDPOINT)}",
        f"primary_key_set={bool(config.AOAI_API_KEY)}",
        f"failover_endpoint_set={bool(config.AOAI_ENDPOINT_2)}",
        f"failover_key_set={bool(config.AOAI_API_KEY_2)}",
    )
    # Restore the user's saved settings over the env-seeded defaults, before the
    # server/tray start so the popup and tray reflect them immediately.
    _restore_settings()
    digest = validate_assets(FRONTEND_DIST)
    # No identity callback depends on the UI port. Both editions can select a
    # free port instead of opening another application's server.
    endpoint = LocalUI(config.HOST, config.PORT, allow_fallback=True)
    STATE.local_ui = endpoint
    print(f"local UI: {endpoint.url} (preferred port {config.PORT})")
    thread = threading.Thread(target=run_server, daemon=True)
    try:
        thread.start()
        endpoint.wait(digest, alive=thread.is_alive)
        STATE.window = webview.create_window(
            APP_DISPLAY_NAME, url=_ui_url(), width=380, height=320,
            min_size=(380, 260), hidden=True, on_top=True, resizable=False,
        )
        STATE.window.events.closing += _on_closing
        STATE.window.events.resized += _on_resized
        webview.start(on_start, gui="edgechromium", icon=str(ICON_PATH))
    finally:
        STATE.quitting = True
        if STATE.server:
            STATE.server.should_exit = True
        if thread.is_alive():
            thread.join(timeout=5)
        endpoint.close()


def _ui_smoke_test() -> None:
    """Verify the bundled HTML over real HTTP without GUI, sign-in or capture."""
    if not os.getenv("VOICE_TRANSCRIBER_DATA_ROOT"):
        raise StartupError(t("startup.errors.separateDataDir"))
    digest = validate_assets(FRONTEND_DIST)
    endpoint = LocalUI(config.HOST, config.PORT, allow_fallback=True)
    STATE.local_ui = endpoint
    thread = threading.Thread(target=lambda: _serve_once(background=False), daemon=True)
    try:
        thread.start()
        endpoint.wait(digest, alive=thread.is_alive)
        (paths.data_dir() / "ui-smoke-result.json").write_text(json.dumps({
            "ok": True, "port": endpoint.port, "preferredPort": config.PORT,
            "fallbackUsed": endpoint.port != config.PORT, "version": config.VERSION,
            "ownHtmlVerified": True,
        }), encoding="utf-8")
    finally:
        if STATE.server:
            STATE.server.should_exit = True
        if thread.is_alive():
            thread.join(timeout=5)
        endpoint.close()


def main() -> None:
    if "--runtime-smoke-test" in sys.argv:
        try:
            from engine.runtime_smoke import run
            run(paths.data_dir(), ICON_PATH)
        except Exception:
            import traceback
            (paths.data_dir() / "runtime-smoke-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            raise SystemExit(1)
        return
    if "--ui-smoke-test" in sys.argv:
        _setup_logging()
        _ui_smoke_test()
        return
    if "--notes-smoke-test" in sys.argv:
        import azure.cognitiveservices.speech as speechsdk
        from engine.speech.mixed import TimelineMixer
        speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
        validate_assets(FRONTEND_DIST)
        assert TimelineMixer is not None
        assert any(route.path == "/api/notes" for route in api.routes)
        return
    instance = _SingleInstance()
    if not instance.acquire():
        # A second launch exits before it can bind the localhost server or add a
        # second tray icon. The original tray process remains the owner.
        return
    try:
        _run_app()
    except StartupError as exc:
        print(f"startup failed: {exc}")
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, str(exc), t("startup.errors.title", app=APP_DISPLAY_NAME), 0x10)
        raise SystemExit(1) from exc
    finally:
        instance.release()


if __name__ == "__main__":
    main()
