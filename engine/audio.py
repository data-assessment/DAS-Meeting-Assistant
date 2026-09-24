"""Meeting audio capture: mic + WASAPI loopback -> one 16 kHz mono WAV.

Two sources make up a meeting's audio on Windows:
  - the microphone (you), and
  - the default render device's *loopback* (remote participants — what you hear).

Capture uses pyaudiowpatch (a PyAudio fork with WASAPI loopback support; plain
sounddevice/PyAudio can't do loopback). On stop() each source is downmixed to mono,
resampled to 16 kHz (soxr), normalized, mixed and written as 16-bit PCM — 16 kHz
mono is what the transcription step expects.

Loopback grabs *all* system audio (not just Teams); per-process capture is a later
refinement. Recording meetings may require participant consent — a deployment
obligation, not handled here.

Import is guarded: if pyaudiowpatch/numpy or a loopback device is missing, the
recorder reports unavailable and the app keeps running without audio.
"""
import datetime
import os
import threading
import time
import wave

import config

try:
    import numpy as np
    import pyaudiowpatch as pyaudio
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - depends on platform/install
    np = None
    pyaudio = None
    _IMPORT_ERROR = exc

try:
    import soxr  # high-quality, anti-aliased resampling
except Exception:  # pragma: no cover
    soxr = None

# Below this peak a source is treated as silent (don't amplify noise). ~ -50 dBFS.
_SILENCE_PEAK = 0.003
_FRAMES_PER_BUFFER = 2048


def _slug(title: str) -> str:
    return "".join(c for c in title if c.isalnum() or c in " -_").strip().replace(" ", "_") or "meeting"


class MeetingRecorder:
    """Records one meeting to a WAV. Create per meeting; start() then stop()."""

    def __init__(self, title: str, started_at: datetime.datetime) -> None:
        self._title = title
        self._started_at = started_at
        self._lock = threading.Lock()
        self._mic_chunks: list = []
        self._loop_chunks: list = []
        self._mic_rate: int | None = None
        self._loop_rate: int | None = None
        # Frames captured per source. Counted as they arrive so the recording's
        # duration can be answered without touching the audio itself.
        self._frames = {"mic": 0, "loopback": 0}
        self._pa = None
        self._streams: list = []
        self._error: str | None = None
        self._frame_sink = None  # optional callable(source, mono_float, src_rate) for realtime
        self._activity_started = time.monotonic()
        self._last_frame_at = {"mic": None, "loopback": None}
        self._last_active_at = {"mic": None, "loopback": None}
        self._last_dbfs = {"mic": None, "loopback": None}

    def set_frame_sink(self, sink) -> None:
        """Tap live frames (e.g. for realtime streaming) in addition to the WAV."""
        self._frame_sink = sink

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> bool:
        """Open the mic + loopback streams. Returns True if at least one opened."""
        if _IMPORT_ERROR is not None:
            self._error = f"audio libs unavailable: {_IMPORT_ERROR}"
            return False
        try:
            self._pa = pyaudio.PyAudio()
        except Exception as exc:
            self._error = f"PyAudio init failed: {exc}"
            return False

        self._open_source("mic", self._mic_chunks, self._resolve_mic, is_mic=True)
        self._open_source("loopback", self._loop_chunks, self._resolve_loopback, is_mic=False)
        if not self._streams:
            self._error = self._error or "no audio sources could be opened"
            return False
        return True

    def stop(self) -> str | None:
        """Stop capture, write the WAV, return its path (or None if nothing captured)."""
        self._close_streams()
        return self._write_wav()

    def _close_streams(self) -> None:
        for stream in self._streams:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        self._streams.clear()
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

    @property
    def error(self) -> str | None:
        return self._error

    def activity_snapshot(self) -> dict:
        """Rolling audio activity for user-facing stop suggestions."""
        now = time.monotonic()
        with self._lock:
            frame_times = [
                t for t in self._last_frame_at.values() if t is not None
            ]
            active_times = [
                t for t in self._last_active_at.values() if t is not None
            ]
            sources = {
                name: {
                    "dbfs": round(dbfs, 1) if dbfs is not None else None,
                    "secondsSinceFrame": (
                        round(now - frame_at, 1) if frame_at else None
                    ),
                    "secondsSinceActive": (
                        round(now - active_at, 1) if active_at else None
                    ),
                    "active": active_at is not None and now - active_at < 2.0,
                }
                for name, dbfs in self._last_dbfs.items()
                for frame_at in [self._last_frame_at[name]]
                for active_at in [self._last_active_at[name]]
            }
        if not frame_times:
            silent_for = now - self._activity_started
            return {
                "hasFrames": False,
                "silentForSeconds": round(silent_for, 1),
                "thresholdDbfs": config.AUDIO_ACTIVITY_DBFS,
                "sources": sources,
            }
        last_active = (
            max(active_times) if active_times else self._activity_started
        )
        return {
            "hasFrames": True,
            "silentForSeconds": round(now - last_active, 1),
            "thresholdDbfs": config.AUDIO_ACTIVITY_DBFS,
            "sources": sources,
        }

    # -- device resolution ------------------------------------------------- #
    def _resolve_mic(self) -> dict:
        return self._pa.get_default_input_device_info()

    def _resolve_loopback(self) -> dict:
        # The loopback "device" matching the default speaker (remote audio).
        return self._pa.get_default_wasapi_loopback()

    # -- stream setup ------------------------------------------------------ #
    def _open_source(self, label: str, buf: list, resolve, is_mic: bool) -> None:
        try:
            info = resolve()
            idx = int(info["index"])
            rate = int(info["defaultSampleRate"])
            channels = max(1, min(int(info["maxInputChannels"]), 2))
            print(f"[audio] {label} device: {info['name']} ({channels}ch @ {rate}Hz)")

            def cb(in_data, _frame_count, _time_info, _status,
                   _buf=buf, _ch=channels, _label=label, _rate=rate):
                arr = np.frombuffer(in_data, dtype=np.float32).reshape(-1, _ch)
                mono = arr.mean(axis=1) if _ch > 1 else arr[:, 0].copy()
                with self._lock:
                    _buf.append(arr.copy())
                    self._frames[_label] += len(arr)
                    self._update_activity(_label, mono)
                sink = self._frame_sink
                if sink is not None:
                    try:
                        sink(_label, mono, _rate)
                    except Exception:
                        pass
                return (None, pyaudio.paContinue)

            stream = self._pa.open(
                format=pyaudio.paFloat32, channels=channels, rate=rate, input=True,
                input_device_index=idx, frames_per_buffer=_FRAMES_PER_BUFFER,
                stream_callback=cb,
            )
            stream.start_stream()
            self._streams.append(stream)
            if is_mic:
                self._mic_rate = rate
            else:
                self._loop_rate = rate
        except Exception as exc:
            note = f"{label} capture failed: {type(exc).__name__}: {exc}"
            print("[audio]", note)
            self._error = f"{self._error}; {note}" if self._error else note

    def _update_activity(self, label: str, mono: "np.ndarray") -> None:
        now = time.monotonic()
        rms = float(np.sqrt(np.mean(mono ** 2))) if len(mono) else 0.0
        dbfs = 20 * np.log10(rms + 1e-9)
        self._last_frame_at[label] = now
        self._last_dbfs[label] = dbfs
        if dbfs >= config.AUDIO_ACTIVITY_DBFS:
            self._last_active_at[label] = now

    # -- mixing / output --------------------------------------------------- #
    def _chunks_for(
        self,
        chunks: list,
        src_rate: int | None,
        start_seconds: float,
        end_seconds: float | None,
    ) -> list:
        """The captured chunks overlapping [start, end), trimmed to it.

        Slicing here rather than after mixing is the whole point: a ten-minute
        slice of a three-hour meeting should cost ten minutes of memory, not
        three hours of it.
        """
        if not chunks or src_rate is None:
            return []
        first = max(0, int(start_seconds * src_rate))
        last = None if end_seconds is None else int(end_seconds * src_rate)
        picked, offset = [], 0
        for chunk in chunks:
            length = len(chunk)
            if last is not None and offset >= last:
                break
            if offset + length > first:
                stop = length if last is None else min(length, last - offset)
                picked.append(chunk[max(0, first - offset):stop])
            offset += length
        return picked

    def _collapse(self, chunks: list, src_rate: int | None) -> "np.ndarray":
        """Concatenate chunks -> mono float32 resampled to the target rate."""
        if not chunks or src_rate is None:
            return np.zeros(0, dtype=np.float32)
        data = np.concatenate(chunks, axis=0)
        if data.ndim > 1:
            data = data.mean(axis=1)  # downmix to mono
        target = config.AUDIO_SAMPLE_RATE
        if src_rate != target and len(data) > 1:
            if soxr is not None:
                data = soxr.resample(data, src_rate, target).astype(np.float32)
            else:  # crude fallback: aliasing-prone, but better than nothing
                n_out = int(len(data) * target / src_rate)
                old = np.linspace(0.0, 1.0, num=len(data), endpoint=False)
                new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
                data = np.interp(new, old, data).astype(np.float32)
        return data

    def _normalize_source(self, data: "np.ndarray") -> "np.ndarray":
        """Bring one source up to the target peak so a quiet remote isn't drowned by
        a loud mic. Near-silent sources are left alone (don't amplify noise). No
        clipping here — that happens after mixing."""
        if not config.AUDIO_NORMALIZE or len(data) == 0:
            return data
        peak = float(np.max(np.abs(data)))
        if peak < _SILENCE_PEAK:
            return data
        target = 10 ** (config.AUDIO_TARGET_PEAK_DBFS / 20.0)
        gain = min(target / peak, config.AUDIO_MAX_GAIN)
        return data * gain

    def _fit_peak(self, mix: "np.ndarray") -> "np.ndarray":
        """Scale the whole mix to the target peak (preserves the per-source balance)."""
        if len(mix) == 0:
            return mix
        peak = float(np.max(np.abs(mix)))
        if peak < 1e-5:
            return mix
        target = 10 ** (config.AUDIO_TARGET_PEAK_DBFS / 20.0)
        return np.clip(mix * (target / peak), -1.0, 1.0)

    @staticmethod
    def _level(name: str, data: "np.ndarray") -> str:
        if len(data) == 0:
            return f"{name}: no samples"
        peak = float(np.max(np.abs(data)))
        rms = float(np.sqrt(np.mean(data ** 2)))
        dbfs = 20 * np.log10(rms + 1e-9)
        secs = len(data) / config.AUDIO_SAMPLE_RATE
        return f"{name}: {secs:.1f}s peak={peak:.3f} rms={dbfs:.1f}dBFS"

    def _mixed_audio(self, log_levels: bool = False) -> "np.ndarray":
        return self._mixed_range(0.0, None, log_levels=log_levels)

    def _mixed_range(
        self,
        start_seconds: float = 0.0,
        end_seconds: float | None = None,
        log_levels: bool = False,
    ) -> "np.ndarray":
        """Mix one span of the recording, touching only the audio it covers.

        The lock is held just long enough to pick out the chunks. They are never
        mutated once appended, so the concatenate and the resample - the
        expensive part - run outside it and no longer stall the capture
        callbacks while a slice is being cut.
        """
        with self._lock:
            mic_parts = self._chunks_for(
                self._mic_chunks, self._mic_rate, start_seconds, end_seconds)
            loop_parts = self._chunks_for(
                self._loop_chunks, self._loop_rate, start_seconds, end_seconds)
            mic_rate, loop_rate = self._mic_rate, self._loop_rate
        mic = self._collapse(mic_parts, mic_rate)
        loop = self._collapse(loop_parts, loop_rate)
        if len(mic) == 0 and len(loop) == 0:
            return np.zeros(0, dtype=np.float32)

        if log_levels:
            # Log raw per-source levels first — reveals whether the remote (loopback)
            # was captured at all, vs captured but quiet.
            print(
                "[audio]",
                self._level("mic", mic),
                "|",
                self._level("loopback", loop),
            )

        # Balance each side independently so a quiet remote matches a loud mic,
        # then mix and fit the whole thing to the target peak.
        mic = self._normalize_source(mic)
        loop = self._normalize_source(loop)
        n = max(len(mic), len(loop))
        mix = np.zeros(n, dtype=np.float32)
        if len(mic):
            mix[:len(mic)] += mic
        if len(loop):
            mix[:len(loop)] += loop
        return self._fit_peak(mix)

    def captured_seconds(self) -> float:
        """Duration captured so far, counted rather than measured.

        Background transcription polls this every few seconds. Deriving it from
        a full mix meant re-concatenating, resampling and normalising the entire
        meeting on every poll - hundreds of megabytes of allocation per call an
        hour in, growing with the meeting. That is what exhausted memory and
        took the event loop down with it. Frame counts give the same answer:
        resampling preserves duration, and the mix is as long as its longest
        source.
        """
        with self._lock:
            counts = dict(self._frames)
        seconds = 0.0
        for label, rate in (("mic", self._mic_rate),
                            ("loopback", self._loop_rate)):
            if rate:
                seconds = max(seconds, counts.get(label, 0) / rate)
        return seconds

    def write_segment_wav(self, start_seconds: float, end_seconds: float,
                          index: int) -> str | None:
        """Write a mixed WAV slice without stopping capture.

        The slice is normalised against its own peak rather than the whole
        recording's, since the rest of the recording is deliberately never
        touched. These chunks feed speech-to-text, which cares about being
        audible and not about matching the final WAV's gain.
        """
        if _IMPORT_ERROR is not None:
            return None
        mix = self._mixed_range(start_seconds, end_seconds)
        if len(mix) == 0:
            return None
        pcm = (mix * 32767.0).astype("<i2")

        os.makedirs(config.AUDIO_DIR, exist_ok=True)
        stem = f"{self._started_at:%Y-%m-%d_%H%M}_{_slug(self._title)}"
        fname = f"{stem}.part{index:03d}.wav"
        path = os.path.join(config.AUDIO_DIR, fname)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(config.AUDIO_SAMPLE_RATE)
            w.writeframes(pcm.tobytes())
        return path

    def _write_wav(self) -> str | None:
        if _IMPORT_ERROR is not None:
            return None
        mix = self._mixed_audio(log_levels=True)
        if len(mix) == 0:
            return None
        pcm = (mix * 32767.0).astype("<i2")

        os.makedirs(config.AUDIO_DIR, exist_ok=True)
        fname = f"{self._started_at:%Y-%m-%d_%H%M}_{_slug(self._title)}.wav"
        path = os.path.join(config.AUDIO_DIR, fname)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(config.AUDIO_SAMPLE_RATE)
            w.writeframes(pcm.tobytes())
        return path
