"""WASAPI sources into bounded RAM buffers. No dependency on MeetingRecorder."""
import math
import time
import numpy as np
import pyaudiowpatch as pa

def devices():
    audio = pa.PyAudio()
    try:
        host = audio.get_host_api_info_by_type(pa.paWASAPI)
        try:
            default_loopback = audio.get_default_wasapi_loopback()["index"]
        except (OSError, LookupError, ValueError):
            default_loopback = None
        result = []
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            if info["hostApi"] == host["index"] and info["maxInputChannels"] > 0:
                item = dict(info)
                item["isSystemDefault"] = item["index"] == (
                    default_loopback if item.get("isLoopbackDevice") else host["defaultInputDevice"])
                result.append(item)
        return result
    finally:
        audio.terminate()

_ENDPOINTS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"
_DEVICE_DESCRIPTION = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
_INTERFACE_NAME = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"

def present_device_names():
    """Names of the active Windows endpoints, spelled like ``devices()``.

    PortAudio keeps its device list until every PyAudio instance is terminated,
    so it cannot notice a headset being plugged back in while capture runs.
    Windows keeps this registry list current. None means it is unavailable.
    """
    try:
        import winreg
        names = set()
        for kind, suffix in (("Capture", ""), ("Render", " [Loopback]")):
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _ENDPOINTS + "\\" + kind) as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    endpoint = winreg.EnumKey(root, index)
                    try:
                        with winreg.OpenKey(root, endpoint) as key:
                            if winreg.QueryValueEx(key, "DeviceState")[0] != 1:  # DEVICE_STATE_ACTIVE
                                continue
                        with winreg.OpenKey(root, endpoint + r"\Properties") as key:
                            names.add(f"{winreg.QueryValueEx(key, _DEVICE_DESCRIPTION)[0]} "
                                      f"({winreg.QueryValueEx(key, _INTERFACE_NAME)[0]}){suffix}")
                    except OSError:
                        continue
        return names
    except (ImportError, OSError):
        return None

class Capture:
    def __init__(self, selection, buffers, fail):
        self.selection, self.buffers, self.fail = selection, buffers, fail
        self.audio = pa.PyAudio()
        self.streams = []
        self.metrics = {source: {"dbfs": -100.0, "frames": 0, "last_frame": None}
                        for source in selection}

    def open(self):
        try:
            for source, info in self.selection.items():
                current = self.audio.get_device_info_by_index(int(info["index"]))
                if (current["name"] != info["name"] or
                    current["defaultSampleRate"] != info["defaultSampleRate"] or
                    current["maxInputChannels"] != info["maxInputChannels"] or
                    bool(current.get("isLoopbackDevice")) != (source == "loopback")):
                    raise ValueError("Audio device changed")
                channels = int(current["maxInputChannels"])
                rate = int(current["defaultSampleRate"])
                if not 1 <= channels <= 8 or not 8000 <= rate <= 192000:
                    raise ValueError("Unsupported capture format")
                callback = self._callback(source, channels)
                stream = self.audio.open(format=pa.paFloat32, channels=channels, rate=rate,
                    input=True, input_device_index=int(current["index"]),
                    frames_per_buffer=1024, stream_callback=callback, start=False)
                self.streams.append(stream)
        except Exception:
            self.close()
            raise

    def _callback(self, source, channels):
        def callback(data, count, time_info, status):
            try:
                # Overflow/underflow flags (``status``) mean samples were lost, but the
                # stream continues; the mixer places later audio on the wall clock.
                if len(data) != count * channels * 4:
                    raise ValueError("Invalid audio frame size")
                values = np.frombuffer(data, dtype="<f4")
                rms = float(np.sqrt(np.mean(values * values))) if len(values) else 0
                self.metrics[source] = {"dbfs": 20 * math.log10(max(rms, 1e-5)),
                                        "frames": self.metrics[source]["frames"] + count,
                                        "last_frame": time.monotonic()}
                if not self.buffers[source].offer(data):
                    return None, pa.paAbort
                return None, pa.paContinue
            except Exception:
                self.fail("Audioaufnahme fehlgeschlagen; Sitzung wird beendet.")
                return None, pa.paAbort
        return callback

    def start(self):
        for stream in self.streams:
            stream.start_stream()

    def healthy(self):
        try:
            return bool(self.streams) and all(stream.is_active() for stream in self.streams)
        except Exception:
            return False

    def close(self):
        for stream in self.streams:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        self.streams.clear()
        if self.audio is not None:
            self.audio.terminate()
            self.audio = None
