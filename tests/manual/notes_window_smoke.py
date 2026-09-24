"""Manual Windows/WebView2 regression check. Run from the repository after npm build.

Uses only synthetic notes and a private temporary WebView profile. Opens one test
window briefly; never contacts Azure, records audio or reads saved meeting notes.
"""
import ctypes
import json
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from ctypes import wintypes

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import webview
from engine.notes_window import present, resize

STATE = dict(health="ok", autoStartSuppressed=False, storagePath="C:/Test/Meeting-Notizen",
    enabled=True, active=False, currentId=None, autoStart=True, error="", uiView="meeting",
    uiRequest=1, options=dict(hasSpeechKey=True, hasChatKey=True), reviews=[])

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "frontend/dist"), **kwargs)
    def log_message(self, *_): pass
    def do_GET(self):
        if self.path == "/api/notes":
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps(STATE).encode())
        else: super().do_GET()
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(b'{"ok":true}')

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
root = webview.create_window("DAS Test host", html="", hidden=True, focus=False)
result = []

def run():
    from System import Action
    from System.Windows.Forms import FormWindowState
    from webview.platforms.winforms import BrowserView
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    window = None
    try:
        before = user32.GetForegroundWindow()
        window = webview.create_window("DAS Fenstertest", url=f"http://127.0.0.1:{server.server_port}/?view=notes",
            hidden=True, focus=False, width=450, height=480, min_size=(360,250))
        assert window.events.loaded.wait(20), "WebView failed to load"
        form = BrowserView.instances[window.uid]
        def inspect():
            data = []
            def read():
                data.extend([bool(form.Visible), bool(form.browser.webview.Visible)])
            form.Invoke(Action(read))
            return data
        resize(window, 450, 400)
        assert inspect() == [False, False], "Resize revealed a hidden window"
        present(window, False)
        resize(window, 450, 440)
        time.sleep(.5)
        assert inspect() == [True, True], "Frame visible without WebView content"
        assert user32.GetForegroundWindow() == before, "Automatic show/resize stole keyboard focus"
        for _ in range(40):
            if window.evaluate_js("document.querySelectorAll('main.meeting-notes').length") == 1: break
            time.sleep(.1)
        assert window.evaluate_js("document.querySelectorAll('main.meeting-notes').length") == 1, "React content missing"
        assert window.evaluate_js("document.querySelector('main').getBoundingClientRect().width > 300"), "Content has no layout"
        window.hide()
        resize(window, 450, 500)
        assert inspect() == [False, False], "Hidden notes resurfaced during resize"
        present(window, False)
        assert inspect() == [True, True], "Reopening left content hidden"
        assert user32.GetForegroundWindow() == before, "Background reopen stole focus"
        form.Invoke(Action(lambda: setattr(form, 'WindowState', FormWindowState.Minimized)))
        present(window, True)
        time.sleep(.5)
        assert window.focus is True
        assert inspect() == [True, True]
        assert user32.GetForegroundWindow() == int(form.Handle.ToInt64()), "Activating did not bring notes to foreground"
        assert form.WindowState == FormWindowState.Normal, "Notes remained minimized"
        result.append({"ok": True, "checks": ["hidden resize", "managed child visibility", "background focus", "actual React layout", "hide/reopen", "foreground activation", "restore minimized window"]})
    except Exception as exc:
        result.append({"ok": False, "error": str(exc)})
    finally:
        if window: window.destroy()
        root.destroy()

webview.start(run, gui="edgechromium", private_mode=True)
server.shutdown()
print(json.dumps(result), flush=True)
sys.exit(0 if result and result[0]["ok"] else 1)
