"""Unplugging a headset must not end the meeting capture (field report)."""
import asyncio
import threading
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

from engine import meeting_notes as mn
from engine.speech import capture as capture_module
from engine.speech.core import AudioBuffer
from engine.speech.mixed import (MixedCapture, MixedSession, RECOVERY_INTERVAL, RETURN_INTERVAL,
                                 candidate_selections)
from test_meeting_notes import DRAFT, notes  # noqa: F401 - fixture

def device(name, loopback=False, default=False):
    return dict(name=name, isLoopbackDevice=loopback, isSystemDefault=default,
                defaultSampleRate=48000, maxInputChannels=2 if loopback else 1)

HEADSET_MIC = device("Mikrofon (Headset)", default=True)
HEADSET_OUT = device("Lautsprecher (Headset) [Loopback]", loopback=True, default=True)
LAPTOP_MIC = device("Mikrofonarray (Laptop)")
LAPTOP_OUT = device("Lautsprecher (Laptop) [Loopback]", loopback=True)
SELECTION = {"mic": HEADSET_MIC, "loopback": HEADSET_OUT}


def laptop_only():
    return [device(LAPTOP_MIC["name"], default=True), device(LAPTOP_OUT["name"], loopback=True, default=True)]


class Machine:
    """What Windows and PortAudio report; `unplug()`/`replug()` change both."""
    def __init__(self):
        self.devices = [HEADSET_MIC, HEADSET_OUT, LAPTOP_MIC, LAPTOP_OUT]
        self.captures = []
        self.listed = None  # Windows' endpoint list when it differs from PortAudio's
        self.broken = set()  # listed devices that still fail to open

    def unplug(self):
        self.devices = laptop_only()
        for capture in self.captures:
            capture.active = False

    def replug(self):
        self.devices = [HEADSET_MIC, HEADSET_OUT, device(LAPTOP_MIC["name"]), device(LAPTOP_OUT["name"], loopback=True)]

    def names(self):
        return self.listed if self.listed is not None else {d["name"] for d in self.devices}

    def factory(self, selection, inputs, lost):
        machine = self
        class PhysicalCapture:
            def __init__(self):
                self.selection, self.inputs, self.lost = selection, inputs, lost
                self.active, self.started, self.closed, self.metrics = True, False, False, {}
                machine.captures.append(self)
            def open(self):
                if any(d not in machine.devices or d["name"] in machine.broken for d in selection.values()):
                    raise OSError("device unavailable")
            def start(self): self.started = True
            def healthy(self): return self.active and not self.closed
            def close(self): self.closed = True
        return PhysicalCapture()


def mixed_capture(machine, errors=None):
    mixed = MixedCapture(SELECTION, AudioBuffer(1 << 20), (errors if errors is not None else []).append,
                         machine.factory, lambda: list(machine.devices), machine.names)
    mixed.open()
    mixed.capture.start()
    return mixed


def names(capture):
    return {source: d["name"] for source, d in capture.selection.items()}


def test_candidates_are_originals_then_devices_in_use_then_windows_defaults():
    laptop = {"mic": LAPTOP_MIC, "loopback": LAPTOP_OUT}
    usb = {"mic": device("Mikrofon (USB)", default=True), "loopback": device("USB [Loopback]", loopback=True, default=True)}
    listed = [HEADSET_MIC | {"isSystemDefault": False}, HEADSET_OUT | {"isSystemDefault": False},
              LAPTOP_MIC, LAPTOP_OUT, *usb.values()]
    assert [{s: d["name"] for s, d in c.items()} for c in candidate_selections(SELECTION, laptop, listed)] == [
        {s: d["name"] for s, d in selection.items()} for selection in (SELECTION, laptop, usb)]
    fallback = candidate_selections(SELECTION, SELECTION, laptop_only())
    assert [{s: d["name"] for s, d in c.items()} for c in fallback] == [{"mic": LAPTOP_MIC["name"], "loopback": LAPTOP_OUT["name"]}]
    assert candidate_selections(SELECTION, SELECTION, [device(LAPTOP_MIC["name"], default=True)]) == []


def test_unplugged_headset_keeps_recording_on_the_windows_defaults_and_returns():
    machine, errors = Machine(), []
    mixed = mixed_capture(machine, errors)
    first = mixed.capture
    machine.unplug()
    mixed._supervise_once(time.monotonic())
    assert first.closed and mixed.healthy() and not errors
    assert names(mixed.capture) == {"mic": LAPTOP_MIC["name"], "loopback": LAPTOP_OUT["name"]}
    assert mixed.capture.started and "gewechselt" in mixed.notice and "Mikrofonarray (Laptop)" in mixed.notice
    assert "[Loopback]" not in mixed.notice
    fallback = mixed.capture
    mixed._supervise_once(time.monotonic())
    assert mixed.capture is fallback, "The headset is still missing; keep the fallback running"
    machine.replug()
    mixed._supervise_once(time.monotonic() + RETURN_INTERVAL)
    assert fallback.closed and names(mixed.capture) == {s: d["name"] for s, d in SELECTION.items()}
    assert "fortgesetzt" in mixed.notice
    assert mixed.interruptions == 1 and "1× unterbrochen" in mixed.summary()
    returned = mixed.capture
    mixed.close()
    assert returned.closed and not errors


def test_device_errors_from_callbacks_also_reopen_the_devices():
    machine = Machine()
    mixed = mixed_capture(machine)
    first = mixed.capture
    first.lost("Audioaufnahme fehlgeschlagen")
    mixed._supervise_once(time.monotonic())
    assert first.closed and mixed.capture is not first and mixed.capture.started
    assert names(mixed.capture) == {s: d["name"] for s, d in SELECTION.items()}
    assert mixed.notice == "Audioaufnahme nach kurzer Unterbrechung fortgesetzt."


def test_without_any_device_capture_waits_and_retries():
    machine = Machine()
    mixed = mixed_capture(machine)
    machine.unplug()
    machine.devices = []
    now = time.monotonic()
    mixed._supervise_once(now)
    assert mixed.capture is None and mixed.healthy() and "unterbrochen" in mixed.notice
    machine.devices = laptop_only()
    mixed._supervise_once(now + 0.25)
    assert mixed.capture is None, "Retries are paced"
    mixed._supervise_once(now + 1.5)
    assert mixed.capture is not None and mixed.capture.started
    machine.unplug()
    machine.devices = []
    mixed._supervise_once(time.monotonic())
    mixed.close()
    assert "kein Audiogerät" in mixed.summary()


def test_listed_but_unopenable_headset_backs_off_instead_of_churning():
    machine = Machine()
    mixed = mixed_capture(machine)
    machine.unplug()
    mixed._supervise_once(time.monotonic())
    fallback = mixed.capture
    machine.listed = {HEADSET_MIC["name"], HEADSET_OUT["name"]}  # Windows lists it, PortAudio not yet
    now = time.monotonic()
    mixed._supervise_once(now)
    assert fallback.closed and "Laptop" in names(mixed.capture)["mic"]
    assert mixed.return_interval == 2 * RETURN_INTERVAL
    retry = mixed.capture
    mixed._supervise_once(now + RETURN_INTERVAL)
    assert mixed.capture is retry, "Wait for the longer interval"


def test_listed_headset_that_fails_to_open_does_not_silence_the_meeting():
    machine = Machine()
    mixed = mixed_capture(machine)
    machine.unplug()
    mixed._supervise_once(time.monotonic())
    fallback = mixed.capture
    machine.replug()  # Windows makes the headset the default again ...
    machine.broken = {HEADSET_MIC["name"]}  # ... but it cannot be opened yet
    mixed._supervise_once(time.monotonic() + RETURN_INTERVAL)
    laptop = {"mic": LAPTOP_MIC["name"], "loopback": LAPTOP_OUT["name"]}
    assert fallback.closed, "PortAudio only lists the headset after the old capture is closed"
    assert mixed.capture is not None and mixed.capture.started and names(mixed.capture) == laptop
    assert "gewechselt" in mixed.notice and mixed.return_interval == 2 * RETURN_INTERVAL
    rollback = mixed.capture
    rollback.active = False  # later retries must not insist on the listed headset either
    mixed._supervise_once(time.monotonic() + RECOVERY_INTERVAL)
    assert rollback.closed and mixed.capture.started and names(mixed.capture) == laptop


def test_overflow_flags_are_not_fatal():
    import pyaudiowpatch as pa
    offered, failures = [], []
    capture = capture_module.Capture.__new__(capture_module.Capture)
    capture.buffers = {"mic": NS(offer=lambda data: offered.append(data) or True)}
    capture.fail = failures.append
    capture.metrics = {"mic": {"dbfs": -100.0, "frames": 0, "last_frame": None}}
    data = np.zeros(1024, dtype="<f4").tobytes()
    assert capture._callback("mic", 1)(data, 1024, None, pa.paInputOverflow) == (None, pa.paContinue)
    assert offered == [data] and not failures


class Signal:
    def __init__(self): self.handlers = []
    def connect(self, fn): self.handlers.append(fn)
    def disconnect_all(self): self.handlers.clear()
    def emit(self, event):
        for fn in self.handlers[:]: fn(event)


class Future:
    def get(self): pass


def speech_sdk(recognizers):
    class Config:
        def __init__(self, **kwargs): pass
        def set_property(self, key, value): pass
    class Transcriber:
        def __init__(self, speech_config, audio_config):
            self.stream = audio_config.stream
            for name in ("transcribed", "transcribing", "session_stopped", "canceled"):
                setattr(self, name, Signal())
            recognizers.append(self)
        def start_transcribing_async(self):
            def consume():
                target = memoryview(bytearray(3200))
                while self.stream.callback.read(target):
                    pass
                self.canceled.emit(NS(reason="eof"))
                self.session_stopped.emit(NS())
            threading.Thread(target=consume, daemon=True).start()
            return Future()
        def stop_transcribing_async(self): return Future()
        def say(self, text, seconds):
            self.transcribed.emit(NS(result=NS(reason="final", text=text, speaker_id="Guest-1", offset=seconds * 10_000_000)))
    class Pull:
        def __init__(self): pass
    class Stream:
        def __init__(self, callback, format): self.callback = callback
    return NS(SpeechConfig=Config, transcription=NS(ConversationTranscriber=Transcriber),
              SpeechRecognizer=lambda **_: pytest.fail("mixed mode uses one transcriber"),
              audio=NS(PullAudioInputStreamCallback=Pull, PullAudioInputStream=Stream,
                       AudioStreamFormat=lambda **kwargs: kwargs, AudioConfig=lambda **kwargs: NS(**kwargs)),
              PropertyId=NS(SpeechServiceResponse_DiarizeIntermediateResults="diarize",
                            SpeechServiceConnection_EnableAudioLogging="audio_logging", Speech_LogFilename="log_file"),
              ResultReason=NS(RecognizedSpeech="final", RecognizingSpeech="partial"),
              CancellationReason=NS(Error="error", EndOfStream="eof"))


def wait(condition, seconds=3):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.02)


def test_meeting_summary_covers_the_time_after_the_headset_was_unplugged(notes, monkeypatch):
    machine, recognizers, summarized = Machine(), [], []
    def generate(text, provider, language="de"):
        summarized.append(text)
        return dict(DRAFT)
    monkeypatch.setattr(mn, "generate_draft", generate)
    factory = lambda name: MixedSession(name, speech_sdk(recognizers), machine.factory,
                                        lambda: list(machine.devices), machine.names)
    review = notes.start("Test-Meeting", "2026-09-23T10:00:00", [HEADSET_MIC, HEADSET_OUT, LAPTOP_MIC, LAPTOP_OUT], factory)
    session = review.session
    wait(lambda: session.phase == "Läuft")
    recognizers[0].say("Vor dem Stecker", 1)
    machine.unplug()
    wait(lambda: (c := session.capture.capture) is not None and "Laptop" in names(c)["mic"])
    assert not session.cancel.is_set() and not session.finished.is_set() and session.error == ""
    assert "gewechselt" in review.public()["warning"]
    recognizers[0].say("Nach dem Stecker", 30)
    notes.current = None
    asyncio.run(notes.finish(review))
    assert session.error == "" and review.status == "Gespräch beendet"
    assert "Vor dem Stecker" in summarized[0] and "Nach dem Stecker" in summarized[0]
    assert "1× unterbrochen" in review.warning
