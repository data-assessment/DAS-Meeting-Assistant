"""Own the desktop HTTP endpoint before opening any WebView."""
import errno
import hashlib
import secrets
import socket
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import ProxyHandler, build_opener

INSTANCE_HEADER = "X-Voice-Transcriber-Instance"


class StartupError(RuntimeError):
    pass


class _Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.files = []
        self.has_script = self.has_root = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and attrs.get("id") == "root":
            self.has_root = True
        if tag == "script" and attrs.get("src"):
            self.has_script = True
            self.files.append(attrs["src"])
        if tag == "link" and attrs.get("rel") in ("stylesheet", "modulepreload") and attrs.get("href"):
            self.files.append(attrs["href"])


def validate_assets(directory):
    """Catch incomplete installations before a blank page or JSON 404 can open."""
    directory = Path(directory).resolve()
    try:
        content = (directory / "index.html").read_bytes()
        parser = _Assets()
        parser.feed(content.decode("utf-8-sig"))
        if not parser.has_root or not parser.has_script:
            raise ValueError("Missing React entry point")
        for value in parser.files:
            url = urlsplit(value)
            asset = (directory / unquote(url.path).lstrip("/")).resolve()
            if url.scheme or url.netloc or not asset.is_relative_to(directory) or not asset.is_file() or not asset.stat().st_size:
                raise ValueError("Missing or invalid UI asset")
        return hashlib.sha256(content).digest()
    except (OSError, ValueError) as exc:
        raise StartupError("Die Programmoberfläche fehlt oder ist unvollständig. "
                           "Bitte den Installer erneut ausführen.") from exc


class LocalUI:
    def __init__(self, host, preferred_port, *, allow_fallback):
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            # SO_REUSEADDR on Windows can let another process bind the same port.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind((host, preferred_port))
            except OSError as exc:
                occupied = exc.errno in (errno.EADDRINUSE, errno.EACCES) or getattr(exc, "winerror", None) in (10013, 10048)
                if not allow_fallback or not occupied or preferred_port == 0:
                    raise
                sock.bind((host, 0))
            sock.listen(128)
            sock.setblocking(False)
        except (OSError, OverflowError) as exc:
            sock.close()
            raise StartupError("Die lokale Verbindung konnte nicht gestartet werden. "
                               "Bitte DAS Meeting Assistant beenden und erneut starten. "
                               "Falls der Fehler bleibt, die lokale Port-Einstellung prüfen lassen.") from exc
        self.socket = sock
        self.port = sock.getsockname()[1]
        self.host = host
        self.instance = secrets.token_hex(24)
        connect_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
        authority = f"[{connect_host}]" if ":" in connect_host else connect_host
        self.url = f"http://{authority}:{self.port}/"

    def close(self):
        self.socket.close()

    def wait(self, expected_digest, *, timeout=15, alive=lambda: True):
        # A company HTTP proxy must not receive local startup requests.
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and alive():
            try:
                with opener.open(self.url + "?view=notes", timeout=min(0.5, max(0.01, deadline - time.monotonic()))) as response:
                    if (response.status == 200 and response.headers.get(INSTANCE_HEADER) == self.instance
                            and response.headers.get_content_type() == "text/html"
                            and hashlib.sha256(response.read(2_000_000)).digest() == expected_digest):
                        return
            except (OSError, ValueError):
                pass
            time.sleep(0.05)
        raise StartupError("Die Programmoberfläche konnte nicht gestartet werden. "
                           "Bitte DAS Meeting Assistant erneut starten. Falls der Fehler bleibt, den Installer erneut ausführen.")
