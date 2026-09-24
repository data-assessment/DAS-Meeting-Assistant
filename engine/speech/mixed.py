"""Clock-paced, bounded RAM mix of two devices into one Azure stream.

Callback arrival times provide an approximate common clock. They are not sample-
accurate hardware timestamps. A 250 ms playout cushion absorbs callback jitter and
late delivery, pauses stay on the timeline, and timing problems are never fatal;
see TimelineMixer for how blocks are placed.
"""
import math
import threading
import time
import numpy as np
import soxr
from .session import Session

RATE = 16000
BLOCK = 320
DELAY = 0.25
RECOVERY_INTERVAL = 1.0  # retry opening devices while none is usable
RETURN_INTERVAL = 3.0    # look for the originally selected device while on a fallback
GAP = RATE // 50         # a block starting later than this after its predecessor keeps the pause
LEAD = RATE // 10        # a source further ahead of its arrival times is played faster
SPEEDUP = 0.05           # at most 5 % faster, well within what speech recognition tolerates
LOST_NOTICE = ("Audioaufnahme unterbrochen. Sie läuft weiter, sobald wieder ein "
               "Mikrofon und ein Wiedergabegerät verfügbar sind.")
DROP_NOTICE = ("Die Verarbeitung war mehrere Sekunden blockiert; ein Teil des Audios aus "
               "dieser Zeit fehlt in den Notizen. Die Aufnahme läuft weiter.")


def candidate_selections(preferred, current, devices):
    """Device selections to try in order: the originals, the devices last in use, the
    Windows defaults. Each source uses the Windows default where its device is not listed.

    A listed device can still fail to open (for example right after it is plugged in),
    so later candidates keep audio flowing instead of retrying only that device.
    """
    result = []
    for names in ({s: d["name"] for s, d in preferred.items()},
                  {s: d["name"] for s, d in current.items()},
                  dict.fromkeys(preferred)):
        chosen = {}
        for source, name in names.items():
            listed = [d for d in devices if bool(d.get("isLoopbackDevice")) == (source == "loopback")]
            chosen[source] = next((d for d in listed if d["name"] == name), None) or next(
                (d for d in listed if d.get("isSystemDefault")), None)
        if all(chosen.values()) and chosen not in result:
            result.append(chosen)
    return result


class TimelineMixer:
    """Unplayed samples per source, mixed only when they are played out.

    Placement relies on one physical fact: a block was captured no later than it
    arrived. A block starting clearly after its source's previous block opens a new
    segment, so real pauses stay on the timeline. When a source's newest segment would
    end after its arrival time, it is moved earlier, towards its previous segment or
    the playout cursor: a backlog delivered after a stall returns to where it was
    captured. A remaining lead (a device clock running fast, or a backlog longer than
    the playout cushion) is caught up by playing that source up to 5 % faster. Only
    when a source holds more unplayed audio than the capacity is its oldest audio dropped.
    """
    def __init__(self, origin, capacity=RATE * 3):
        self.origin = origin
        self.capacity = capacity
        self.cursor = 0
        self.last = 0
        self.next = {}    # source -> timeline index after its newest sample
        self.tracks = {}  # source -> [[start, samples], ...], unplayed and ascending
        self.lock = threading.Lock()
        self.closed = False
        self.late_blocks = 0         # arrived after their playout time
        self.caught_up_samples = 0   # removed by playing a leading source faster
        self.dropped_samples = 0     # beyond the buffer capacity

    @property
    def pending(self):
        with self.lock:
            return sum(len(values) for track in self.tracks.values() for _, values in track)

    def write(self, source, samples, end_time):
        values = np.clip(np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0), -1, 1).astype(np.float32)
        if not len(values):
            return
        arrival = round((end_time - self.origin) * RATE)
        observed = arrival - len(values)
        with self.lock:
            if self.closed:
                return
            track = self.tracks.setdefault(source, [])
            if track and observed <= self.next[source] + GAP:
                # The source's own stream continues: jitter or a delivered backlog.
                track[-1][1] = np.concatenate((track[-1][1], values))
            else:
                if source in self.next and observed < self.cursor:
                    self.late_blocks += 1
                track.append([max(observed, self.cursor), values])
            self._anchor(track, arrival)
            self._catch_up(track, arrival, len(values))
            self._bound(track)
            if track:
                self.next[source] = track[-1][0] + len(track[-1][1])
                self.last = max(self.last, self.next[source])

    def _anchor(self, track, arrival):
        """Move the newest segment earlier while it would end after it arrived."""
        start, values = track[-1]
        floor = self.cursor if len(track) < 2 else max(self.cursor, track[-2][0] + len(track[-2][1]))
        shift = min(start + len(values) - arrival, start - floor)
        if shift <= 0:
            return
        track[-1][0] = start - shift
        if len(track) > 1 and track[-1][0] == track[-2][0] + len(track[-2][1]):
            track[-2][1] = np.concatenate((track[-2][1], values))
            track.pop()

    def _catch_up(self, track, arrival, count):
        """Play the newest, still unplayed block faster while its source leads."""
        start, values = track[-1]
        lead = start + len(values) - arrival
        if lead <= LEAD:
            return
        keep = count - min(lead - LEAD, math.ceil(count * SPEEDUP))
        block = values[-count:]
        faster = np.interp(np.linspace(0, count - 1, keep), np.arange(count), block).astype(np.float32)
        track[-1][1] = np.concatenate((values[:-count], faster))
        self.caught_up_samples += count - keep

    def _bound(self, track):
        """Keep at most `capacity` unplayed samples per source; the oldest go first."""
        excess = sum(len(values) for _, values in track) - self.capacity
        while excess > 0:
            start, values = track[0]
            if len(values) <= excess:
                track.pop(0)
            else:
                track[0] = [start + excess, values[excess:]]
            self.dropped_samples += min(excess, len(values))
            excess -= len(values)

    def take_until(self, end):
        with self.lock:
            count = min(BLOCK, max(0, end - self.cursor))
            if not count or self.closed:
                return b""
            low, high = self.cursor, self.cursor + count
            mix = np.zeros(count, dtype=np.float32)
            for track in self.tracks.values():
                while track and track[0][0] < high:
                    start, values = track[0]
                    stop = min(start + len(values), high)
                    # Fixed -6 dB per source leaves headroom even during crosstalk.
                    mix[start - low:stop - low] += values[:stop - start] * 0.5
                    if start + len(values) <= high:
                        track.pop(0)
                    else:
                        track[0] = [high, values[high - start:]]
                        break
            self.cursor = high
            return np.clip(mix, -1, 1).astype("<f4").tobytes()

    def clear(self):
        with self.lock:
            self.closed = True
            self.tracks.clear()
            self.next.clear()


class MixInput:
    def __init__(self, source, device, owner):
        self.source, self.owner = source, owner
        self.rate = int(device["defaultSampleRate"])
        self.channels = int(device["maxInputChannels"])
        if not 8000 <= self.rate <= 192000 or not 1 <= self.channels <= 8:
            raise ValueError("Unsupported capture format")
        # Low-latency streaming resampling; avoids a large startup delay.
        self.resampler = soxr.ResampleStream(self.rate, RATE, 1, dtype="float32", quality="LQ")
        self.last_time = None

    def offer(self, data):
        try:
            if self.owner.stopping.is_set():
                return False
            values = np.frombuffer(data, dtype="<f4")
            if len(values) % self.channels:
                raise ValueError("Invalid capture block")
            mono = values.reshape(-1, self.channels).mean(axis=1)
            self.last_time = time.monotonic()
            result = self.resampler.resample_chunk(mono.astype("float32"))
            self.owner.mixer.write(self.source, result,
                                   self.last_time - self.resampler.delay() / RATE)
            return True
        except Exception as exc:
            # Invalid device audio: reopen the devices, keep the meeting.
            print(f"[audio] {self.source}: {type(exc).__name__}: {exc}; reopening devices")
            self.owner.device_lost()
            return False

    def finish(self):
        if self.resampler is not None:
            result = self.resampler.resample_chunk(np.empty(0, dtype="float32"), last=True)
            if self.last_time is not None:
                self.owner.mixer.write(self.source, result, self.last_time)
            self.resampler = None


class MixedCapture:
    """A lost or failing audio device never ends the meeting.

    The pump keeps sending silence while devices are missing, so Azure and the
    meeting timeline continue. A supervisor reopens the originally selected devices,
    or the current Windows defaults, and returns to the originals once Windows
    reports them again. PortAudio only re-enumerates devices after every PyAudio
    instance is terminated, so each reopen fully closes the previous capture.
    """
    def __init__(self, selection, output, fail, capture_factory=None, device_list=None, present_names=None):
        if capture_factory is None or device_list is None or present_names is None:
            from . import capture
            capture_factory = capture_factory or capture.Capture
            device_list = device_list or capture.devices
            present_names = present_names or capture.present_device_names
        self.factory, self.device_list, self.present_names = capture_factory, device_list, present_names
        self.output, self.fail = output, fail
        self.preferred = dict(selection)
        self.current = dict(selection)
        self.stopping = threading.Event()
        self.halt = threading.Event()
        self.failed = threading.Event()
        self.lost = threading.Event()
        self.lock = threading.Lock()
        self.worker = self.supervisor = None
        self.mixer = None
        self.closed = False
        self.notice = ""
        self.reported_drops = 0
        self.interruptions = 0
        self.silent_seconds = 0.0
        self.silent_since = None
        self.next_attempt = self.next_return = 0.0
        self.return_interval = RETURN_INTERVAL
        self.mix_metrics = {"dbfs": -100.0, "frames": 0, "last_frame": None}
        self.inputs = self._inputs(selection)
        self.capture = capture_factory(selection, self.inputs, self.device_lost)

    def _inputs(self, selection):
        return {source: MixInput(source, device, self) for source, device in selection.items()}

    @property
    def metrics(self):
        capture = self.capture
        return {**(capture.metrics if capture is not None else {}), "mixed": self.mix_metrics}

    def open(self):
        self.capture.open()

    def start(self):
        self.mixer = TimelineMixer(time.monotonic())
        self.capture.start()
        self.worker = threading.Thread(target=self._pump, daemon=True)
        self.worker.start()
        self.supervisor = threading.Thread(target=self._supervise, daemon=True)
        self.supervisor.start()

    def device_lost(self, _message=""):
        # Called from device callbacks; the supervisor thread does the reopening.
        self.lost.set()

    def _supervise(self):
        while not self.halt.wait(0.25):
            try:
                self._supervise_once(time.monotonic())
            except Exception:
                pass  # Supervision must never end the meeting; retry on the next tick.

    def _supervise_once(self, now):
        if self.mixer is not None and self.mixer.dropped_samples > self.reported_drops:
            self.reported_drops = self.mixer.dropped_samples
            print(f"[audio] mix buffer exceeded: {self.reported_drops * 1000 // RATE} ms dropped so far")
            self.notice = DROP_NOTICE
        capture = self.capture
        if capture is not None and not self.lost.is_set() and (
                not hasattr(capture, "healthy") or capture.healthy()):
            if self._on_fallback() and now >= self.next_return:
                if self._preferred_present():
                    self._detach(lost=False)
                    attached = self._attach()
                    if not attached:
                        self.notice = LOST_NOTICE
                    returned = attached and not self._on_fallback()
                    # Windows can list a device before PortAudio can open it; back off.
                    self.return_interval = RETURN_INTERVAL if returned else min(self.return_interval * 2, 60.0)
                self.next_return = now + self.return_interval
            return
        if capture is not None:
            self._detach(lost=True)
        if now >= self.next_attempt:
            self.next_attempt = now + RECOVERY_INTERVAL
            self._attach()

    def _on_fallback(self):
        return any(self.current[s]["name"] != d["name"] for s, d in self.preferred.items())

    def _preferred_present(self):
        names = self.present_names()
        return bool(names) and any(self.current[s]["name"] != d["name"] and d["name"] in names
                                   for s, d in self.preferred.items())

    def _detach(self, lost):
        with self.lock:
            capture, self.capture = self.capture, None
            # A detached source has no playout tail to drain at close.
            self.inputs = {}
        if capture is None:
            return
        if self.silent_since is None:
            self.silent_since = time.monotonic()
        if lost:
            print("[audio] capture interrupted; reopening devices")
            self.interruptions += 1
            self.return_interval = RETURN_INTERVAL
            self.notice = LOST_NOTICE
        try:
            capture.close()
        except Exception:
            pass

    def _attach(self):
        # PortAudio only lists a newly plugged device after the previous capture is
        # closed, so a return cannot be opened before breaking the running fallback.
        # The devices last in use are therefore the second candidate: a failed return
        # reopens them right away.
        try:
            selections = candidate_selections(self.preferred, self.current, self.device_list())
        except Exception:
            return False
        for chosen in selections:
            if self.halt.is_set():
                return False
            if self._open(chosen):
                break
        else:
            return False
        if self.silent_since is not None:
            self.silent_seconds += time.monotonic() - self.silent_since
            self.silent_since = None
        print(f"[audio] capture reopened: mic {self.current['mic']['name']!r}, "
              f"playback {self.current['loopback']['name']!r}")
        if self._on_fallback():
            self.notice = ("Audiogerät gewechselt. Die Aufnahme läuft weiter mit Mikrofon „"
                           + self.current["mic"]["name"] + "“ und Wiedergabe „"
                           + self.current["loopback"]["name"].removesuffix(" [Loopback]") + "“.")
        elif self.interruptions:
            self.notice = "Audioaufnahme nach kurzer Unterbrechung fortgesetzt."
        return True

    def _open(self, chosen):
        try:
            inputs = self._inputs(chosen)
            capture = self.factory(chosen, inputs, self.device_lost)
        except Exception:
            return False
        try:
            capture.open()
            with self.lock:
                if self.halt.is_set():
                    raise RuntimeError("Capture is closing")
                self.lost.clear()
                capture.start()
                self.capture, self.inputs, self.current = capture, inputs, chosen
        except Exception:
            try:
                capture.close()
            except Exception:
                pass
            return False
        return True

    def summary(self):
        """Capture note kept with the finished meeting; empty when nothing was lost."""
        notes = []
        if self.interruptions and self.silent_since is not None:
            notes.append("Audioaufnahme während des Meetings unterbrochen; danach war kein Audiogerät "
                         "verfügbar. Die Notizen reichen nur bis zu dieser Stelle.")
        elif self.interruptions:
            notes.append(f"Audioaufnahme während des Meetings {self.interruptions}× unterbrochen; ca. "
                         f"{max(1, round(self.silent_seconds))} s ohne Ton. "
                         "Die Notizen umfassen die Zeit davor und danach.")
        dropped = self.mixer.dropped_samples if self.mixer is not None else 0
        if dropped:
            notes.append(f"Wegen einer Verzögerung fehlen ca. {max(1, round(dropped / RATE))} s Audio.")
        return " ".join(notes)

    def _emit_until(self, end):
        while data := self.mixer.take_until(end):
            if not self.output.offer(data):
                self.failed.set()
                return False
            values = np.frombuffer(data, dtype="<f4")
            rms = float(np.sqrt(np.mean(values * values)))
            self.mix_metrics = {"dbfs": 20 * math.log10(max(rms, 1e-5)),
                                "frames": self.mix_metrics["frames"] + len(values),
                                "last_frame": time.monotonic()}
        return True

    def _pump(self):
        try:
            while not self.stopping.wait(0.01):
                end = int((time.monotonic() - self.mixer.origin - DELAY) * RATE)
                if not self._emit_until(end):
                    return
        except Exception:
            self.failed.set()
            self.fail("Audio-Mix fehlgeschlagen; Test wird beendet.")

    def healthy(self):
        # Device health is the supervisor's job; only a broken mix/output is fatal.
        return not self.failed.is_set()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.halt.set()
        if self.supervisor is not None:
            self.supervisor.join(5)
        try:
            # First stop device callbacks, then stop playout and drain its tail.
            with self.lock:
                capture = self.capture
            if capture is not None:
                capture.close()
        finally:
            self.stopping.set()
            if self.worker is not None:
                self.worker.join(2)
            try:
                if self.worker is not None and self.worker.is_alive():
                    raise RuntimeError("Mixer did not stop")
                if self.mixer is not None and not self.output.closed:
                    for sink in self.inputs.values():
                        sink.finish()
                    end = max(self.mixer.last, math.ceil((time.monotonic() - self.mixer.origin) * RATE))
                    self._emit_until(end)
            finally:
                for sink in self.inputs.values():
                    sink.resampler = None
                if self.mixer is not None:
                    mixer = self.mixer
                    if mixer.late_blocks or mixer.caught_up_samples or mixer.dropped_samples:
                        print(f"[audio] mix timing: {mixer.late_blocks} late blocks, "
                              f"{mixer.caught_up_samples * 1000 // RATE} ms caught up by faster playout, "
                              f"{mixer.dropped_samples * 1000 // RATE} ms dropped beyond the buffer")
                    self.mixer.clear()


class MixedSession(Session):
    TEST_SECONDS = 5 * 60

    def __init__(self, local_name, sdk_module=None, capture_factory=None, device_list=None, present_names=None):
        super().__init__(local_name, sdk_module, capture_factory)
        self._device_list, self._present_names = device_list, present_names

    def transcript_source(self, source):
        return "mixed"

    @property
    def notice(self):
        """Live device-change note while the meeting continues."""
        return getattr(self.capture, "notice", "")

    def capture_warning(self):
        return self.capture.summary() if isinstance(self.capture, MixedCapture) else ""

    def _run(self, region, key, language, selection):
        physical_factory = self._capture_factory
        def factory(_selection, buffers, fail):
            return MixedCapture(selection, buffers["loopback"], fail, physical_factory,
                                self._device_list, self._present_names)
        self._capture_factory = factory
        try:
            # Reuse Azure lifecycle/EOF handling with exactly one mono transcriber.
            super()._run(region, key, language,
                         {"loopback": {"defaultSampleRate": RATE, "maxInputChannels": 1}})
        finally:
            self._capture_factory = physical_factory

    def snapshot(self):
        result = super().snapshot()
        result["buffers"] = {"mixed": value for value in result["buffers"].values()}
        result["phase"] += " | Azure Mix: 1 Mono-Stream mit Sprechertrennung"
        return result
