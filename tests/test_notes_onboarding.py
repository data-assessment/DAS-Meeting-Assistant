import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import app
import config
from engine import meeting_notes, settings_store


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'AI_MODE', 'entra')
    notes = meeting_notes.Notes(tmp_path / 'notes')
    state = app.AppState()
    state.notes_window = SimpleNamespace(hide=Mock())
    state.notes_visible = True
    monkeypatch.setattr(app, 'NOTES', notes)
    monkeypatch.setattr(app, 'STATE', state)
    monkeypatch.setattr(settings_store, '_PATH', tmp_path / 'settings.json')
    yield notes, state, TestClient(app.api)
    notes.shutdown()


PREFERENCES = dict(enabled=True, language='de-DE', mic='', loopback='', autoStart=False)


@pytest.mark.parametrize('signed_in,busy', [(False, False), (False, True), (True, True)])
def test_finish_and_configure_cannot_bypass_login(setup, signed_in, busy):
    notes, state, client = setup
    notes.setup_complete, notes.setup_busy = signed_in, busy
    assert not client.post('/api/notes/finish-setup', json=PREFERENCES).json()['ok']
    assert not client.post('/api/notes/configure', json={'enabled': True}).json()['ok']
    assert not notes.onboarding_complete
    state.notes_window.hide.assert_not_called()


@pytest.mark.parametrize('signed_in', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('manual', [False, True])
def test_no_capture_until_setup_finished(setup, monkeypatch, signed_in, enabled, manual):
    notes, state, _ = setup
    notes.setup_complete, notes.enabled = signed_in, enabled
    monkeypatch.setattr(notes, 'start', Mock(side_effect=AssertionError('capture before finish')))
    monkeypatch.setattr(app, '_start_recording', Mock(side_effect=AssertionError('legacy capture before finish')))
    show = Mock()
    monkeypatch.setattr(app, '_show_notes_window', show)
    asyncio.run(app._begin_meeting('Meeting', manual=manual))
    assert not state.active and not state.notes_starting
    show.assert_called_once_with('settings', activate=True)


def test_finish_saves_preferences_closes_window_and_survives_restart(setup, monkeypatch):
    notes, state, client = setup
    notes.setup_complete = True
    state.auto_start = True
    assert client.post('/api/notes/finish-setup', json=PREFERENCES).json()['ok']
    assert notes.ready and not state.auto_start and not state.notes_visible
    state.notes_window.hide.assert_called_once()
    saved = settings_store.load()
    assert saved['meeting_notes_onboarding_complete'] is True
    notes.setup_complete = notes.onboarding_complete = False
    app._apply_settings(saved)
    assert notes.ready
    start = Mock(side_effect=RuntimeError('synthetic capture attempt'))
    monkeypatch.setattr(notes, 'start', start)
    from engine.speech import capture
    monkeypatch.setattr(capture, 'devices', lambda: [])
    monkeypatch.setattr(app, '_show_notes_window', Mock())
    asyncio.run(app._begin_meeting('Meeting', manual=True))
    start.assert_called_once()


def test_close_without_finish_keeps_setup_pending(setup):
    notes, state, client = setup
    notes.setup_complete = True
    assert client.post('/api/notes/close', json={}).json()['ok']
    assert not notes.ready and not state.notes_visible
    app._save_settings()
    app._apply_settings(settings_store.load())
    assert notes.setup_complete and not notes.onboarding_complete


def test_failed_save_does_not_complete_or_close_setup(setup, monkeypatch):
    notes, state, client = setup
    notes.setup_complete = True
    before = (notes.options.copy(), state.auto_start)
    monkeypatch.setattr(settings_store, '_PATH', notes.folder.parent)  # A directory cannot be written as a settings file.
    result = client.post('/api/notes/finish-setup', json={**PREFERENCES, 'language': 'en-US'}).json()
    assert not result['ok'] and not notes.ready
    assert (notes.options, state.auto_start) == before
    state.notes_window.hide.assert_not_called()


def test_invalid_preferences_and_active_meeting_keep_window_open(setup):
    notes, state, client = setup
    notes.setup_complete = True
    assert not client.post('/api/notes/finish-setup', json={**PREFERENCES, 'language': 'invalid'}).json()['ok']
    state.active = True
    assert not client.post('/api/notes/finish-setup', json=PREFERENCES).json()['ok']
    state.notes_window.hide.assert_not_called()
    assert client.post('/api/notes/close', json={}).json()['ok']
    assert state.active  # Closing settings must not stop a meeting.


def test_existing_signed_in_users_migrate_but_incomplete_setup_stays_pending(setup):
    notes, _, _ = setup
    data = {'meeting_notes_setup_complete': True, 'meeting_notes_access_mode': 'entra'}
    app._apply_settings(data)
    assert notes.ready
    app._apply_settings({**data, 'meeting_notes_onboarding_complete': False})
    assert not notes.ready and notes.setup_complete
    app._apply_settings({**data, 'meeting_notes_access_mode': 'gateway'})
    assert not notes.setup_complete and not notes.onboarding_complete
