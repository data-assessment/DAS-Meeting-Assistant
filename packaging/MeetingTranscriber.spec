# -*- mode: python ; coding: utf-8 -*-
"""Build with packaging/build.ps1; all builds require a validated public profile."""
import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable,
    VarFileInfo, VarStruct, VSVersionInfo,
)

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

sys.path.insert(0, ROOT)
from deployment_profile import load_profile, PROFILE_FILENAME

profile = load_profile(Path(SPECPATH) / PROFILE_FILENAME)
app_name = profile.executable_name
version_text = re.search(r'^VERSION\s*=\s*"([^"]+)"',
                         Path(ROOT, "config.py").read_text(encoding="utf-8"), re.M)[1]
version_parts = tuple(map(int, version_text.split("."))) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_parts, prodvers=version_parts,
                     mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", "Data Assessment Solutions"),
        StringStruct("FileDescription", profile.display_name),
        StringStruct("FileVersion", version_text),
        StringStruct("InternalName", app_name),
        StringStruct("OriginalFilename", app_name + ".exe"),
        StringStruct("ProductName", profile.display_name),
        StringStruct("ProductVersion", version_text),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)

datas = [
    (os.path.join(ROOT, "frontend", "dist"), "frontend/dist"),
    (os.path.join(ROOT, "docs", "app"), "docs/app"),
    (os.path.join(ROOT, "engine", "locales"), "engine/locales"),
    (os.path.join(ROOT, "favicon.ico"), "."),
    (os.path.join(SPECPATH, PROFILE_FILENAME), "."),
]
binaries = []
hiddenimports = collect_submodules("engine")
# Custom hooks select only the Windows UI and PCM Speech components we use.
for pkg in ("pyaudiowpatch", "soxr"):
    datas += collect_data_files(pkg)
    binaries += collect_dynamic_libs(pkg)
hiddenimports += ["webview.platforms.edgechromium", "webview.platforms.winforms"]


a = Analysis(
    [os.path.join(ROOT, "app.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(SPECPATH, "hooks")],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "server",
              "engine.usage_store",
              "aiortc", "aioice", "av", "pylibsrtp", "OpenSSL",
              "PyInstaller", "_pyinstaller_hooks_contrib", "webview.__pyinstaller",
              "pip", "piptools", "setuptools", "wheel", "build", "_pytest",
              "webview.platforms.gtk", "webview.platforms.qt", "webview.platforms.cocoa",
              "PIL._imagingtk", "PIL.ImageTk", "PIL._avif", "PIL._webp",
              "PIL._imagingft", "PIL._imagingcms"],
    noarchive=False,
)
# Windows 10+ supplies these OS components. Never copy them from another app.
os_components = {"dbghelp.dll", "dbgcore.dll", "ucrtbase.dll"}
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in os_components
              and not Path(entry[0]).name.lower().startswith("api-ms-win-")]

# Fail closed if dependency discovery escapes the selected Python distribution.
allowed_roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()]
system32 = Path(os.environ["SystemRoot"], "System32").resolve()
# The C++ runtime required by Speech is an explicit redistributable, unlike
# arbitrary DLLs found on PATH. Python already supplies the C runtime DLLs.
system_redist = {"msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_codecvt_ids.dll",
                 "vcruntime140.dll", "vcruntime140_1.dll"}
for dest, source, kind in a.binaries:
    if Path(source).resolve().parent == system32 and Path(source).name.lower() in system_redist:
        continue
    if not any(Path(source).resolve().is_relative_to(base) for base in allowed_roots):
        raise RuntimeError(f"Native dependency outside the build runtime: {dest}: {source}")
for module, source, kind in a.pure:
    if module.split(".")[0] in {"PyInstaller", "_pyinstaller_hooks_contrib", "aiortc", "av", "pytest", "_pytest"}:
        raise RuntimeError(f"Unexpected build-only or removed module: {module}")
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=app_name,
    version=version_info,
    console=False,  # windowed: no console (logs go to the data dir)
    icon=os.path.join(ROOT, "favicon.ico"),
    upx=False,  # UPX-packed binaries are a common antivirus false-positive trigger
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name=app_name,
)
