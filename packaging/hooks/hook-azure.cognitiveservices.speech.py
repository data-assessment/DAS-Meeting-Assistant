"""PCM PushAudioInputStream needs only the core SDK, not device/codec/KWS DLLs."""
from pathlib import Path
from PyInstaller.utils.hooks import get_package_paths

_, package = get_package_paths("azure.cognitiveservices.speech")
core = Path(package) / "Microsoft.CognitiveServices.Speech.core.dll"
if not core.is_file():
    raise RuntimeError("Microsoft Speech core SDK library missing")
binaries = [(str(core), "azure/cognitiveservices/speech")]
