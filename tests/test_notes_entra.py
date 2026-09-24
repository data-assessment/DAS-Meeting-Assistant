"""Direct DAS access must never depend on broker availability or stored resource keys."""
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from azure.core.credentials import AccessToken
from fastapi.testclient import TestClient

import app
import config
from engine import ai_auth, graph_auth, meeting_notes as mn, notes_cloud
from engine.speech.session import Session
from test_meeting_notes import DEVICES, FakeSession, DRAFT


@pytest.fixture
def direct(monkeypatch, tmp_path):
    for name, value in dict(AI_MODE='entra', SPEECH_ENDPOINT='https://company-speech.cognitiveservices.azure.com',
            AOAI_ENDPOINT='https://company-text.openai.azure.com', SUMMARY_MODELS=['summary-model']).items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(ai_auth, 'gateway_token', Mock(side_effect=AssertionError('no gateway')))
    monkeypatch.setattr(notes_cloud, 'speech_credential', Mock(side_effect=AssertionError('no broker')))
    notes = mn.Notes(tmp_path / 'notes')
    monkeypatch.setattr(app, 'NOTES', notes)
    yield notes
    notes.shutdown()


def test_meeting_ignores_old_keys_and_uses_renewable_user_credential(direct):
    direct.credentials.load = Mock(side_effect=AssertionError('no old keys'))
    direct.load_credentials()
    direct.configure(dict(enabled=True, endpoint='https://old.openai.azure.com', model='old',
                          speechKey='old-secret', chatKey='old-chat'))
    class DirectSession(FakeSession):
        def start_entra(self, endpoint, language, selection, credential):
            assert endpoint == config.SPEECH_ENDPOINT
            assert isinstance(credential, ai_auth.UserTokenCredential)
            super().start('', '', language, selection)
    review = direct.start('Meeting', '2026-09-18T10:00:00', DEVICES, DirectSession)
    assert review.provider == {'auth': 'entra-direct', 'model': 'summary-model'}
    assert direct.enabled and direct.managed
    assert 'old-secret' not in json.dumps(review.public())
    assert not direct.public_options()['hasSpeechKey']


def test_summary_uses_packaged_endpoint_and_user_auth(direct, monkeypatch):
    create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
        message=SimpleNamespace(content=json.dumps(DRAFT)))]))
    client = Mock()
    client.__enter__ = Mock(return_value=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    client.__exit__ = Mock()
    constructor = Mock(return_value=client)
    monkeypatch.setattr(notes_cloud, 'AzureOpenAI', constructor)
    mn.generate_draft('synthetic text', {'endpoint': 'https://stale.invalid', 'key': 'old-secret', 'model': 'old'})
    assert constructor.call_args.kwargs['azure_endpoint'] == config.AOAI_ENDPOINT
    assert constructor.call_args.kwargs['azure_ad_token_provider'] is ai_auth.azure_token
    assert 'api_key' not in constructor.call_args.kwargs
    assert create.call_args.kwargs['model'] == 'summary-model'


def test_credential_refresh_is_silent_and_has_real_expiry(direct, monkeypatch):
    expires = int(time.time()) + 1200
    acquire = Mock(side_effect=[AccessToken('first', expires), AccessToken('refreshed', expires + 1200)])
    monkeypatch.setattr(graph_auth, 'get_access_token_for_scopes', acquire)
    credential = ai_auth.UserTokenCredential()
    assert credential.get_token(ai_auth.AZURE_SCOPE) == AccessToken('first', expires)
    assert credential.get_token(ai_auth.AZURE_SCOPE).token == 'refreshed'
    for call in acquire.call_args_list:
        assert call.kwargs == {'interactive': False}
    with pytest.raises(ValueError):
        credential.get_token('https://unexpected.example/.default')
    acquire.side_effect = RuntimeError('secret-request-detail')
    with pytest.raises(RuntimeError) as error:
        credential.get_token(ai_auth.AZURE_SCOPE)
    assert 'secret' not in str(error.value)


def test_msal_expiry_is_not_extended(monkeypatch):
    monkeypatch.setattr(graph_auth, '_acquire_token', lambda *a, **k: {
        'access_token': 'synthetic', 'expires_in': 600, 'expires_on': int(time.time()) + 300})
    result = graph_auth.get_access_token_for_scopes([ai_auth.AZURE_SCOPE])
    assert time.time() + 295 <= result.expires_on <= time.time() + 301


def test_missing_signin_never_opens_capture(direct, monkeypatch):
    monkeypatch.setattr(ai_auth, 'azure_access_token', Mock(side_effect=RuntimeError('Anmelden')))
    capture = Mock()
    session = Session('Name', capture_factory=capture)
    with pytest.raises(RuntimeError, match='Anmelden'):
        session.start_entra(config.SPEECH_ENDPOINT, 'de-DE', {}, ai_auth.UserTokenCredential())
    capture.assert_not_called()
    assert session.phase == 'Bereit'


def test_shipped_sdk_accepts_direct_credentials_for_both_recognizers(direct):
    import azure.cognitiveservices.speech as sdk
    session = Session('Name')
    session._endpoint = config.SPEECH_ENDPOINT
    session._token_credential = ai_auth.UserTokenCredential()
    speech = session._speech_config(sdk, None, None)
    assert speech.token_credential is session._token_credential
    stream = sdk.audio.PushAudioInputStream()
    try:
        audio = sdk.audio.AudioConfig(stream=stream)
        # Native SDK objects, without starting a network or microphone session.
        for factory in (sdk.SpeechRecognizer, sdk.transcription.ConversationTranscriber):
            recognizer = factory(speech_config=speech, audio_config=audio)
            assert recognizer.authorization_token == ''
    finally:
        stream.close()


def test_onboarding_tests_both_direct_services_and_keeps_failure_unready(direct, monkeypatch):
    monkeypatch.setattr(app.STATE, 'active', False)
    monkeypatch.setattr(app.STATE, 'notes_starting', False)
    monkeypatch.setattr(graph_auth, 'get_token', lambda **_: 'graph-token')
    monkeypatch.setattr(ai_auth, 'azure_token', lambda **_: 'azure-token')
    speech = Mock(side_effect=RuntimeError('Speech fehlt'))
    monkeypatch.setattr(notes_cloud, 'check_speech_access', speech)
    saved = []
    monkeypatch.setattr(app, '_save_settings', lambda: saved.append(app._persistable_settings()))
    api = TestClient(app.api)
    assert not api.post('/api/notes/connect', json={}).json()['ok']
    assert not direct.setup_complete
    speech.side_effect = None
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[object()])
    monkeypatch.setattr(notes_cloud, 'summary_client', lambda: client)
    assert api.post('/api/notes/connect', json={}).json()['ok']
    assert direct.setup_complete
    assert saved[-1]['meeting_notes_access_mode'] == 'entra'
    assert 'azure-token' not in json.dumps(saved)


def test_old_gateway_setup_does_not_skip_new_direct_setup(direct):
    app._apply_settings({'meeting_notes_setup_complete': True, 'meeting_notes_access_mode': 'gateway'})
    assert not direct.setup_complete
    app._apply_settings({'meeting_notes_setup_complete': True, 'meeting_notes_access_mode': 'entra'})
    assert direct.setup_complete


@pytest.mark.parametrize('denied', [True, False])
def test_speech_check_waits_for_final_service_outcome(direct, monkeypatch, denied):
    import azure.cognitiveservices.speech as sdk
    class Signal:
        def connect(self, fn): self.fn = fn
        def disconnect_all(self): pass
    class Transcriber:
        def __init__(self):
            self.session_started, self.session_stopped, self.canceled = Signal(), Signal(), Signal()
        def start_transcribing_async(self):
            self.session_started.fn(None)
            return SimpleNamespace(get=lambda: None)
        def stop_transcribing_async(self): return SimpleNamespace(get=lambda: None)
    transcriber = Transcriber()
    class Stream:
        def write(self, data): assert data == bytes(3200)
        def close(self):
            if denied:
                transcriber.canceled.fn(SimpleNamespace(reason=sdk.CancellationReason.Error))
            else:
                transcriber.session_stopped.fn(None)
    monkeypatch.setattr(sdk.audio, 'PushAudioInputStream', Stream)
    monkeypatch.setattr(sdk.audio, 'AudioConfig', lambda **_: object())
    monkeypatch.setattr(sdk, 'SpeechConfig', lambda **_: SimpleNamespace(set_property=lambda *a: None))
    monkeypatch.setattr(sdk.transcription, 'ConversationTranscriber', lambda **_: transcriber)
    if denied:
        with pytest.raises(RuntimeError, match='nicht freigeschaltet'):
            notes_cloud.check_speech_access()
    else:
        notes_cloud.check_speech_access()
