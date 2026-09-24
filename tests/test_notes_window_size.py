from fastapi.testclient import TestClient
import pytest

import app
from engine import notes_window


def test_expand_restores_compact_width_and_does_not_reopen_hidden_window(monkeypatch):
    state = app.AppState()
    state.notes_window = object()
    state.notes_width = 720
    calls = []
    monkeypatch.setattr(app, 'STATE', state)
    monkeypatch.setattr(notes_window, 'resize', lambda window, *size: calls.append(size))

    app._fit_notes_window(960, expanded=True)
    assert state.notes_expanded and state.notes_compact_width == 720
    assert calls == []
    state.notes_visible = True
    app._fit_notes_window(state.notes_content_height)
    app._fit_notes_window(960, expanded=True)
    assert calls == [(980, 1000)]
    # A user's manual resize in the expanded view must not replace compact width.
    state.notes_width = 1100
    app._fit_notes_window(700, expanded=False)
    assert calls[-1] == (720, 750)
    assert not state.notes_expanded


def test_size_api_accepts_explicit_mode_and_legacy_height(monkeypatch):
    calls = []
    monkeypatch.setattr(app, '_fit_notes_window', lambda *a, **kw: calls.append((a, kw)))
    client = TestClient(app.api)
    assert client.post('/api/notes/fit', json={'height': 960, 'expanded': True}).json()['ok']
    assert client.post('/api/notes/fit', json={'height': 700, 'expanded': False}).json()['ok']
    assert client.post('/api/notes/fit', json={'height': 440}).json()['ok']
    assert calls == [((960,), {'expanded': True}), ((700,), {'expanded': False}), ((440,), {})]


@pytest.mark.parametrize('expanded', ['true', 1, 0, None, [], {}])
def test_size_api_rejects_invalid_mode_without_changing_window(monkeypatch, expanded):
    monkeypatch.setattr(app, '_fit_notes_window', lambda *a, **kw: pytest.fail('invalid resize'))
    assert not TestClient(app.api).post('/api/notes/fit', json={'height': 700, 'expanded': expanded}).json()['ok']
