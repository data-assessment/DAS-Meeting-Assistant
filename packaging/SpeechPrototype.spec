# -*- mode: python ; coding: utf-8 -*-
"""Standalone prototype. No deployment profile, .env, credentials or production app."""
import os
from PyInstaller.utils.hooks import collect_all
APP_NAME=os.environ.get("DAS_SPEECH_PROTOTYPE_NAME", "DASSpeechPrototype")
ROOT=os.path.abspath(os.path.join(SPECPATH,".."))
datas,binaries,hiddenimports=collect_all("azure.cognitiveservices.speech")
for package in ("pyaudiowpatch","soxr"):
    data,native,imports=collect_all(package)
    datas+=data
    binaries+=native
    hiddenimports+=imports
a=Analysis([os.path.join(ROOT,"speech_prototype.py")],pathex=[ROOT],
    datas=datas,binaries=binaries,hiddenimports=hiddenimports,
    excludes=["pytest","app","config","engine","webview"],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name=APP_NAME,
    console=False,icon=os.path.join(ROOT,"favicon.ico"),upx=False)
coll=COLLECT(exe,a.binaries,a.datas,name=APP_NAME,upx=False)
