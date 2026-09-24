import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

import app
import config
from engine import meeting_notes as mn, notes_cloud as cloud
from engine.speech.session import Session
from test_meeting_notes import DEVICES, FakeSession, DRAFT


@pytest.fixture
def managed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'AI_MODE', 'gateway')
    monkeypatch.setattr(config, 'SUMMARY_MODELS', ['das-summary'])
    monkeypatch.setattr(config, 'USAGE_SERVICE_ENDPOINT', 'https://control.example')
    notes = mn.Notes(tmp_path / 'notes')
    monkeypatch.setattr(app, 'NOTES', notes)
    yield notes
    notes.shutdown()


def test_managed_default_and_upgrade_ignore_direct_keys(managed, monkeypatch):
    assert managed.enabled
    managed.credentials.load = Mock(side_effect=AssertionError('must not load keys'))
    managed.credentials.save = Mock(side_effect=AssertionError('must not store keys'))
    managed.configure(dict(enabled=True, language='de-DE', endpoint='https://old.openai.azure.com',
                           model='old', chatKey='old-chat', speechKey='old-speech', region='westus'))
    managed.load_credentials()
    managed.save_credentials()
    assert managed.options['chatKey'] == managed.options['speechKey'] == managed.options['endpoint'] == ''
    assert managed.public_options()['managed']
    assert not managed.public_options()['hasSpeechKey']


def test_managed_meeting_uses_broker_without_local_keys(managed):
    class ManagedSession(FakeSession):
        def start_managed(self, language, selection, provider):
            assert provider is cloud.speech_credential
            super().start('', '', language, selection)
    review = managed.start('Meeting', '2026-09-18T10:00:00', DEVICES, ManagedSession)
    assert review.provider == {'auth': 'entra', 'model': 'das-summary'}
    assert 'key' not in review.provider
    assert 'token' not in json.dumps(review.public())


def test_summary_uses_profile_route_and_model_even_with_stale_provider(managed, monkeypatch):
    create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
        message=SimpleNamespace(content=json.dumps(DRAFT)))]))
    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr(cloud, 'summary_client', Client)
    monkeypatch.setattr(mn, 'OpenAI', Mock(side_effect=AssertionError('direct client forbidden')))
    result = mn.generate_draft('synthetic text', {'endpoint':'https://old.openai.azure.com', 'key':'old', 'model':'old'})
    assert result['summary'] == DRAFT['summary']
    assert create.call_args.kwargs['model'] == 'das-summary'
    assert create.call_args.kwargs['store'] is False


@pytest.mark.parametrize('status,body,message', [
    (401, {}, 'DAS-Zugang'), (403, {}, 'DAS-Zugang'), (404, {}, 'noch nicht bereit'),
    (503, {}, 'noch nicht bereit'), (200, {'authorizationToken': 'secret', 'region':'x'}, 'konnte nicht'),
])
def test_broker_errors_are_safe(managed, monkeypatch, status, body, message):
    monkeypatch.setattr(cloud, 'gateway_token', lambda **_: 'user-access-token')
    def post(*args, **kwargs): return httpx.Response(status, json=body)
    monkeypatch.setattr(httpx.Client, 'post', post)
    with pytest.raises(RuntimeError, match=message) as error:
        cloud.speech_credential()
    assert 'secret' not in str(error.value)


def test_broker_token_only_in_memory_and_bounded_lifetime(managed, monkeypatch):
    monkeypatch.setattr(cloud, 'gateway_token', lambda **_: 'user-access-token')
    def post(self, url, **kwargs):
        assert url == 'https://control.example/speech/session'
        assert kwargs['headers']['Authorization'] == 'Bearer user-access-token'
        assert kwargs['json'] == {}
        return httpx.Response(200, json={'authorizationToken':'ephemeral-token-value', 'region':'westeurope', 'expiresIn':570})
    monkeypatch.setattr(httpx.Client, 'post', post)
    lease = cloud.speech_credential()
    assert 'ephemeral' not in repr(lease)
    assert time.monotonic() + 550 < lease.expires <= time.monotonic() + 570


def test_recognizer_token_renewal_and_transient_failure(monkeypatch):
    session = Session('Name')
    now = time.monotonic()
    session._credential = cloud.SpeechCredential('westeurope', 'old', now + 100)
    session._credential_provider = lambda: cloud.SpeechCredential('westeurope', 'new', now + 570)
    recognizer = SimpleNamespace(authorization_token='old')
    session._refresh_credentials([('loopback', recognizer)])
    assert recognizer.authorization_token == 'new'
    assert session._refresh_at == now + 450
    session._refresh_at = 0
    session._credential_provider = Mock(side_effect=RuntimeError('network'))
    session._refresh_credentials([('loopback', recognizer)])
    assert not session.cancel.is_set()
    assert session._refresh_at > time.monotonic()
    session._credential.expires = time.monotonic() + 20
    session._refresh_at = 0
    session._refresh_credentials([('loopback', recognizer)])
    assert session.cancel.is_set()
    assert 'verlängert' in session.error


def test_revoked_access_stops_capture_immediately():
    session = Session('Name')
    session._credential = cloud.SpeechCredential('westeurope', 'old', time.monotonic()+100)
    session._credential_provider = Mock(side_effect=cloud.AccessDenied('denied'))
    session._refresh_credentials([])
    assert session.cancel.is_set()


def test_real_speech_sdk_conversation_token_can_be_renewed_without_audio():
    # Instantiate the shipped SDK without opening a network/audio session. Its
    # ConversationTranscriber setter in 1.51.2 raises AttributeError; mocks alone
    # cannot detect this. Verify the running-object property path directly.
    import azure.cognitiveservices.speech as sdk
    speech = sdk.SpeechConfig(auth_token='offline-initial-token', region='westeurope')
    stream = sdk.audio.PushAudioInputStream()
    try:
        recognizer = sdk.transcription.ConversationTranscriber(
            speech_config=speech, audio_config=sdk.audio.AudioConfig(stream=stream))
        session = Session('Name')
        session._credential = cloud.SpeechCredential('westeurope', 'old', time.monotonic()+100)
        session._credential_provider = lambda: cloud.SpeechCredential('westeurope', 'offline-renewed-token', time.monotonic()+570)
        session._refresh_credentials([('loopback', recognizer)])
        assert recognizer.authorization_token == 'offline-renewed-token'
        assert session._credential.token == 'offline-renewed-token'
        assert not session.error
    finally:
        stream.close()


def test_setup_checks_both_services_and_does_not_store_secrets(managed, monkeypatch):
    from engine import graph_auth, ai_auth
    monkeypatch.setattr(app.STATE, 'active', False)
    monkeypatch.setattr(app.STATE, 'notes_starting', False)
    monkeypatch.setattr(graph_auth, 'get_token', lambda **_: 'graph-token')
    monkeypatch.setattr(ai_auth, 'gateway_token', lambda **_: 'cloud-token')
    check = Mock()
    monkeypatch.setattr(cloud, 'check_access', check)
    saved = []
    monkeypatch.setattr(app, '_save_settings', lambda: saved.append(app._persistable_settings()))
    result = TestClient(app.api).post('/api/notes/connect', json={}).json()
    assert result['ok'] and managed.enabled and managed.setup_complete
    check.assert_called_once()
    assert saved[0]['meeting_notes_setup_complete'] is True
    assert 'cloud-token' not in json.dumps(saved)
    monkeypatch.setattr(cloud, 'check_access', Mock(side_effect=RuntimeError('Dienst nicht bereit')))
    result = TestClient(app.api).post('/api/notes/connect', json={}).json()
    assert not result['ok'] and not managed.setup_complete


def test_first_managed_launch_opens_setup(managed, monkeypatch):
    monkeypatch.setattr(app.threading, 'Thread', lambda **_: SimpleNamespace(start=lambda: None))
    show = Mock()
    monkeypatch.setattr(app, '_show_notes_window', show)
    app.on_start()
    show.assert_called_once_with('settings', activate=True)
    managed.setup_complete = True
    show.reset_mock()
    app.on_start()
    show.assert_called_once_with('settings', activate=True)
    managed.onboarding_complete = True
    show.reset_mock()
    app.on_start()
    show.assert_not_called()
