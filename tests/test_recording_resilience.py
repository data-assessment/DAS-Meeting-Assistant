"""Surviving a meeting that runs long, and a loop that dies mid-recording.

A user's PC ran out of memory four minutes into a call. The MemoryError landed
in the event loop's own poll rather than in a task, so nothing caught it: the
loop unwound, the server thread ended, and the window stayed up in front of a
dead backend. Stop did nothing, the meeting never ended, no transcript was
written, and Task Manager was the only way out.

Two halves are guarded here. The recorder must answer "how long is this so far"
without rebuilding the whole recording, because doing that every fifteen
seconds is what exhausted the memory. And when a loop dies anyway - the machine
itself may be out of memory - the audio must survive it.
"""
import asyncio
import datetime

import numpy as np
import pytest

import app
import config
from engine.audio import MeetingRecorder

RATE = 16000
START = datetime.datetime(2026, 8, 17, 14, 0, 0)


def _recorder(seconds: float = 60.0, rate: int = RATE) -> MeetingRecorder:
    """A recorder holding `seconds` of captured audio, no devices involved."""
    recorder = MeetingRecorder("Jour Fixe", START)
    recorder._mic_rate = rate
    recorder._loop_rate = rate
    frames = int(rate * seconds)
    chunk = int(rate)  # one second per chunk, as the callback would deliver
    for offset in range(0, frames, chunk):
        block = np.full((min(chunk, frames - offset), 1), 0.5, dtype=np.float32)
        recorder._mic_chunks.append(block)
        recorder._loop_chunks.append(block.copy())
        recorder._frames["mic"] += len(block)
        recorder._frames["loopback"] += len(block)
    return recorder


# --- the duration must not cost the whole recording ----------------------- #
def test_the_duration_is_counted_not_mixed(monkeypatch):
    """captured_seconds() polls every few seconds; mixing there was the leak."""
    recorder = _recorder(seconds=90)

    def boom(*_args, **_kwargs):
        raise AssertionError("captured_seconds must not rebuild the audio")

    monkeypatch.setattr(recorder, "_mixed_range", boom)

    assert recorder.captured_seconds() == pytest.approx(90.0)


def test_the_duration_follows_the_longer_source():
    """The mix is as long as its longest side, so the duration must be too."""
    recorder = _recorder(seconds=10)
    recorder._frames["loopback"] = int(RATE * 25)

    assert recorder.captured_seconds() == pytest.approx(25.0)


def test_a_recorder_that_captured_nothing_reports_zero():
    assert MeetingRecorder("Empty", START).captured_seconds() == 0.0


# --- a slice must cost only the slice ------------------------------------- #
def test_only_the_requested_chunks_are_collected():
    recorder = _recorder(seconds=60)

    picked = recorder._chunks_for(recorder._mic_chunks, RATE, 50.0, 60.0)

    assert sum(len(part) for part in picked) == RATE * 10


def test_a_partial_chunk_is_trimmed_to_the_range():
    """The range rarely lands on a chunk boundary; the edges must be cut."""
    recorder = _recorder(seconds=10)

    picked = recorder._chunks_for(recorder._mic_chunks, RATE, 2.5, 4.25)

    assert sum(len(part) for part in picked) == int(RATE * 1.75)


def test_a_segment_does_not_rebuild_the_whole_meeting(tmp_path, monkeypatch):
    """The bug: cutting a 10 s slice re-mixed every second recorded so far."""
    recorder = _recorder(seconds=600)
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path), raising=False)
    collapsed = []
    original = recorder._collapse

    def spy(parts, rate):
        collapsed.append(sum(len(part) for part in parts))
        return original(parts, rate)

    monkeypatch.setattr(recorder, "_collapse", spy)

    path = recorder.write_segment_wav(590.0, 600.0, 0)

    assert path is not None
    # One source's worth of the slice, not of the ten minutes behind it.
    assert max(collapsed) == RATE * 10


def test_a_segment_still_holds_the_audio_it_was_asked_for(tmp_path, monkeypatch):
    import wave

    recorder = _recorder(seconds=60)
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path), raising=False)

    path = recorder.write_segment_wav(10.0, 25.0, 3)

    with wave.open(path, "rb") as handle:
        seconds = handle.getnframes() / handle.getframerate()
    assert seconds == pytest.approx(15.0, abs=0.05)


def test_an_empty_range_writes_nothing(tmp_path, monkeypatch):
    recorder = _recorder(seconds=60)
    monkeypatch.setattr(config, "AUDIO_DIR", str(tmp_path), raising=False)

    assert recorder.write_segment_wav(80.0, 90.0, 0) is None


# --- the server must come back -------------------------------------------- #
class _FakeRecorder:
    def __init__(self, path="C:/audio/meeting.wav") -> None:
        self._path = path
        self.stopped = False

    def stop(self):
        self.stopped = True
        return self._path


@pytest.fixture(autouse=True)
def _quiet_state(monkeypatch):
    """Recording state is a module-level singleton; never leak it between tests."""
    monkeypatch.setattr(app.STATE, "quitting", False, raising=False)
    monkeypatch.setattr(app.STATE, "active", False, raising=False)
    monkeypatch.setattr(app.STATE, "recorder", None, raising=False)
    monkeypatch.setattr(app, "_RESCUED", [], raising=False)
    monkeypatch.setattr(app, "_refresh_tray", lambda: None)
    yield


def _recording(monkeypatch, recorder):
    monkeypatch.setattr(app.STATE, "active", True, raising=False)
    monkeypatch.setattr(app.STATE, "recorder", recorder, raising=False)
    monkeypatch.setattr(app.STATE, "title", "Jour Fixe", raising=False)
    monkeypatch.setattr(app.STATE, "started_at", START, raising=False)


def test_the_server_is_restarted_when_its_loop_dies(monkeypatch):
    attempts = []

    def serve():
        attempts.append(1)
        if len(attempts) == 1:
            raise MemoryError()
        app.STATE.quitting = True  # second run: shut down cleanly

    monkeypatch.setattr(app, "_serve_once", serve)
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)

    app.run_server()

    assert len(attempts) == 2


def test_a_clean_quit_does_not_restart_anything(monkeypatch):
    attempts = []

    def serve():
        attempts.append(1)
        app.STATE.quitting = True

    monkeypatch.setattr(app, "_serve_once", serve)

    app.run_server()

    assert len(attempts) == 1


def test_restarting_gives_up_rather_than_spinning(monkeypatch):
    monkeypatch.setattr(app, "_serve_once", lambda: (_ for _ in ()).throw(MemoryError()))
    monkeypatch.setattr(app.time, "sleep", lambda _seconds: None)

    app.run_server()

    assert len(app._RESCUED) == 0  # nothing was recording


def test_a_dead_loop_does_not_lose_the_recording(monkeypatch):
    recorder = _FakeRecorder()
    _recording(monkeypatch, recorder)

    app._rescue_recording()

    assert recorder.stopped is True
    assert len(app._RESCUED) == 1
    assert app._RESCUED[0]["audio_path"] == "C:/audio/meeting.wav"
    # The UI must come back to a state the user can act on, not a stuck one.
    assert app.STATE.active is False
    assert app.STATE.recorder is None
    assert app.STATE.phase == "idle"


def test_a_recorder_that_cannot_save_still_frees_the_state(monkeypatch):
    class _Broken:
        def stop(self):
            raise MemoryError()

    _recording(monkeypatch, _Broken())

    app._rescue_recording()

    assert app.STATE.active is False
    assert app._RESCUED[0]["audio_path"] is None


def test_the_rescued_meeting_is_finalized_after_the_restart(monkeypatch):
    recorder = _FakeRecorder()
    _recording(monkeypatch, recorder)
    app._rescue_recording()
    finalized = {}

    async def fake_finalize(job, title, started, ended, rec, *rest):
        finalized["title"] = title
        finalized["audio"] = rec.stop()

    async def no_broadcast():
        return None

    monkeypatch.setattr(app, "_finalize_meeting", fake_finalize)
    monkeypatch.setattr(app, "broadcast", no_broadcast)

    async def drain():
        await app._finalize_rescued()
        await asyncio.sleep(0)  # let the finalize task run

    asyncio.run(drain())

    assert finalized["title"] == "Jour Fixe"
    # The WAV the crash handler wrote is what gets transcribed.
    assert finalized["audio"] == "C:/audio/meeting.wav"
    assert app._RESCUED == []
