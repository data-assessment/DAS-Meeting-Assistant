import threading
import time
from types import SimpleNamespace as NS
import numpy as np
import pytest
from prototypes.speech.core import AudioBuffer, PcmReader, Transcript
from prototypes.speech.mixed import TimelineMixer, MixInput, MixedCapture, MixedSession, RATE
from prototypes.speech.tests.test_session import fake_sdk, SELECTION, wait_running
from prototypes.speech.tests.test_core import drain


def take(mixer, end):
    data = bytearray()
    while chunk := mixer.take_until(end):
        data.extend(chunk)
    return np.frombuffer(data, dtype="<f4")


def test_two_sources_sum_with_headroom_on_one_timeline():
    mixer = TimelineMixer(10)
    mixer.write("mic", np.full(1600, .8), 10.1)
    mixer.write("loopback", np.full(1600, .6), 10.1)
    result = take(mixer, 1600)
    assert len(result) == 1600
    np.testing.assert_allclose(result, .7)
    assert not mixer.samples.any()


def test_loopback_silence_does_not_compress_time_or_block_mic():
    mixer = TimelineMixer(0)
    mixer.write("mic", np.full(1600, .4), .1)
    first = take(mixer, 16000)
    np.testing.assert_allclose(first[:1600], .2)
    assert not first[1600:].any()
    mixer.write("loopback", np.full(1600, .8), 1.1)
    np.testing.assert_allclose(take(mixer, 17600), .4)


def test_callback_jitter_preserves_consecutive_audio_without_gaps():
    mixer = TimelineMixer(0)
    mixer.write("mic", np.ones(320), .020)
    mixer.write("mic", np.ones(320), .045)
    mixer.write("mic", np.ones(320), .058)
    np.testing.assert_allclose(take(mixer, 960), .5)


def test_initial_pre_start_audio_trimmed_and_output_finite():
    mixer = TimelineMixer(0)
    mixer.write("mic", np.array([np.nan, np.inf, -np.inf, 2.] * 80), .010)
    result = take(mixer, 160)
    assert len(result) == 160 and np.isfinite(result).all()
    assert max(abs(result)) <= .5


def test_late_audio_is_kept_and_only_capacity_overflow_fails():
    mixer = TimelineMixer(0)
    take(mixer, 1000)
    mixer.write("loopback", np.ones(320), .02)  # already past its playout time
    np.testing.assert_allclose(take(mixer, 1320), .5)
    with pytest.raises(ValueError, match="full"):
        mixer.write("mic", np.ones(320), 5)
    assert not mixer.samples.any() and "mic" not in mixer.next


def test_ring_wrap_does_not_replay_old_audio():
    mixer = TimelineMixer(0, capacity=640)
    mixer.write("mic", np.ones(640), .04)
    np.testing.assert_allclose(take(mixer, 640), .5)
    mixer.write("mic", np.full(320, .4), .06)
    result = take(mixer, 1280)
    np.testing.assert_allclose(result[:320], .2)
    assert not result[320:].any()
    mixer.clear()
    assert not mixer.samples.any() and not mixer.next
    assert mixer.take_until(1600) == b""


@pytest.mark.parametrize("rate,channels", [(48000,2), (44100,1), (16000,1)])
def test_native_rates_resampled_and_tail_flushed_in_ram(monkeypatch, rate, channels):
    clock = [0.0]
    monkeypatch.setattr("prototypes.speech.mixed.time.monotonic", lambda: clock[0])
    errors = []
    owner = NS(stopping=threading.Event(), mixer=TimelineMixer(0), fail=errors.append)
    sink = MixInput("mic", {"defaultSampleRate":rate, "maxInputChannels":channels}, owner)
    block = np.full((rate // 100, channels), .6, dtype="<f4").tobytes()
    for n in range(20):
        clock[0] = (n + 1) / 100
        assert sink.offer(block)
    sink.finish()
    result = take(owner.mixer, 3200)
    assert not errors and sink.resampler is None
    np.testing.assert_allclose(result[200:-200], .3, atol=.002)
    assert owner.mixer.last == 3200


class PhysicalCapture:
    def __init__(self, selection, buffers, fail):
        self.buffers = buffers
        self.closed = False
        self.metrics = {}
        self.halt = threading.Event()
        self.worker = None

    def open(self): pass

    def start(self):
        def produce():
            while not self.halt.wait(.02):
                for source, buffer in self.buffers.items():
                    buffer.offer(np.full(320, .2 if source == "mic" else .6, dtype="<f4").tobytes())
        self.worker = threading.Thread(target=produce, daemon=True)
        self.worker.start()

    def close(self):
        self.halt.set()
        if self.worker:
            self.worker.join(1)
        self.closed = True


def test_single_azure_transcriber_receives_both_sources_and_final_text(monkeypatch):
    sdk, configs, recognizers = fake_sdk()
    def forbidden(**kwargs):
        pytest.fail("Mixed mode must not create a microphone recognizer")
    sdk.SpeechRecognizer = forbidden
    captured = bytearray()
    original_stream = sdk.audio.PullAudioInputStream
    class Stream(original_stream):
        def __init__(self, callback, format):
            assert format == {"samples_per_second":16000, "bits_per_sample":16, "channels":1}
            original_read = callback.read
            def read(target):
                count = original_read(target)
                captured.extend(target[:count])
                return count
            callback.read = read
            super().__init__(callback, format)
    sdk.audio.PullAudioInputStream = Stream
    session = MixedSession("Alex", sdk, PhysicalCapture)
    session.start("westeurope", "synthetic-test-key", "de-DE", SELECTION)
    wait_running(session)
    time.sleep(.45)
    session.stop()
    assert session.finished.wait(3)
    assert not session.error
    assert len(configs) == len(recognizers) == 1
    assert configs[0].properties["audio_logging"] == "false"
    assert configs[0].properties["log_file"] == ""
    pcm = np.frombuffer(captured, dtype="<i2")
    assert np.max(pcm) > 12000  # mixed .4 full scale; either source alone <= .3
    assert len(pcm) < RATE * 2  # one shared timeline, not concatenated tracks
    finals = session.transcript.snapshot()[1]
    assert [(i.source, i.speaker) for i in finals] == [("mixed", "Guest-1")]
    assert session.capture.capture.closed
    assert not session.capture.worker.is_alive()
    assert not session.capture.mixer.samples.any()
    assert all(b.closed and b.size == 0 for b in session.buffers.values())
    assert set(session.snapshot()["buffers"]) == {"mixed"}


def test_capture_start_failure_releases_everything_without_raw_exception():
    class Broken(PhysicalCapture):
        def start(self): raise RuntimeError("DO-NOT-LOG")
    sdk, _, _ = fake_sdk()
    session = MixedSession("Alex", sdk, Broken)
    session.start("westeurope", "synthetic-test-key", "de-DE", SELECTION)
    assert session.finished.wait(3)
    assert session.error and "DO-NOT-LOG" not in session.error
    assert session.capture.capture.closed
    assert not session.capture.mixer.samples.any()


def test_mixed_speakers_are_never_relabelled_as_local_name():
    transcript = Transcript("test", "Alex")
    transcript.add("mixed", "Guest-1", "Hallo")
    transcript.add("mixed", "Guest-2", "Hallo zurück")
    assert [s.speaker for s in transcript.snapshot()[1]] == ["Guest-1", "Guest-2"]
    transcript.clear()
    assert not transcript.add("mixed", "Guest-1", "late")


def test_output_backpressure_stops_mixer_instead_of_unbounded_growth():
    errors = []
    buffer = AudioBuffer(1280, errors.append)
    capture = MixedCapture(SELECTION, buffer, errors.append, PhysicalCapture)
    capture.open()
    capture.start()
    deadline = time.monotonic() + 2
    while not capture.failed.is_set() and time.monotonic() < deadline:
        time.sleep(.01)
    capture.close()
    assert capture.failed.is_set() and errors
    assert buffer.size == 0 and buffer.closed
    assert not capture.mixer.samples.any()


def test_idle_loopback_does_not_block_audio_or_stop():
    class MicOnly(PhysicalCapture):
        def start(self):
            remote = self.buffers.pop("loopback")
            super().start()
    sdk, _, recognizers = fake_sdk()
    session = MixedSession("Alex", sdk, MicOnly)
    session.start("westeurope", "synthetic-test-key", "de-DE", SELECTION)
    wait_running(session)
    time.sleep(.3)
    session.stop()
    assert session.finished.wait(3) and not session.error
    assert len(recognizers) == 1
