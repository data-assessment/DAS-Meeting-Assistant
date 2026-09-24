"""Phase B speech-to-text: transcribe a recorded meeting WAV via Azure OpenAI.

Uses a configured Azure OpenAI audio transcription deployment. Azure caps
/audio/transcriptions at
~25 MB per request, so long meetings are split into time-bounded chunks,
transcribed sequentially, and concatenated.

Phase B surfaces plain transcript text. The chosen model can also diarize
(speaker labels); rendering that is a later phase, so we keep the text path
simple and reliable here.

Import is guarded: if the openai SDK is missing the recorder/transcript still
work, just without text.
"""
import io
import os
import time
import wave
from dataclasses import dataclass, field

import config
from engine.stt_context import combine_prompt

try:
    from openai import AzureOpenAI
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - depends on install
    AzureOpenAI = None
    _IMPORT_ERROR = exc

# 16 kHz * 2 bytes/sample (mono) = 32 KB/s; 600 s ≈ 19 MB, safely under the cap.
_CHUNK_SECONDS = 600

# The GlobalStandard deployment intermittently returns 404/DeploymentNotFound in
# bursts (global-pool routing), beyond just the first-few-minutes propagation;
# 429/5xx are transient too. Retry generously — this runs in the background after
# the meeting, so a ~2 min window is fine and beats losing the transcript.
_TRANSIENT = (
    "DeploymentNotFound", "404", "429", "500", "502", "503", "504",
    "timeout", "timed out", "APIConnectionError", "APITimeoutError",
)
_RETRY_DELAY = 10   # seconds, * attempt, capped at configured max delay


def _is_transient(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker.lower() in message for marker in _TRANSIENT)


@dataclass
class TranscriptResult:
    text: str = ""
    segments: list = field(default_factory=list)  # [{start, end, speaker, text}]
    error: str | None = None
    note: str | None = None  # non-fatal caveat (e.g. cross-chunk speaker labels)


@dataclass
class TranscriptChunkResult:
    index: int
    offset_seconds: float
    result: TranscriptResult


def _append_unique(parts: list[str], value: str | None) -> None:
    if value and value not in parts:
        parts.append(value)


def merge_chunk_results(
    chunks: list[TranscriptChunkResult],
) -> TranscriptResult:
    """Merge independently transcribed rolling chunks."""
    text_parts: list[str] = []
    segments: list[dict] = []
    notes: list[str] = []
    errors: list[str] = []
    successful_chunks = 0
    for chunk in sorted(chunks, key=lambda item: item.index):
        result = chunk.result
        if result.error:
            errors.append(f"chunk {chunk.index + 1}: {result.error}")
            continue
        if result.text.strip():
            text_parts.append(result.text.strip())
        for segment in result.segments or []:
            shifted = dict(segment)
            shifted["start"] = (
                shifted.get("start") or 0.0
            ) + chunk.offset_seconds
            shifted["end"] = (shifted.get("end") or 0.0) + chunk.offset_seconds
            shifted["chunk"] = chunk.index
            segments.append(shifted)
        _append_unique(notes, result.note)
        successful_chunks += 1

    if successful_chunks > 1 and any(
        segment.get("speaker") for segment in segments
    ):
        _append_unique(notes, "speaker labels reset every rolling chunk")
    if errors and not (text_parts or segments):
        return TranscriptResult(
            error="; ".join(errors),
            note="; ".join(notes) or None,
        )
    if errors:
        _append_unique(
            notes,
            "partial rolling transcript: " + "; ".join(errors),
        )
    return TranscriptResult(
        text="\n".join(text_parts),
        segments=segments,
        note="; ".join(notes) or None,
    )


def _enabled() -> tuple[bool, str | None]:
    if config.STT_BACKEND == "none":
        return False, "speech-to-text disabled (STT_BACKEND=none)"
    if _IMPORT_ERROR is not None:
        return False, f"openai SDK unavailable: {_IMPORT_ERROR}"
    from engine.ai_auth import available
    return available()


def _iter_chunks(path: str):
    """Yield in-memory WAV chunks (each ≤ _CHUNK_SECONDS) from a PCM WAV file.

    The SDK infers the upload content type from the file name, so each buffer is
    given a .wav name.
    """
    with wave.open(path, "rb") as w:
        rate, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames_per_chunk = rate * _CHUNK_SECONDS
        idx = 0
        while True:
            frames = w.readframes(frames_per_chunk)
            if not frames:
                break
            buf = io.BytesIO()
            with wave.open(buf, "wb") as cw:
                cw.setnchannels(ch)
                cw.setsampwidth(sw)
                cw.setframerate(rate)
                cw.writeframes(frames)
            buf.seek(0)
            buf.name = f"chunk_{idx}.wav"
            # chunk idx starts at exactly idx * _CHUNK_SECONDS (all but the last
            # chunk are full length), so that's its absolute time offset.
            yield idx * _CHUNK_SECONDS, buf
            idx += 1


def _count_chunks(path: str) -> int:
    """How many _CHUNK_SECONDS slices the WAV splits into (for progress display)."""
    try:
        with wave.open(path, "rb") as w:
            frames_per_chunk = w.getframerate() * _CHUNK_SECONDS
            if frames_per_chunk <= 0:
                return 1
            return max(1, -(-w.getnframes() // frames_per_chunk))  # ceil div
    except Exception:
        return 1


def transcribe_wav(path: str, deployment: str | None = None,
                   language: str | None = None, prompt: str | None = None,
                   progress=None, providers: list[dict] | None = None) -> TranscriptResult:
    """Transcribe a meeting WAV.

    Diarization is auto-detected from the deployment name: a '*-diarize' model
    returns speaker segments (and rejects prompts); other models (e.g.
    gpt-4o-transcribe, whisper) return plain text and accept a `prompt` to bias
    names/jargon. Returns text/segments, or an error explaining why it didn't run
    (the caller writes that into the transcript).

    Provider order: an explicit `deployment` pins a single provider (CLI); else an
    explicit `providers` list (e.g. the tray-chosen engine first) is used in order
    with failover; else the configured config.stt_providers()."""
    ok, why = _enabled()
    if not ok:
        return TranscriptResult(error=why)
    if not path or not os.path.isfile(path):
        return TranscriptResult(error="no audio file to transcribe")

    language = config.STT_LANGUAGE if language is None else language
    prompt = combine_prompt(prompt)
    # An explicit deployment (e.g. from the CLI) pins one provider; otherwise use
    # the configured list and fail over to the second resource on error.
    if deployment:
        providers = config.providers_for_model(deployment)
    elif not providers:
        providers = config.stt_providers()

    last_err: Exception | None = None
    for i, p in enumerate(providers):
        try:
            print(
                "transcription provider:",
                f"deployment={p['deployment']}",
                f"endpoint={p['endpoint']}",
                f"api_version={p['api_version']}",
            )
            return _transcribe_one(path, p, language, prompt, progress)
        except Exception as exc:
            last_err = exc
            more = " — trying fallback" if i + 1 < len(providers) else ""
            print(f"transcription failed on {p['deployment']}@{p['endpoint']}: {exc}{more}")
    return TranscriptResult(error=f"transcription failed (all providers): {last_err}")


def _transcribe_one(path: str, provider: dict, language: str, prompt, progress) -> TranscriptResult:
    """Transcribe via one provider; raises on failure so the caller can fail over."""
    deployment = provider["deployment"]
    dl = deployment.lower()
    is_diarize = "diarize" in dl       # speaker-labelled segments
    is_whisper = "whisper" in dl       # verbose_json -> time-stamped segments (no speaker)

    from engine.ai_auth import openai_auth_kwargs
    client = AzureOpenAI(
        azure_endpoint=provider["endpoint"],
        api_version=provider["api_version"],
        timeout=max(1.0, config.STT_REQUEST_TIMEOUT_SECONDS),
        **openai_auth_kwargs(provider),
    )

    total_chunks = _count_chunks(path)
    parts: list[str] = []
    segments: list[dict] = []
    n_chunks = 0
    for offset, chunk in _iter_chunks(path):
        n_chunks += 1
        if progress:
            try:
                progress(n_chunks, total_chunks)
            except Exception:
                pass
        resp = _create_with_retry(
            client, chunk, deployment, is_diarize, is_whisper, language, prompt
        )
        data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        try:
            usage_data = data.get("usage") or {}
            from engine import usage as usage_meter
            usage_meter.record(
                "batch_stt",
                deployment,
                input_tokens=usage_data.get("input_tokens", 0),
                output_tokens=usage_data.get("output_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
                audio_seconds=_wav_duration(chunk),
                request_id=getattr(resp, "_request_id", ""),
            )
        except Exception as exc:
            print("usage metering failed:", exc)
        text = (data.get("text") or "").strip()
        if text:
            parts.append(text)
        for s in (data.get("segments") or []) if (is_diarize or is_whisper) else []:
            segments.append({
                "start": (s.get("start") or 0.0) + offset,
                "end": (s.get("end") or 0.0) + offset,
                # whisper segments carry no speaker (None); diarize carries A/B/…
                "speaker": (s.get("speaker") or "?") if is_diarize else None,
                "text": (s.get("text") or "").strip(),
                "chunk": offset // _CHUNK_SECONDS,
            })

    # Diarization runs per request, so speaker labels (A/B/…) are only consistent
    # within a chunk; across chunks the same letter may be a different person.
    note = None
    if n_chunks > 1 and is_diarize and segments:
        note = f"speaker labels reset every ~{_CHUNK_SECONDS // 60} min (per-chunk diarization)"
    return TranscriptResult(text="\n".join(parts), segments=segments, note=note)


def _wav_duration(chunk) -> float:
    """Duration of an in-memory WAV without changing its position for callers."""
    position = chunk.tell()
    try:
        chunk.seek(0)
        with wave.open(chunk, "rb") as audio:
            rate = audio.getframerate()
            return audio.getnframes() / rate if rate else 0.0
    finally:
        chunk.seek(position)


def _create_with_retry(client, chunk, deployment, is_diarize, is_whisper, language, prompt):
    """Call the transcription API, retrying transient errors (notably the
    DeploymentNotFound a freshly-created deployment throws while it propagates)."""
    kwargs: dict = {}
    if language:
        kwargs["language"] = language
    if is_diarize:
        kwargs["response_format"] = "diarized_json"
        kwargs["chunking_strategy"] = "auto"  # required by the diarization model
    elif is_whisper:
        kwargs["response_format"] = "verbose_json"  # time-stamped segments (no speaker)
        if prompt:
            kwargs["prompt"] = prompt
    else:
        kwargs["response_format"] = "json"
        if prompt:
            kwargs["prompt"] = prompt  # diarize models reject this; plain models accept it

    last = None
    max_tries = max(1, config.STT_MAX_RETRIES)
    max_delay = max(0.0, config.STT_RETRY_MAX_DELAY_SECONDS)
    for attempt in range(1, max_tries + 1):
        try:
            chunk.seek(0)
            return client.audio.transcriptions.create(
                model=deployment, file=chunk, **kwargs
            )
        except Exception as exc:
            last = exc
            if attempt < max_tries and _is_transient(exc):
                time.sleep(min(_RETRY_DELAY * attempt, max_delay))
                continue
            raise
    raise last
