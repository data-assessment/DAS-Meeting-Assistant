# Optional standalone experiment; no production installer or profile changes.
param([string]$PythonExe=".venv\Scripts\python.exe")
$ErrorActionPreference="Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
& $PythonExe -c "import tkinter, azure.cognitiveservices.speech, pyaudiowpatch, soxr"
if ($LASTEXITCODE -ne 0) { throw "Install runtime requirements and prototypes/speech/requirements.txt first." }
& $PythonExe -m PyInstaller --clean --noconfirm --distpath dist/speech-prototype --workpath build/speech-prototype packaging/SpeechPrototype.spec
if ($LASTEXITCODE -ne 0) { throw "Prototype build failed." }
Write-Host "Built dist\speech-prototype\DASSpeechPrototype\DASSpeechPrototype.exe (unsigned)"
