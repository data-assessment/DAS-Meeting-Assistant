"""Offline checks of native components in the actual frozen Windows application."""
import json
import os


def run(data_dir, icon_path):
    if not os.getenv("VOICE_TRANSCRIBER_DATA_ROOT"):
        raise RuntimeError("A separate VOICE_TRANSCRIBER_DATA_ROOT is required.")
    import azure.cognitiveservices.speech as speechsdk
    import numpy as np
    import soxr
    import pystray
    from PIL import Image
    import webview.platforms.winforms
    import webview.platforms.edgechromium

    # Construct the same PCM/transcriber pipeline as production, without opening
    # devices, making network calls or starting recognition.
    stream_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=16000, bits_per_sample=16, channels=1)
    stream = speechsdk.audio.PushAudioInputStream(stream_format)
    speech = speechsdk.SpeechConfig(subscription="offline-smoke-test", region="westeurope")
    transcriber = speechsdk.transcription.ConversationTranscriber(
        speech_config=speech, audio_config=speechsdk.audio.AudioConfig(stream=stream))
    assert transcriber is not None
    stream.write(bytes(320))
    stream.close()
    resampled = soxr.resample(np.zeros(4800, dtype=np.float32), 48000, 16000)
    assert len(resampled) == 1600
    with Image.open(icon_path) as image:
        tray = pystray.Icon("offline-smoke-test", image.convert("RGBA"))
        assert tray.icon.width > 0
    (data_dir / "runtime-smoke-result.json").write_text(json.dumps({
        "ok": True, "speechPcm": True, "soxr": True,
        "tray": True, "webview2Assemblies": True,
    }, indent=2) + "\n", encoding="utf-8")
