import io
import json
import threading
import time
import wave
from types import SimpleNamespace as NS
import numpy as np
import pytest
from prototypes.speech import mai

class Reply:
    status_code=200
    def __init__(self,data=None,status=200):
        self.status_code=status
        self.data=data if data is not None else {"phrases":[{"speaker":0,"text":"Hallo", "offsetMilliseconds":100}]}
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def iter_content(self,size): yield json.dumps(self.data).encode()

class Client:
    def __init__(self,reply): self.reply=reply;self.calls=[]
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def post(self,url,**kwargs):
        wav=kwargs['files']['audio'][1]
        with wave.open(io.BytesIO(wav.getvalue()),'rb') as audio:
            assert audio.getnchannels()==1 and audio.getframerate()==16000
            assert audio.getsampwidth()==2 and audio.getnframes()==16000
        self.calls.append((url,kwargs))
        return self.reply


def test_request_is_in_memory_and_preserves_speaker_zero(monkeypatch):
    client=Client(Reply())
    monkeypatch.setattr(mai.requests,'Session',lambda:client)
    result=mai.transcribe(bytearray(32000),'northeurope','test-key',True,threading.Event())
    assert result==[(0,'Hallo',.1)]
    url,kwargs=client.calls[0]
    assert url.startswith('https://northeurope.api.cognitive.microsoft.com/')
    definition=json.loads(kwargs['data']['definition'])
    assert definition['enhancedMode']['model']=='MAI-Transcribe-2'
    assert definition['diarization']=={'enabled':True}
    assert definition['enhancedMode']['modelOptions']['transcribeStyle']=='verbatim'
    assert kwargs['allow_redirects'] is False
    assert client.trust_env is False


@pytest.mark.parametrize('status',[301,401,403,408,429,500,503])
def test_errors_and_redirects_never_expose_raw_response(monkeypatch,status):
    client=Client(Reply({'error':{'message':'SECRET audio text'}},status))
    monkeypatch.setattr(mai.requests,'Session',lambda:client)
    with pytest.raises(mai.MaiError) as exc:
        mai.transcribe(bytearray(32000),'northeurope','test-key',True,threading.Event())
    assert 'SECRET' not in str(exc.value) and str(status) in str(exc.value)


def test_discard_before_request_never_contacts_azure(monkeypatch):
    abort=threading.Event();abort.set()
    monkeypatch.setattr(mai.requests,'Session',lambda:pytest.fail('Network must not open'))
    assert mai.transcribe(bytearray(32000),'northeurope','test-key',True,abort)==[]


def test_wrong_region_rejected_before_upload():
    with pytest.raises(mai.MaiError):
        mai.transcribe(bytearray(32000),'westeurope','test-key',True,threading.Event())


@pytest.mark.parametrize('data',[{}, {'phrases':[{'text':'x','speaker':-1}]},
    {'phrases':[{'text':'x','speaker':'Guest-1'}]}, {'phrases':[{'text':'x','offsetMilliseconds':-1}]}])
def test_invalid_response_is_rejected(monkeypatch,data):
    monkeypatch.setattr(mai.requests,'Session',lambda:Client(Reply(data)))
    with pytest.raises(mai.MaiError):
        mai.transcribe(bytearray(32000),'northeurope','test-key',True,threading.Event())

class Capture:
    def __init__(self,selection,buffers,fail):self.buffers=buffers;self.metrics={};self.closed=False
    def open(self):pass
    def start(self):
        for buffer in self.buffers.values():buffer.offer(np.full(1600,.1,dtype='<f4').tobytes())
    def healthy(self):return True
    def close(self):self.closed=True

SELECTION={name:{'defaultSampleRate':16000,'maxInputChannels':1} for name in ('mic','loopback')}

def running(session):
    deadline=time.monotonic()+3
    while not session.phase.startswith('MAI: Aufnahme') and time.monotonic()<deadline:time.sleep(.01)
    assert session.phase.startswith('MAI: Aufnahme')


def test_stop_uploads_both_tracks_and_releases_audio(monkeypatch):
    import builtins
    original=builtins.open
    def no_write(path,mode='r',*args,**kwargs):
        assert not any(x in mode for x in 'wax+')
        return original(path,mode,*args,**kwargs)
    monkeypatch.setattr(builtins,'open',no_write)
    calls=[]
    def transport(pcm,region,key,diarize,abort):
        calls.append((len(pcm),diarize))
        return [(0,'Hallo',0)]
    session=mai.MaiSession('Alex',Capture,transport)
    session.start('northeurope','test-key','de-DE',SELECTION)
    running(session)
    assert calls==[]
    session.stop()
    assert session.finished.wait(3)
    assert session.error=='' and calls==[(3200,False),(3200,True)]
    finals=session.transcript.snapshot()[1]
    assert {x.source for x in finals}=={'mic','loopback'}
    assert {x.speaker for x in finals}=={'Alex (Mikrofon)','MAI-Sprecher 0'}
    assert all(not b for b in session.pcm.values())


def test_discard_prevents_uploads():
    session=mai.MaiSession('Alex',Capture,lambda *args:pytest.fail('Unexpected upload'))
    session.start('northeurope','test-key','de-DE',SELECTION)
    running(session);session.discard()
    assert session.finished.wait(3)
    assert session.transcript.snapshot()[1]==[]
    assert all(not b for b in session.pcm.values())


def test_discard_during_upload_rejects_result_and_second_request():
    entered=threading.Event();release=threading.Event();calls=[]
    def transport(*args):
        calls.append(True);entered.set();assert release.wait(3)
        return [(0,'must disappear',0)]
    session=mai.MaiSession('Alex',Capture,transport)
    session.start('northeurope','test-key','de-DE',SELECTION)
    running(session);session.stop();assert entered.wait(3)
    session.discard();release.set()
    assert session.finished.wait(3)
    assert len(calls)==1 and session.transcript.snapshot()[1]==[]


def test_microphone_and_guest_ids_are_not_merged():
    def transport(pcm,region,key,diarize,abort):return [(0,'Hallo',0),(1,'Weiter',1)]
    session=mai.MaiSession('Alex',Capture,transport)
    session.start('northeurope','test-key','de-DE',SELECTION)
    running(session);session.stop();assert session.finished.wait(3)
    remote=[x.speaker for x in session.transcript.snapshot()[1] if x.source=='loopback']
    assert remote==['MAI-Sprecher 0','MAI-Sprecher 1']


def test_audio_limit_does_not_silently_truncate(monkeypatch):
    monkeypatch.setattr(mai,'MAX_SECONDS',.05)
    session=mai.MaiSession('Alex',Capture,lambda *args:pytest.fail('No upload after overflow'))
    session.start('northeurope','test-key','de-DE',SELECTION)
    assert session.finished.wait(3)
    assert 'RAM-Limit' in session.error
    assert all(not b for b in session.pcm.values())


def test_response_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(mai,'MAX_RESPONSE',8)
    monkeypatch.setattr(mai.requests,'Session',lambda:Client(Reply()))
    with pytest.raises(mai.MaiError,match='Größen- oder Zeitlimit'):
        mai.transcribe(bytearray(32000),'northeurope','test-key',True,threading.Event())


def test_azure_failure_releases_all_audio():
    def broken(*args):raise mai.MaiError('MAI HTTP 503.')
    session=mai.MaiSession('Alex',Capture,broken)
    session.start('northeurope','test-key','de-DE',SELECTION)
    running(session);session.stop()
    assert session.finished.wait(3)
    assert session.error=='MAI HTTP 503.'
    assert all(not b for b in session.pcm.values())
