import threading
import time
from types import SimpleNamespace as NS
import numpy as np
import pytest
from prototypes.speech.session import Session

class Signal:
    def __init__(self): self.handlers=[]
    def connect(self,fn): self.handlers.append(fn)
    def disconnect_all(self): self.handlers.clear()
    def emit(self,event):
        for fn in self.handlers[:]: fn(event)

class Future:
    def get(self): pass

def fake_sdk():
    configs=[]
    recognizers=[]
    class Config:
        def __init__(self,**kwargs): self.properties={};configs.append(self)
        def set_property(self,key,value): self.properties[key]=value
    class Pull:
        def __init__(self): pass
    class Stream:
        def __init__(self,callback,format): self.callback=callback
    class Recognizer:
        def __init__(self,speech_config,audio_config):
            self.stream=audio_config.stream
            for name in ("transcribed","transcribing","recognized","recognizing","session_stopped","canceled"):
                setattr(self,name,Signal())
            recognizers.append(self)
        def _start(self,final_signal):
            def consume():
                target=memoryview(bytearray(1024))
                while self.stream.callback.read(target):
                    pass
                final_signal.emit(NS(result=NS(reason="final",text="Letzte Wörter",speaker_id="Guest-1",offset=2500000)))
                self.canceled.emit(NS(reason="eof"))
                self.session_stopped.emit(NS())
            self.thread=threading.Thread(target=consume,daemon=True)
            self.thread.start()
            return Future()
        def start_transcribing_async(self): return self._start(self.transcribed)
        def start_continuous_recognition_async(self): return self._start(self.recognized)
        def stop_transcribing_async(self): return Future()
        def stop_continuous_recognition_async(self): return Future()
    sdk=NS(SpeechConfig=Config,SpeechRecognizer=Recognizer,
           transcription=NS(ConversationTranscriber=Recognizer),
           audio=NS(PullAudioInputStreamCallback=Pull,PullAudioInputStream=Stream,
                    AudioStreamFormat=lambda **kwargs:kwargs,AudioConfig=lambda **kwargs:NS(**kwargs)),
           PropertyId=NS(SpeechServiceResponse_DiarizeIntermediateResults="diarize",
                         SpeechServiceConnection_EnableAudioLogging="audio_logging",
                         Speech_LogFilename="log_file"),
           ResultReason=NS(RecognizedSpeech="final",RecognizingSpeech="partial"),
           CancellationReason=NS(Error="error",EndOfStream="eof"))
    return sdk,configs,recognizers

class Capture:
    def __init__(self,selection,buffers,fail):
        self.buffers=buffers;self.closed=False;self.metrics={}
    def open(self): pass
    def start(self):
        for queue in self.buffers.values():
            queue.offer(np.full(1600,0.1,dtype="<f4").tobytes())
    def close(self): self.closed=True

SELECTION={name:{"defaultSampleRate":16000,"maxInputChannels":1} for name in ("mic","loopback")}

def wait_running(session):
    deadline=time.monotonic()+2
    while session.phase!="Läuft" and time.monotonic()<deadline:
        time.sleep(.01)
    assert session.phase=="Läuft"

def test_stop_drains_both_sources_and_keeps_final_results_only_in_ram(monkeypatch):
    import builtins, io
    writes=[]
    original=builtins.open
    def guard(file,mode="r",*args,**kwargs):
        if isinstance(mode,str) and any(flag in mode for flag in "wax+"):
            writes.append(str(file))
        return original(file,mode,*args,**kwargs)
    monkeypatch.setattr(builtins,"open",guard)
    monkeypatch.setattr(io,"open",guard)
    sdk,configs,recognizers=fake_sdk()
    session=Session("Alex",sdk,Capture)
    session.start("westeurope","synthetic-test-key","de-DE",SELECTION)
    wait_running(session)
    session.stop()
    assert session.finished.wait(3)
    assert session.error==""
    _,finals,partials=session.transcript.snapshot()
    assert {item.source for item in finals}=={"mic","loopback"}
    assert {item.speaker for item in finals}=={"Alex (Mikrofon)","Guest-1"}
    assert all(item.audio_offset==.25 for item in finals)
    assert all(item.text=="Letzte Wörter" for item in finals)
    assert not partials
    assert all(q.closed and q.size==0 for q in session.buffers.values())
    assert session.capture.closed
    assert all(c.properties["audio_logging"]=="false" and c.properties["log_file"]=="" for c in configs)
    assert all(not r.transcribed.handlers and not r.recognized.handlers for r in recognizers)
    assert writes==[]

def test_failure_closes_everything_and_never_exposes_raw_error():
    sdk,configs,recognizers=fake_sdk()
    class Broken(Capture):
        def open(self): raise RuntimeError("DO-NOT-LOG-secret")
    session=Session("Alex",sdk,Broken)
    session.start("westeurope","synthetic-test-key","de-DE",SELECTION)
    assert session.finished.wait(3)
    assert session.error and "DO-NOT-LOG" not in session.error
    assert session.capture.closed
    assert all(q.closed and q.size==0 for q in session.buffers.values())

def test_live_cancellation_stops_both_sources_and_clear_blocks_late_results():
    sdk,_,recognizers=fake_sdk()
    session=Session("Alex",sdk,Capture)
    session.start("westeurope","synthetic-test-key","de-DE",SELECTION)
    wait_running(session)
    recognizers[0].canceled.emit(NS(reason="error",error_details="DO-NOT-LOG"))
    session.transcript.clear()
    assert session.finished.wait(3)
    assert session.error and "DO-NOT-LOG" not in session.error
    assert session.transcript.snapshot()[1]==[]
