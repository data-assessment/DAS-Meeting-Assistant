"""Realtime streaming transcription via the Azure OpenAI realtime websocket API.

Used when the live overlay is on. Audio captured live (mic + loopback) is mixed and
streamed as pcm16 over a websocket; transcription deltas come back and are pushed to
the popup for a live view. No speaker labels (realtime has no diarization) — the final
saved transcript still comes from the batch diarized pass (hybrid).

Two model kinds (selected in the tray; see config.realtime_engine_kind):

  transcribe  gpt-4o-transcribe, gpt-realtime-whisper, ...
              Source-language transcript. Connects with intent=transcription and
              configures the model via transcription_session.update.

  translate   gpt-realtime-translate
              Emits the live transcript *already translated* into a target language
              (audio.output.language), so no separate per-line translation step is
              needed. Connects as a full realtime session (session.update) requesting
              text-only output; the translated transcript arrives as output-transcript
              deltas. The target language is passed in by the caller.

Flow:
    session = RealtimeSession(on_delta, deployment, language, target_language)
    await session.start()                       # opens ws, configures session
    recorder.set_frame_sink(session.feed)       # capture frames -> live mixer
    ... meeting ...
    text = await session.stop()                 # returns the accumulated text

The exact api-version, query params and event names for the newer translate/whisper
models can vary by deployment — unknown event types and errors are logged (without
secrets) so a live run can be tuned. The connection never carries the API key in a way
that reaches stdout.
"""
import asyncio
import base64
import json
import re
import threading
import time

import config
from engine.stt_context import dictionary_prompt, dictionary_terms, translation_instructions

try:
    import numpy as np
    import soxr
    import websockets
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    np = None
    soxr = None
    websockets = None
    _IMPORT_ERROR = exc


_TICK_SECONDS = 0.1  # how often we drain the mixer and send a chunk
_AUDIO_TOKEN_START = "<|vq_hbr_audio_"
_AUDIO_TOKEN_RE = re.compile(r"<\|vq_hbr_audio_\d+\|>")


def _clean_transcript_text(text: str) -> str:
    return _AUDIO_TOKEN_RE.sub("", text or "")


def _split_trailing_audio_token_prefix(text: str) -> tuple[str, str]:
    idx = text.rfind("<|")
    if idx < 0:
        return text, ""
    candidate = text[idx:]
    suffix = candidate.removeprefix(_AUDIO_TOKEN_START)
    if _AUDIO_TOKEN_START.startswith(candidate) or (
        candidate.startswith(_AUDIO_TOKEN_START) and suffix.isdigit()
    ):
        return text[:idx], candidate
    return text, ""


class _LiveMixer:
    """Combines the two async capture sources into one pcm16 stream at the target
    rate. Each source is resampled (streaming soxr) to the target rate and summed;
    a silent source contributes nothing (so one side talking still streams)."""

    def __init__(self, target_rate: int) -> None:
        self._target = target_rate
        self._lock = threading.Lock()
        self._bufs = {"mic": np.zeros(0, dtype=np.float32), "loopback": np.zeros(0, dtype=np.float32)}
        self._resamplers: dict = {}

    def add(self, source: str, mono: "np.ndarray", src_rate: int) -> None:
        if source not in self._bufs:
            return
        if src_rate != self._target:
            rs = self._resamplers.get(source)
            if rs is None:
                rs = soxr.ResampleStream(src_rate, self._target, 1, dtype="float32")
                self._resamplers[source] = rs
            mono = rs.resample_chunk(mono.astype(np.float32))
        with self._lock:
            self._bufs[source] = np.concatenate([self._bufs[source], np.asarray(mono, dtype=np.float32)])

    def _take(self, source: str, n: int) -> "np.ndarray":
        buf = self._bufs[source]
        out = buf[:n]
        self._bufs[source] = buf[n:]
        if len(out) < n:
            out = np.concatenate([out, np.zeros(n - len(out), dtype=np.float32)])
        return out

    def drain(self, n: int) -> bytes:
        with self._lock:
            mix = self._take("mic", n) + self._take("loopback", n)
        np.clip(mix, -1.0, 1.0, out=mix)
        return (mix * 32767.0).astype("<i2").tobytes()



class RealtimeSession:
    def __init__(self, on_delta, deployment: str | None = None,
                 language: str | None = None, target_language: str | None = None) -> None:
        """on_delta(text: str, final: bool) is called (in the event loop) as
        transcription text arrives — partial deltas (final=False) and completed
        segments (final=True).

        deployment       live model to use (default: config.STT_REALTIME_DEPLOYMENT).
        language         source-language hint; "" or None = auto-detect.
        target_language  output language for the translate model. Ignored by the
                 transcribe models. For the translate model, a blank value
                 disables startup unless STT_REALTIME_TRANSLATE_TO is set.
        """
        self._on_delta = on_delta
        self._deployment = deployment or config.STT_REALTIME_DEPLOYMENT
        self._kind = config.realtime_engine_kind(self._deployment)
        self._language = config.STT_LANGUAGE if language is None else language
        self._dictionary = dictionary_terms()
        target = target_language if target_language is not None else ""
        self._target = target or config.STT_REALTIME_TRANSLATE_TO
        self._mixer = _LiveMixer(config.STT_REALTIME_SAMPLE_RATE) if _IMPORT_ERROR is None else None
        self._ws = None
        self._tasks: list = []
        self._final: list[str] = []
        self._running = False
        self._unknown_logged: set[str] = set()
        self._text_filter_tail = ""
        self._meter_started_at: float | None = None
        self._usage_input_tokens = 0
        self._usage_output_tokens = 0
        self._usage_total_tokens = 0

    @staticmethod
    def available() -> tuple[bool, str | None]:
        if _IMPORT_ERROR is not None:
            return False, f"realtime deps unavailable: {_IMPORT_ERROR}"
        if config.AI_MODE == "gateway":
            return False, "Gateway live transcription is not included in this client. Use direct Azure access."
        from engine.ai_auth import available
        return available()

    @property
    def translated(self) -> bool:
        """True when the live transcript is already translated by the model (so the
        UI must not run its own per-line translation on top)."""
        return self._kind == "translate"

    def feed(self, source: str, mono: "np.ndarray", src_rate: int) -> None:
        """Frame sink for the recorder (called from capture threads)."""
        if self._mixer is not None:
            self._mixer.add(source, mono, src_rate)

    # ---------------------------------------------------------------- connect
    def _base_url(self) -> str:
        return (config.realtime_endpoint().replace("https://", "wss://").rstrip("/")
                + "/openai/realtime?api-version=" + config.STT_REALTIME_API_VERSION)

    def _transcription_session_update(self) -> dict:
        # For the transcription intent the model is set here, NOT as a deployment=
        # query param (passing both yields HTTP 400).
        transcription = {"model": self._deployment}
        if self._language:
            transcription["language"] = self._language
        prompt = dictionary_prompt(self._dictionary)
        if prompt:
            # The legacy Azure transcription-session shape predates the dedicated
            # keywords field, but supports the equivalent prompt guidance.
            transcription["prompt"] = prompt
        if config.STT_REALTIME_WHISPER_DELAY and "whisper" in self._deployment.lower():
            transcription["delay"] = config.STT_REALTIME_WHISPER_DELAY
        return {
            "type": "transcription_session.update",
            "session": {
                "input_audio_format": "pcm16",
                "input_audio_transcription": transcription,
                # shorter silence -> the segment finalizes sooner (less perceived lag)
                "turn_detection": {"type": "server_vad", "silence_duration_ms": 500,
                                   "prefix_padding_ms": 300},
            },
        }

    def _translate_session_update(self) -> dict:
        # Full realtime session: request text-only output (we never play the audio),
        # set the input audio format + VAD, and the translation target language. The
        # model then emits a translated transcript as output-transcript deltas.
        session = {
            "type": "realtime",
            "output_modalities": ["text"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": config.STT_REALTIME_SAMPLE_RATE},
                    "turn_detection": {"type": "server_vad", "silence_duration_ms": 500,
                                       "prefix_padding_ms": 300},
                },
                "output": {"language": self._target},
            },
        }
        instructions = translation_instructions(self._dictionary)
        if instructions:
            session["instructions"] = instructions
        return {
            "type": "session.update",
            "session": session,
        }

    async def start(self) -> None:
        if config.AI_MODE == "gateway":
            raise RuntimeError("Gateway live transcription is not included in this client. Use direct Azure access.")
        if self._kind == "translate":
            if not self._target:
                raise RuntimeError("gpt-realtime-translate requires a target language")
            url = self._base_url() + f"&deployment={self._deployment}"
            update = self._translate_session_update()
        else:
            url = self._base_url() + "&intent=transcription"
            update = self._transcription_session_update()
        print(f"[realtime] connecting model={self._deployment} kind={self._kind} "
              f"target={self._target if self._kind == 'translate' else '-'} "
              f"lang={self._language or 'auto'}")
        from engine.ai_auth import websocket_headers
        self._ws = await websockets.connect(
            url, additional_headers=websocket_headers()
        )
        await self._ws.send(json.dumps(update))
        self._running = True
        self._meter_started_at = time.monotonic()
        self._tasks = [asyncio.create_task(self._send_loop()),
                       asyncio.create_task(self._recv_loop())]


    # ------------------------------------------------------------------ loops
    async def _send_loop(self) -> None:
        chunk = int(config.STT_REALTIME_SAMPLE_RATE * _TICK_SECONDS)
        try:
            while self._running:
                await asyncio.sleep(_TICK_SECONDS)
                pcm = self._mixer.drain(chunk)
                await self._ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm).decode("ascii"),
                }))
        except Exception as exc:
            print("[realtime] send loop ended:", exc)

    # Translate emits its output on the response/output side; with text-only output
    # the exact name varies by model family (transcript vs text), so match a few.
    _TRANSLATE_DELTA = ("output_transcript.delta", "output_audio_transcript.delta",
                        "output_text.delta", "text.delta")
    _TRANSLATE_DONE = ("output_transcript.done", "output_audio_transcript.done",
                       "output_text.done", "text.done")

    @staticmethod
    def _is_delta(t: str, kind: str) -> bool:
        if kind == "translate":
            return t.endswith(RealtimeSession._TRANSLATE_DELTA)
        return t.endswith("input_audio_transcription.delta")

    @staticmethod
    def _is_completed(t: str, kind: str) -> bool:
        if kind == "translate":
            return t.endswith(RealtimeSession._TRANSLATE_DONE)
        return t.endswith("input_audio_transcription.completed")

    @staticmethod
    def _completed_text(evt: dict) -> str:
        # transcribe path uses "transcript"; the translate done event may use
        # "transcript" or "text" depending on the event family.
        return (evt.get("transcript") or evt.get("text") or "").strip()

    async def _recv_loop(self) -> None:
        try:
            async for msg in self._ws:
                self._handle_event(json.loads(msg))
        except Exception as exc:
            print("[realtime] recv loop ended:", exc)

    def _handle_event(self, evt: dict) -> None:
        t = evt.get("type", "")
        usage = evt.get("usage") or (evt.get("response") or {}).get("usage")
        if isinstance(usage, dict):
            self._usage_input_tokens += int(usage.get("input_tokens") or 0)
            self._usage_output_tokens += int(usage.get("output_tokens") or 0)
            self._usage_total_tokens += int(usage.get("total_tokens") or 0)
        if self._is_delta(t, self._kind):
            delta = self._clean_delta(evt.get("delta", ""))
            if delta:
                self._on_delta(delta, False)
        elif self._is_completed(t, self._kind):
            txt = _clean_transcript_text(self._completed_text(evt)).strip()
            if txt:
                self._final.append(txt)
                self._on_delta(txt, True)
        elif t in ("error", "session.error"):
            print("[realtime] error:", evt.get("error"))
        elif t and t not in self._unknown_logged:
            self._unknown_logged.add(t)
            print("[realtime] event:", t)

    def _clean_delta(self, text: str) -> str:
        cleaned = _clean_transcript_text(self._text_filter_tail + (text or ""))
        cleaned, self._text_filter_tail = _split_trailing_audio_token_prefix(
            cleaned
        )
        return cleaned

    async def stop(self) -> str:
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._meter_started_at is not None:
            try:
                from engine import usage as usage_meter
                usage_meter.record(
                    "realtime_stt",
                    self._deployment,
                    input_tokens=self._usage_input_tokens,
                    output_tokens=self._usage_output_tokens,
                    total_tokens=self._usage_total_tokens,
                    session_seconds=time.monotonic() - self._meter_started_at,
                )
            except Exception as exc:
                print("usage metering failed:", exc)
            self._meter_started_at = None
        return "\n".join(self._final)
