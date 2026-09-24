import threading
import time
import numpy as np
import pytest
from prototypes.speech.core import AudioBuffer, PcmReader, Transcript
from prototypes.speech.session import validate, wait_future

def test_queue_is_bounded_and_overflow_is_visible():
    errors=[]
    queue=AudioBuffer(8,errors.append)
    assert queue.offer(b"1234")
    assert queue.offer(b"5678")
    assert queue.peak==8
    assert not queue.offer(b"9")
    assert queue.failed and queue.closed and queue.size==0
    assert errors and queue.take()==b""
    assert not queue.offer(b"later")

def test_finish_drains_in_order_without_saving():
    queue=AudioBuffer(10)
    assert queue.offer(b"")
    queue.offer(b"12")
    queue.offer(b"34")
    queue.finish()
    assert [queue.take(),queue.take(),queue.take()]==[b"12",b"34",b""]

def test_discard_unblocks_waiting_consumer():
    queue=AudioBuffer(10)
    result=[]
    thread=threading.Thread(target=lambda:result.append(queue.take()))
    thread.start()
    queue.discard()
    thread.join(timeout=1)
    assert not thread.is_alive() and result==[b""]

def drain(reader, chunk_size=1024):
    result=bytearray()
    target=bytearray(chunk_size)
    while True:
        count=reader.read(memoryview(target))
        if not count:
            return bytes(result)
        result.extend(target[:count])

def test_stereo_is_downmixed_before_streaming_resampling():
    queue=AudioBuffer(100000)
    stereo=np.tile(np.array([0.25,-0.25],dtype="<f4"),(4800,1))
    for part in np.array_split(stereo,5):
        queue.offer(part.tobytes())
    queue.finish()
    reader=PcmReader(queue,48000,2)
    result=drain(reader)
    assert len(result)==1600*2
    assert not any(result)
    assert not result.startswith(b"RIFF")

def test_sources_remain_separate_and_clipped_pcm_is_finite():
    results=[]
    for value in (0.2,0.6):
        queue=AudioBuffer(20000)
        queue.offer(np.full(3200,value,dtype="<f4").tobytes())
        queue.finish()
        results.append(np.frombuffer(drain(PcmReader(queue,16000,1)),dtype="<i2"))
    assert abs(np.mean(results[1][100:-100])/np.mean(results[0][100:-100])-3)<0.01

def test_pcm_reader_releases_data_and_unblocks_on_close():
    queue=AudioBuffer(100)
    reader=PcmReader(queue,16000,1)
    result=[]
    thread=threading.Thread(target=lambda:result.append(reader.read(memoryview(bytearray(32)))))
    thread.start()
    reader.close()
    thread.join(timeout=1)
    assert not thread.is_alive() and result==[0]
    assert not reader.pending and queue.size==0

@pytest.mark.parametrize("rate,channels",[(0,1),(48000,0),(48000,9),(200000,1)])
def test_unsupported_formats_rejected(rate,channels):
    with pytest.raises(ValueError):
        PcmReader(AudioBuffer(100),rate,channels)

def test_partial_replacement_and_session_scoped_speakers():
    first=Transcript("one","Alex")
    second=Transcript("two","Alex")
    first.add("loopback","Guest-1","a",final=False)
    first.add("loopback","Guest-2","ab",final=False)
    assert len(first.snapshot()[2])==1
    first.add("loopback","Guest-2","abc",1,True)
    first.add("mic","Guest-99","Hallo",2,True)
    second.add("loopback","Guest-2","Andere Person")
    _,finals,partials=first.snapshot()
    assert not partials
    assert finals[0].speaker=="Guest-2"
    assert finals[1].speaker=="Alex (Mikrofon)"
    assert finals[0].session_id!=second.snapshot()[1][0].session_id
    first.clear()
    assert first.snapshot()[1]==[]
    assert first.add("mic",None,"late") is False

def test_text_limit_and_finalization():
    transcript=Transcript("one","Alex",max_chars=4,max_segments=1)
    transcript.add("mic",None,"Test")
    with pytest.raises(ValueError):
        transcript.add("mic",None,"weiter")
    transcript.add("loopback","Guest-1","partial",final=False)
    transcript.seal()
    assert not transcript.snapshot()[2]
    assert len(transcript.snapshot()[1])==1
    assert not transcript.add("mic",None,"late")
    transcript.clear()
    assert transcript.characters==0

@pytest.mark.parametrize("region,key,language,name",[
    ("https://secret.example","key","de-DE","Alex"),
    ("westeurope","","de-DE","Alex"),
    ("westeurope","DO-NOT-LOG\n","de-DE","Alex"),
    ("westeurope","key","German","Alex"),
    ("westeurope","key","de-DE",""),
])
def test_configuration_errors_never_echo_credentials(region,key,language,name):
    with pytest.raises(ValueError) as error:
        validate(region,key,language,name)
    assert "DO-NOT-LOG" not in str(error.value)

def test_wait_future_is_bounded_and_does_not_echo_service_error():
    class Failure:
        def get(self): raise RuntimeError("DO-NOT-LOG-secret-or-transcript")
    with pytest.raises(RuntimeError) as error:
        wait_future(Failure(),1)
    assert "DO-NOT-LOG" not in str(error.value)
    release=threading.Event()
    class Slow:
        def get(self): release.wait()
    try:
        with pytest.raises(TimeoutError):
            wait_future(Slow(),0.05)
    finally:
        release.set()
