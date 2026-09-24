# Azure Speech audio prototype

An isolated Windows experiment for microphone and WASAPI loopback capture, bounded
RAM-only audio mixing and speech recognition. It has its own window and does not replace
the main Meeting Notes application.

```powershell
.venv/Scripts/python.exe -m pip install -r requirements.txt -r prototypes/speech/requirements.txt
.venv/Scripts/python.exe speech_prototype.py --demo
```

The demo uses synthetic text without microphone capture or Azure calls. For a live test,
run without `--demo`, choose your own permitted Speech resource's region and key, select
the intended microphone and loopback device, then Start. Stop ends the bounded test.
Two physical speakers and synthetic demo text are not evidence of recognition quality.

The mixed mode uses one conversation transcriber. Speaker IDs are anonymous and are not
verified people or biometric identities. Loopback includes other system audio; Teams mute
does not automatically suppress the capture. Use a headset to avoid microphone echo.

Audio and transcript text remain in process memory in this prototype. This does not exclude
OS paging, crash dumps or service-side processing. Never commit a resource key, real meeting
text or personal test evidence. Private operational instructions are maintained separately.
