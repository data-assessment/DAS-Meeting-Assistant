from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock
import hashlib
import socket
import threading

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import app
import config
from engine.local_ui import LocalUI, StartupError, INSTANCE_HEADER, validate_assets

HTML = '<!doctype html><div id="root"></div><script type="module" src="./assets/main.js"></script><link rel="stylesheet" href="./assets/main.css">'


@pytest.fixture
def assets(tmp_path):
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'index.html').write_text(HTML, encoding='utf-8')
    (tmp_path / 'assets/main.js').write_text('document.getElementById("root").textContent="Bereit";', encoding='utf-8')
    (tmp_path / 'assets/main.css').write_text('body { color: black }', encoding='utf-8')
    return tmp_path


@pytest.mark.parametrize('missing', ['index.html', 'assets/main.js', 'assets/main.css'])
def test_incomplete_installation_is_detected_before_opening_window(assets, missing):
    (assets / missing).unlink()
    with pytest.raises(StartupError, match='Installer erneut'):
        validate_assets(assets)


@pytest.mark.parametrize('invalid', ['<h1>No app</h1>', HTML.replace('./assets/main.js', '../outside.js'),
                                    HTML.replace('./assets/main.js', 'https://example.org/app.js')])
def test_invalid_ui_assets_are_not_considered_ready(assets, invalid):
    (assets / 'index.html').write_text(invalid, encoding='utf-8')
    with pytest.raises(StartupError): validate_assets(assets)


def test_valid_assets_include_javascript_and_css(assets):
    assert validate_assets(assets) == hashlib.sha256(HTML.encode()).digest()
    (assets / 'assets/main.js').write_text('')
    with pytest.raises(StartupError): validate_assets(assets)


@contextmanager
def foreign_server():
    requests = []
    class Foreign(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"detail":"Not Found"}')
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Foreign)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def running(endpoint, monkeypatch, api):
    monkeypatch.setattr(app.STATE, 'local_ui', endpoint)
    monkeypatch.setattr(app, 'api', api)
    monkeypatch.setattr(app.STATE, 'server', None)
    thread = threading.Thread(target=lambda: app._serve_once(background=False), daemon=True)
    thread.start()
    try:
        yield thread
    finally:
        if app.STATE.server:
            app.STATE.server.should_exit = True
        thread.join(timeout=3)
        assert not thread.is_alive(), 'Test HTTP server must stop'


def ui_app(assets):
    api = FastAPI()
    api.middleware('http')(app._local_instance_header)
    api.mount('/', StaticFiles(directory=assets, html=True))
    return api


def test_foreign_port_falls_back_without_contacting_other_application(assets, monkeypatch):
    with foreign_server() as (port, requests):
        endpoint = LocalUI('127.0.0.1', port, allow_fallback=True)
        try:
            assert endpoint.port != port
            with running(endpoint, monkeypatch, ui_app(assets)) as thread:
                endpoint.wait(validate_assets(assets), timeout=3, alive=thread.is_alive)
                assert app._ui_url('?view=notes') == endpoint.url + '?view=notes'
            assert not requests
        finally:
            endpoint.close()


def test_fixed_oauth_port_never_silently_changes():
    with foreign_server() as (port, requests):
        with pytest.raises(StartupError, match='Port-Einstellung'):
            LocalUI('127.0.0.1', port, allow_fallback=False)
        assert not requests


def test_port_is_reserved_without_reuse_and_released_after_close():
    endpoint = LocalUI('127.0.0.1', 0, allow_fallback=False)
    port = endpoint.port
    with pytest.raises(StartupError): LocalUI('127.0.0.1', port, allow_fallback=False)
    if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
        assert endpoint.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) == 1
    endpoint.close()
    replacement = LocalUI('127.0.0.1', port, allow_fallback=False)
    replacement.close()


@pytest.mark.parametrize('kind', ['foreign-html', 'wrong-body', 'json-404'])
def test_open_port_is_not_readiness(assets, monkeypatch, kind):
    endpoint = LocalUI('127.0.0.1', 0, allow_fallback=False)
    api = FastAPI()
    @api.get('/')
    async def root():
        if kind == 'json-404':
            return HTMLResponse('{"detail":"Not Found"}', status_code=404, media_type='application/json')
        return HTMLResponse(HTML if kind == 'foreign-html' else '<html>Wrong app</html>',
                            headers={} if kind == 'foreign-html' else {INSTANCE_HEADER: endpoint.instance})
    try:
        with running(endpoint, monkeypatch, api):
            with pytest.raises(StartupError, match='Programmoberfläche konnte nicht gestartet'):
                endpoint.wait(validate_assets(assets), timeout=0.35)
    finally:
        endpoint.close()


def test_server_restart_retains_the_same_port(assets, monkeypatch):
    endpoint = LocalUI('127.0.0.1', 0, allow_fallback=False)
    digest = validate_assets(assets)
    try:
        for _ in range(2):
            with running(endpoint, monkeypatch, ui_app(assets)) as thread:
                endpoint.wait(digest, timeout=3, alive=thread.is_alive)
            with pytest.raises(StartupError): LocalUI('127.0.0.1', endpoint.port, allow_fallback=False)
    finally:
        endpoint.close()


def test_dead_server_does_not_open_any_webview(assets, monkeypatch):
    monkeypatch.setattr(app, '_setup_logging', lambda: None)
    monkeypatch.setattr(app, '_restore_settings', lambda: None)
    monkeypatch.setattr(app, 'FRONTEND_DIST', assets)
    monkeypatch.setattr(app, 'run_server', lambda: None)
    monkeypatch.setattr(config, 'PORT', 0)
    monkeypatch.setattr(app.STATE, 'quitting', False)
    monkeypatch.setattr(app.STATE, 'local_ui', None)
    monkeypatch.setattr(app.STATE, 'server', None)
    window = Mock()
    monkeypatch.setattr(app.webview, 'create_window', window)
    with pytest.raises(StartupError): app._run_app()
    window.assert_not_called()
    assert app.STATE.local_ui.socket.fileno() == -1


def test_real_startup_uses_owned_url_for_every_window(assets, monkeypatch):
    class Event:
        def __iadd__(self, fn): return self
    created = []
    def window(*args, **kwargs):
        created.append(kwargs['url'])
        return SimpleNamespace(events=SimpleNamespace(closing=Event(), resized=Event()))
    monkeypatch.setattr(app, '_setup_logging', lambda: None)
    monkeypatch.setattr(app, '_restore_settings', lambda: None)
    monkeypatch.setattr(app, 'FRONTEND_DIST', assets)
    monkeypatch.setattr(app, 'api', ui_app(assets))
    monkeypatch.setattr(app, 'run_server', lambda: app._serve_once(background=False))
    monkeypatch.setattr(app.webview, 'create_window', window)
    monkeypatch.setattr(config, 'MANAGED_BUILD', True)
    monkeypatch.setattr(app.STATE, 'quitting', False)
    monkeypatch.setattr(app.STATE, 'local_ui', None)
    monkeypatch.setattr(app.STATE, 'server', None)
    monkeypatch.setattr(app.STATE, 'window', None)
    with foreign_server() as (port, requests):
        monkeypatch.setattr(config, 'PORT', port)
        def gui(*args, **kwargs):
            assert created == [app.STATE.local_ui.url]
            assert app.STATE.local_ui.port != port
            for view in ('notes', 'settings', 'transcript', 'docs'):
                assert app._ui_url('?view=' + view).startswith(created[0])
            # Later environment reloads must not retarget the active UI.
            monkeypatch.setattr(config, 'PORT', 1)
            assert app._ui_url() == created[0]
        monkeypatch.setattr(app.webview, 'start', gui)
        app._run_app()
        assert not requests
        assert app.STATE.local_ui.socket.fileno() == -1


def test_missing_assets_prevent_server_start(assets, monkeypatch):
    monkeypatch.setattr(app, '_setup_logging', lambda: None)
    monkeypatch.setattr(app, '_restore_settings', lambda: None)
    monkeypatch.setattr(app, 'FRONTEND_DIST', assets)
    server = Mock()
    monkeypatch.setattr(app, 'LocalUI', server)
    (assets / 'index.html').unlink()
    with pytest.raises(StartupError, match='Installer erneut'): app._run_app()
    server.assert_not_called()


def test_startup_error_is_visible_and_releases_instance_lock(monkeypatch):
    instance = Mock()
    instance.acquire.return_value = True
    monkeypatch.setattr(app, '_SingleInstance', lambda: instance)
    monkeypatch.setattr(app, '_run_app', Mock(side_effect=StartupError('Bitte Installer erneut ausführen.')))
    message = Mock()
    monkeypatch.setattr(app.ctypes.windll.user32, 'MessageBoxW', message)
    with pytest.raises(SystemExit) as caught: app.main()
    assert caught.value.code == 1
    message.assert_called_once()
    assert 'Installer erneut' in message.call_args.args[1]
    instance.release.assert_called_once()


def test_api_and_websocket_share_the_reserved_endpoint(assets, monkeypatch):
    import json
    from urllib.request import ProxyHandler, build_opener
    from websockets.sync.client import connect
    api = ui_app(assets)
    # Put real application routes before the static mount, just as in app.py.
    for route in reversed(app.api.routes):
        if getattr(route, 'path', '') in ('/ws', '/api/notes'):
            api.router.routes.insert(0, route)
    endpoint = LocalUI('127.0.0.1', 0, allow_fallback=False)
    try:
        with running(endpoint, monkeypatch, api) as thread:
            endpoint.wait(validate_assets(assets), timeout=3, alive=thread.is_alive)
            opener = build_opener(ProxyHandler({}))
            with opener.open(endpoint.url + 'api/notes', timeout=2) as response:
                assert json.load(response)['ok']
            with connect(endpoint.url.replace('http:', 'ws:') + 'ws', proxy=None, open_timeout=2) as ws:
                assert json.loads(ws.recv(timeout=2))['type'] == 'state'
                ws.send('keepalive')
    finally:
        endpoint.close()


def test_company_proxy_is_bypassed_for_local_readiness(assets, monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    endpoint = LocalUI('127.0.0.1', 0, allow_fallback=False)
    try:
        with running(endpoint, monkeypatch, ui_app(assets)) as thread:
            endpoint.wait(validate_assets(assets), timeout=3, alive=thread.is_alive)
    finally:
        endpoint.close()
