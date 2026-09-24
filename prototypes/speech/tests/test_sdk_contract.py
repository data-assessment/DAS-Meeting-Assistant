"""Constructor-only smoke checks against the optional real SDK; never starts Azure."""
import pytest

def test_real_sdk_accepts_the_two_stream_and_recognizer_types():
    sdk=pytest.importorskip("azure.cognitiveservices.speech")
    from prototypes.speech.core import AudioBuffer, PcmReader
    class Pull(sdk.audio.PullAudioInputStreamCallback):
        def __init__(self):
            super().__init__()
            self.reader=PcmReader(AudioBuffer(100),16000,1)
        def read(self,buffer): return self.reader.read(buffer)
        def close(self): self.reader.close()
    callbacks=[]
    for diarization in (False,True):
        config=sdk.SpeechConfig(subscription="0"*32,region="westeurope")
        config.speech_recognition_language="de-DE"
        config.set_property(sdk.PropertyId.SpeechServiceResponse_DiarizeIntermediateResults,"true")
        config.set_property(sdk.PropertyId.SpeechServiceConnection_EnableAudioLogging,"false")
        config.set_property(sdk.PropertyId.Speech_LogFilename,"")
        callback=Pull()
        callbacks.append(callback)
        stream=sdk.audio.PullAudioInputStream(callback,sdk.audio.AudioStreamFormat(
            samples_per_second=16000,bits_per_sample=16,channels=1))
        audio=sdk.audio.AudioConfig(stream=stream)
        recognizer=(sdk.transcription.ConversationTranscriber(speech_config=config,audio_config=audio)
                    if diarization else sdk.SpeechRecognizer(speech_config=config,audio_config=audio))
        assert recognizer is not None
        callback.close()
