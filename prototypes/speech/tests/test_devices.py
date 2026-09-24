from types import SimpleNamespace
import pytest
from prototypes.speech.diagnostics import preferred_device, input_status
from prototypes.speech import capture


def device(name, index, default=False, loop=True):
    return dict(name=name, index=index, defaultSampleRate=48000, maxInputChannels=2,
                isLoopbackDevice=loop, isSystemDefault=default, hostApi=2)


def test_first_loopback_is_not_selected_instead_of_headset():
    choices=[device("Realtek",25),device("Sennheiser",28,True)]
    assert preferred_device(choices)==1


def test_explicit_selection_survives_reordering_and_changed_default():
    previous=device("Realtek",25)
    assert preferred_device([device("Sennheiser",25,True),device("Realtek",28)],previous)==1


@pytest.mark.parametrize("choices", [[], [device("Realtek",25)],
    [device("Realtek",25,True),device("Sennheiser",28,True)]])
def test_missing_or_ambiguous_default_requires_selection(choices):
    assert preferred_device(choices)==-1


def test_unplugged_device_does_not_silently_switch_to_another():
    assert preferred_device([device("Realtek",25,True)],device("Sennheiser",28))==-1


def test_device_enumeration_marks_default_loopback_without_opening_streams(monkeypatch):
    choices=[device("Sennheiser mic",22,loop=False),device("Realtek",25),device("Sennheiser",28)]
    class Audio:
        closed=False
        def get_host_api_info_by_type(self, kind): return {"index":2,"defaultInputDevice":22}
        def get_default_wasapi_loopback(self): return choices[2]
        def get_device_count(self): return len(choices)
        def get_device_info_by_index(self,index): return choices[index]
        def terminate(self): self.closed=True
    audio=Audio()
    monkeypatch.setattr(capture.pa,"PyAudio",lambda:audio)
    found=capture.devices()
    assert [d["name"] for d in found if d["isSystemDefault"]]==["Sennheiser mic","Sennheiser"]
    assert audio.closed


@pytest.mark.parametrize("metric, expected", [
    ({}, "Keine Audiodaten vom Gerät"),
    ({"frames":1024,"last_frame":5,"dbfs":-10},"Aktuell keine Audiodaten vom Gerät"),
    ({"frames":1024,"last_frame":10,"dbfs":-100},"Audiodaten kommen an, aber Stille / sehr leise"),
    ({"frames":1024,"last_frame":10,"dbfs":-25},"Audiosignal vorhanden"),
])
def test_signal_diagnostics_do_not_confuse_missing_frames_with_speech(metric,expected):
    assert input_status(metric,10)==expected
