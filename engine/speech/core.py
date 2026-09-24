"""Bounded, file-free audio and transcript state."""
from collections import deque
from dataclasses import dataclass
import threading
import time
import numpy as np
import soxr

class AudioBuffer:
    """Capture never waits for the cloud. Overflow ends the stream, never drops silently."""
    def __init__(self, capacity, on_error=lambda reason: None):
        if capacity <= 0:
            raise ValueError("Positive buffer capacity required")
        self.capacity = capacity
        self._chunks = deque()
        self._size = 0
        self.peak = 0
        self.closed = False
        self.failed = False
        self._condition = threading.Condition()
        self.on_error = on_error

    def offer(self, data):
        failed = False
        with self._condition:
            if self.closed:
                return False
            if not data:
                return True
            if self._size + len(data) > self.capacity:
                self.failed = self.closed = True
                self._chunks.clear()
                self._size = 0
                failed = True
            else:
                self._chunks.append(bytes(data))
                self._size += len(data)
                self.peak = max(self.peak, self._size)
            self._condition.notify_all()
        if failed:
            self.on_error("Audiopuffer voll; Sitzung wird beendet.")
        return not failed

    def take(self):
        with self._condition:
            while not self._chunks and not self.closed:
                self._condition.wait()
            if self._chunks:
                data = self._chunks.popleft()
                self._size -= len(data)
                return data
            return b""

    def finish(self):
        with self._condition:
            self.closed = True
            self._condition.notify_all()

    def discard(self):
        with self._condition:
            self.closed = True
            self._chunks.clear()
            self._size = 0
            self._condition.notify_all()

    @property
    def size(self):
        with self._condition:
            return self._size

class PcmReader:
    """Convert one source continuously to headerless mono 16 kHz / signed PCM16."""
    def __init__(self, buffer, rate, channels):
        if not 8000 <= rate <= 192000 or not 1 <= channels <= 8:
            raise ValueError("Unsupported capture format")
        self.buffer = buffer
        self.channels = channels
        self.resampler = soxr.ResampleStream(rate, 16000, 1, dtype="float32")
        self.pending = bytearray()
        self.eof = False
        self.closed = False
        self._read_lock = threading.Lock()

    def read(self, target):
        with self._read_lock:
            return self._read(target)

    def _read(self, target):
        if self.closed or not len(target):
            return 0
        # Return available data promptly; zero denotes EOF to Azure.
        while not self.pending and not self.eof and not self.closed:
            data = self.buffer.take()
            if self.closed:
                return 0
            if data:
                samples = np.frombuffer(data, dtype="<f4")
                if len(samples) % self.channels:
                    self.buffer.on_error("Ungültiges Audioformat; Sitzung wird beendet.")
                    self.closed = True
                    self.buffer.discard()
                    self.pending.clear()
                    return 0
                mono = samples.reshape(-1, self.channels).mean(axis=1)
                output = self.resampler.resample_chunk(mono.astype("float32"), last=False)
            else:
                self.eof = True
                output = self.resampler.resample_chunk(np.empty(0, dtype="float32"), last=True)
            output = np.nan_to_num(output, nan=0.0, posinf=1.0, neginf=-1.0)
            self.pending.extend((np.clip(output, -1, 1) * 32767).astype("<i2").tobytes())
        count = min(len(target), len(self.pending))
        target[:count] = self.pending[:count]
        del self.pending[:count]
        return count

    def close(self):
        self.closed = True
        self.buffer.discard()
        with self._read_lock:
            self.pending.clear()

@dataclass(frozen=True)
class Segment:
    session_id: str
    source: str
    speaker: str
    audio_offset: float
    received_at: float
    text: str

class Transcript:
    def __init__(self, session_id, local_name, max_chars=200000, max_segments=1000):
        self.session_id = session_id
        self.local_name = local_name
        self.max_chars = max_chars
        self.max_segments = max_segments
        self._lock = threading.Lock()
        self.finals = []
        self.partials = {}
        self.characters = 0
        self.revision = 0
        self.closed = False
        self.started = time.monotonic()

    def add(self, source, speaker, text, offset=0, final=True):
        if source not in ("mic", "loopback", "mixed"):
            raise ValueError("Unknown audio source")
        speaker = self.local_name + " (Mikrofon)" if source == "mic" else (speaker or "Unknown")
        speaker = speaker[:120]
        with self._lock:
            if self.closed:
                return False
            if len(text) > 4096 or (final and
                    (self.characters + len(text) > self.max_chars or len(self.finals) >= self.max_segments)):
                raise ValueError("Transkriptlimit erreicht; Sitzung wird beendet.")
            item = Segment(self.session_id, source, speaker, float(offset),
                           time.monotonic() - self.started, text)
            if final:
                self.partials.pop(source, None)
                if text:
                    self.finals.append(item)
                    self.characters += len(text)
            else:
                self.partials[source] = item
            self.revision += 1
            return True

    def snapshot(self):
        with self._lock:
            return self.revision, list(self.finals), dict(self.partials)

    def seal(self):
        with self._lock:
            self.closed = True
            self.partials.clear()
            self.revision += 1

    def clear(self):
        with self._lock:
            self.closed = True
            self.finals.clear()
            self.partials.clear()
            self.characters = 0
            self.revision += 1
