import importlib
import asyncio
import datetime

import config
import app
from engine import summarize
from engine.transcribe import (
    TranscriptChunkResult,
    TranscriptResult,
    _create_with_retry,
    _is_transient,
    merge_chunk_results,
)
from engine.stt_context import combine_prompt


def test_merge_chunk_results_offsets_segments_and_text():
    merged = merge_chunk_results([
        TranscriptChunkResult(
            1,
            600.0,
            TranscriptResult(
                text="second",
                segments=[{"start": 1.0, "end": 2.0, "speaker": "A", "text": "second"}],
            ),
        ),
        TranscriptChunkResult(
            0,
            0.0,
            TranscriptResult(
                text="first",
                segments=[{"start": 3.0, "end": 4.0, "speaker": "B", "text": "first"}],
            ),
        ),
    ])

    assert merged.text == "first\nsecond"
    assert merged.segments == [
        {"start": 3.0, "end": 4.0, "speaker": "B", "text": "first", "chunk": 0},
        {"start": 601.0, "end": 602.0, "speaker": "A", "text": "second", "chunk": 1},
    ]
    assert "speaker labels reset" in (merged.note or "")


def test_merge_chunk_results_reports_complete_failure():
    merged = merge_chunk_results([
        TranscriptChunkResult(0, 0.0, TranscriptResult(error="upload failed")),
    ])

    assert "upload failed" in (merged.error or "")


def test_timeout_errors_are_transient():
    assert _is_transient(RuntimeError("Request timed out."))
    assert _is_transient(RuntimeError("APITimeoutError"))


def test_dictionary_normalization_preserves_unicode_and_removes_duplicates():
    assert config.normalize_stt_dictionary([
        " CaféPlan ", "Workboard", "CAFÉPLAN", "", "Power   BI",
    ]) == ["CaféPlan", "Workboard", "Power BI"]


def test_dictionary_is_combined_with_explicit_batch_prompt(monkeypatch):
    monkeypatch.setattr(config, "STT_DICTIONARY", ["CaféPlan", "Workboard"])

    assert combine_prompt("Participants: Robin") == (
        "Participants: Robin\n\n"
        "Important business terms and exact spellings: CaféPlan, Workboard."
    )


def test_batch_request_sends_dictionary_prompt(monkeypatch):
    captured = {}

    class Transcriptions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return {"text": "ok"}

    class Client:
        audio = type("Audio", (), {"transcriptions": Transcriptions()})()

    monkeypatch.setattr(config, "STT_MAX_RETRIES", 1)
    prompt = combine_prompt(None, ["CaféPlan", "Power BI"])
    _create_with_retry(
        Client(),
        __import__("io").BytesIO(b"wav"),
        "gpt-4o-transcribe",
        False,
        False,
        "de",
        prompt,
    )

    assert captured["prompt"] == (
        "Important business terms and exact spellings: CaféPlan, Power BI."
    )


def test_diarization_request_omits_unsupported_prompt(monkeypatch):
    captured = {}

    class Transcriptions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return {"text": "ok"}

    class Client:
        audio = type("Audio", (), {"transcriptions": Transcriptions()})()

    monkeypatch.setattr(config, "STT_MAX_RETRIES", 1)
    _create_with_retry(
        Client(),
        __import__("io").BytesIO(b"wav"),
        "gpt-4o-transcribe-diarize",
        True,
        False,
        "de",
        combine_prompt(None, ["CaféPlan"]),
    )

    assert "prompt" not in captured


def test_comment_like_optional_env_values_are_blank(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv(
        "AOAI_API_VERSION_2",
        "# blank = reuse AOAI_API_VERSION",
    )
    monkeypatch.setenv("STT_LANGUAGE", "# force a language or blank = auto")
    importlib.reload(config)
    try:
        assert config.AOAI_API_VERSION_2 == ""
        assert config.STT_LANGUAGE == ""
    finally:
        monkeypatch.delenv("AOAI_API_VERSION_2", raising=False)
        monkeypatch.delenv("STT_LANGUAGE", raising=False)
        importlib.reload(config)


def test_rolling_transcription_broadcasts_chunk_activity(monkeypatch, tmp_path):
    chunk_path = tmp_path / "chunk.wav"
    chunk_path.write_bytes(b"wav")
    statuses = []

    class Recorder:
        def write_segment_wav(self, _start, _end, _index):
            return str(chunk_path)

    async def fake_broadcast():
        statuses.append(app.state_payload()["backgroundTranscriptionStatus"])

    import engine.transcribe as transcribe

    monkeypatch.setattr(app, "broadcast", fake_broadcast)
    monkeypatch.setattr(app.config, "stt_providers_for", lambda _engine: [])
    monkeypatch.setattr(
        transcribe,
        "transcribe_wav",
        lambda *_args: TranscriptResult(text="hello"),
    )
    session = app.BackgroundTranscriptionSession(Recorder(), "azure_openai", "")
    previous = app.STATE.background_transcription
    app.STATE.background_transcription = session
    try:
        asyncio.run(session._transcribe_chunk(0, 60, None))
    finally:
        app.STATE.background_transcription = previous

    assert statuses == [
        "Transcribing audio chunk 1…",
        "Audio chunk 1 transcribed",
    ]


def test_retry_transcription_deletes_wav_after_success(monkeypatch, tmp_path):
    audio_path = tmp_path / "recording.wav"
    audio_path.write_bytes(b"wav")
    transcript_path = tmp_path / "transcript.md"
    statuses = []

    async def fake_job_status(_job, detail):
        statuses.append(detail)

    async def fake_sleep(_seconds):
        return None

    async def fake_broadcast():
        return None

    monkeypatch.setattr(app, "_job_status", fake_job_status)
    monkeypatch.setattr(app.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(app.config, "SUMMARIZE", False)
    monkeypatch.setattr(app.config, "CLEAN_TRANSCRIPT", False)
    monkeypatch.setattr(app.config, "stt_providers_for", lambda _engine: [])
    monkeypatch.setattr(app, "broadcast", fake_broadcast)
    monkeypatch.setattr(app, "_refresh_tray", lambda: None)
    monkeypatch.setattr(
        app,
        "write_transcript",
        lambda *_args: transcript_path,
    )

    import engine.transcribe as transcribe

    monkeypatch.setattr(
        transcribe,
        "transcribe_wav",
        lambda *_args: TranscriptResult(text="hello"),
    )

    job = app.ProcessingJob(
        "Retry: Recording",
        datetime.datetime(2026, 1, 1, 10, 0),
        datetime.datetime(2026, 1, 1, 10, 1),
    )
    app.STATE.jobs[job.id] = job

    asyncio.run(app._retry_transcription_job(
        job,
        str(audio_path),
        "Recording",
        datetime.datetime(2026, 1, 1, 10, 0),
        datetime.datetime(2026, 1, 1, 10, 1),
        "azure_openai",
        "",
    ))

    assert not audio_path.exists()
    assert statuses[-1] == "Done - transcript saved"


def test_retry_transcription_keeps_wav_after_failure(monkeypatch, tmp_path):
    audio_path = tmp_path / "recording.wav"
    audio_path.write_bytes(b"wav")

    async def fake_job_status(_job, _detail):
        return None

    async def fake_sleep(_seconds):
        return None

    async def fake_broadcast():
        return None

    monkeypatch.setattr(app, "_job_status", fake_job_status)
    monkeypatch.setattr(app.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(app.config, "stt_providers_for", lambda _engine: [])
    monkeypatch.setattr(app, "broadcast", fake_broadcast)
    monkeypatch.setattr(app, "_refresh_tray", lambda: None)

    import engine.transcribe as transcribe

    monkeypatch.setattr(
        transcribe,
        "transcribe_wav",
        lambda *_args: TranscriptResult(error="failed"),
    )

    job = app.ProcessingJob(
        "Retry: Recording",
        datetime.datetime(2026, 1, 1, 10, 0),
        datetime.datetime(2026, 1, 1, 10, 1),
    )
    app.STATE.jobs[job.id] = job

    asyncio.run(app._retry_transcription_job(
        job,
        str(audio_path),
        "Recording",
        datetime.datetime(2026, 1, 1, 10, 0),
        datetime.datetime(2026, 1, 1, 10, 1),
        "azure_openai",
        "",
    ))

    assert audio_path.exists()
