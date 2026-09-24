"""Windows current-user DPAPI storage, outside installation and source files."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path

class CredentialError(Exception):
    pass

class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

def crypt(value, purpose, decrypt=False):
    if os.name != "nt": raise CredentialError("Windows-Schlüsselablage nicht verfügbar.")
    source_buffer = ctypes.create_string_buffer(value)
    entropy_buffer = ctypes.create_string_buffer(purpose.encode("utf-8"))
    source = Blob(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = Blob(len(purpose.encode("utf-8")), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    dll = ctypes.WinDLL("crypt32", use_last_error=True)
    fn = dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN only; never CRYPTPROTECT_LOCAL_MACHINE.
    if not fn(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
        raise CredentialError("Azure-Zugänge konnten nicht ver- oder entschlüsselt werden.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(output.data)

class CredentialStore:
    def __init__(self, path):
        self.path = Path(path)
        self.purpose = "DAS-Meeting-Notes/v1/" + self.path.parent.name

    def save(self, options):
        data = {"version": 1, **{k: options[k] for k in ("region", "endpoint", "model", "speechKey", "chatKey")}}
        try:
            encrypted = crypt(json.dumps(data).encode("utf-8"), self.purpose)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_bytes(encrypted)
            temp.replace(self.path)
        except Exception:
            raise CredentialError("Azure-Zugänge nicht gespeichert. Windows-Schlüsselablage oder Speicherort prüfen.") from None

    def load(self):
        if not self.path.exists(): return None
        try:
            if self.path.stat().st_size > 20000: raise ValueError()
            data = json.loads(crypt(self.path.read_bytes(), self.purpose, decrypt=True))
            if set(data) != {"version", "region", "endpoint", "model", "speechKey", "chatKey"} or data["version"] != 1:
                raise ValueError()
            if any(not isinstance(v, str) or len(v) > 2048 for k,v in data.items() if k != "version"): raise ValueError()
            return data
        except Exception:
            raise CredentialError("Gespeicherte Azure-Zugänge konnten nicht geladen werden. In Einstellungen erneut eintragen.") from None
