import asyncio
import json
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
import app
from engine import meeting_notes as mn
from engine.speech.core import Transcript

DEVICES = [dict(name="Mic", isLoopbackDevice=False, isSystemDefault=True),
           dict(name="Headset", isLoopbackDevice=True, isSystemDefault=True)]
DRAFT = {"summary": "Budget vereinbart.", "decisions": "Pilot starten.", "openQuestions": "Termin offen.",
         "tasks": [{"title": "Angebot erstellen", "owner": "", "recipient": "", "due": "", "uncertainty": "Empfänger prüfen"}]}

DRAFT = mn.Draft.model_validate(DRAFT).model_dump()

class FakeSession:
    def __init__(self, name):
        self.id = str(uuid.uuid4())
        self.transcript = Transcript(self.id, name)
        self.finished = threading.Event()
        self.error = ""
        self.stopped = False
    def start(self, region, key, language, selection):
        self.selection = selection
        self.transcript.add("mixed", "Guest-1", "VERTRAULICHES GESPRÄCH", 0)
    def stop(self):
        self.stopped = True
        self.transcript.seal()
        self.finished.set()
    def snapshot(self):
        return {"phase": "Läuft", "metrics": {}, "error": ""}

@pytest.fixture
def notes(monkeypatch, tmp_path):
    from engine import notes_people, notes_calls
    monkeypatch.setattr(notes_calls, "call_people", lambda *_: ([], "", True))
    monkeypatch.setattr(notes_people, "resolve_people", lambda started: ([], "Test ohne Kalender"))
    monkeypatch.setattr(mn, "generate_draft", lambda *_: json.loads(json.dumps(DRAFT)))
    notes = mn.Notes(tmp_path / "notes")
    notes.configure({"enabled": True, "speechKey": "test-speech-placeholder", "chatKey": "test-chat-placeholder",
                     "endpoint": "https://example.openai.azure.com", "model": "test-model"})
    monkeypatch.setattr(app, "NOTES", notes)
    yield notes
    notes.shutdown()

def start(notes):
    return notes.start("Test-Meeting", "2026-09-11T10:00:00", DEVICES, FakeSession)

def finish(notes, review):
    notes.current = None
    asyncio.run(notes.finish(review))

def test_stop_automatically_saves_derived_notes_without_raw_files(notes, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = []
    def generate(text, provider):
        calls.append((text, provider))
        return json.loads(json.dumps(DRAFT))
    monkeypatch.setattr(mn, "generate_draft", generate)
    review = start(notes)
    assert review.session.TEST_SECONDS == 7200
    assert set(review.session.selection) == {"mic", "loopback"}
    finish(notes, review)
    assert len(calls) == 1
    assert calls[0][1]["endpoint"] == "https://example.openai.azure.com"
    assert review.status == "Gespräch beendet"
    assert not review.public()["canSummarize"]
    files = list(notes.state_folder.glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["summary"] == DRAFT["summary"] and saved["reviewed"] is False
    assert saved["phase"] == "complete"
    assert "VERTRAULICHES" not in files[0].read_text(encoding="utf-8")


def test_success_clears_raw_but_keeps_results(notes, monkeypatch):
    review = start(notes)
    calls = []
    def generate(text, provider):
        calls.append((text, provider))
        return DRAFT.copy()
    monkeypatch.setattr(mn, "generate_draft", generate)
    finish(notes, review)
    assert "VERTRAULICHES GESPRÄCH" in calls[0][0]
    assert calls[0][1]["endpoint"] == "https://example.openai.azure.com"
    assert review.draft == DRAFT
    assert not review.session.transcript.snapshot()[1]
    assert not review.provider and not review.raw_deadline
    assert "VERTRAULICHES" not in json.dumps(review.public())


def test_failure_is_sanitized_and_retry_expires(notes, monkeypatch, capsys):
    review = start(notes)
    def fail(*_):
        raise RuntimeError("SECRET KEY and VERTRAULICHES GESPRÄCH")
    monkeypatch.setattr(mn, "generate_draft", fail)
    finish(notes, review)
    assert review.public()["canSummarize"]
    assert "SECRET" not in json.dumps(review.public()) + capsys.readouterr().out
    review.raw_deadline = time.monotonic() - 1
    notes.sweep()
    assert not review.session.transcript.snapshot()[1]
    assert not review.public()["canSummarize"]
    assert "15 Minuten" in review.error


def test_one_save_exports_edited_notes_and_tasks(notes, tmp_path):
    review = start(notes); finish(notes, review); review.draft = DRAFT.copy()
    result = notes.export(review.id, {"draft": DRAFT, "revision": review.revision})
    saved = json.loads(result["text"])
    assert saved["tasks"] == DRAFT["tasks"]
    assert saved["summary"] == DRAFT["summary"]
    assert "VERTRAULICHES" not in result["text"] and "key" not in result["text"]
    assert len(list(notes.state_folder.glob("*.json"))) == 1
    with pytest.raises(ValueError):
        notes.export(review.id, {"draft": DRAFT | {"transcript": "RAW"}, "revision": review.revision})
    with pytest.raises(ValueError):
        notes.export(review.id, {"draft": DRAFT, "transcript": "RAW"})

@pytest.mark.parametrize("endpoint", ["https://apim.example.com/ai", "https://x.azure-api.net", "http://x.openai.azure.com", "https://x.openai.azure.com.evil.org", "https://user@x.openai.azure.com", "https://x.openai.azure.com/path", "https://x.openai.azure.com?key=secret"])
def test_gateway_and_non_resource_endpoints_rejected(endpoint):
    with pytest.raises(ValueError):
        mn.direct_endpoint(endpoint)


def test_changed_resource_does_not_reuse_previous_key(notes):
    notes.configure({"enabled": True, "endpoint": "https://new.openai.azure.com"})
    assert not notes.options["chatKey"]
    with pytest.raises(ValueError):
        start(notes)


def test_shutdown_and_discard_block_late_callbacks(notes):
    review = start(notes); finish(notes, review)
    notes.discard(review.id)
    assert not review.session.transcript.add("mixed", "Guest-1", "late")
    notes.shutdown()
    assert not notes.options["chatKey"] and not notes.options["speechKey"]


def test_busy_discard_and_concurrent_summary_blocked(notes, monkeypatch):
    async def run():
        review = start(notes)
        notes.current = None
        entered, release = threading.Event(), threading.Event()
        def generate(*_):
            entered.set(); release.wait(3)
            return DRAFT
        monkeypatch.setattr(mn, "generate_draft", generate)
        task = asyncio.create_task(notes.finish(review))
        await asyncio.to_thread(entered.wait, 2)
        with pytest.raises(ValueError):
            await notes.summarize(review)
        with pytest.raises(ValueError):
            notes.discard(review.id)
        notes.shutdown()
        release.set()
        await task
        assert review.draft is None
    asyncio.run(run())


@pytest.mark.parametrize('manual', [False, True])
def test_app_start_stop_bypasses_all_legacy_paths(notes, monkeypatch, manual):
    from engine.speech import capture
    monkeypatch.setattr(app, "STATE", app.AppState())
    monkeypatch.setattr(capture, "devices", lambda: DEVICES)
    real_start = notes.start
    monkeypatch.setattr(notes, "start", lambda title, started, devices: real_start(title, started, devices, FakeSession))
    for name in ("_start_recording", "_start_background_transcription", "_finalize_meeting", "write_transcript"):
        monkeypatch.setattr(app, name, lambda *a, **k: pytest.fail("legacy file path used"))
    for name in ("_show_window", "_show_notes_window", "_refresh_tray", "_log_event"):
        monkeypatch.setattr(app, name, lambda *a, **k: None)
    presented = []
    monkeypatch.setattr(app, '_show_notes_window', lambda **kwargs: presented.append(kwargs['activate']))
    async def quiet(*args): pass
    monkeypatch.setattr(app, "broadcast", quiet)
    async def run():
        await app._begin_meeting("Meeting", manual=manual, keep_meeting_mapping=True)
        assert app.STATE.active and notes.current and app.STATE.recorder is None
        assert presented == [True]
        review = notes.current
        await app._begin_meeting("Meeting", manual=manual, keep_meeting_mapping=True)
        assert presented == [True], 'Repeated presence updates must not steal focus again'
        await app._end_meeting("manual")
        for _ in range(30):
            if not review.busy: break
            await asyncio.sleep(.01)
        assert not app.STATE.active and review.status == "Gespräch beendet"
        assert app.STATE.last_meeting is None
        assert presented == [True], 'Finalizing must not reopen the window'
    asyncio.run(run())


def test_failed_ram_start_does_not_fall_back_to_files(notes, monkeypatch):
    from engine.speech import capture
    monkeypatch.setattr(app, "STATE", app.AppState())
    monkeypatch.setattr(capture, "devices", lambda: [])
    monkeypatch.setattr(app, "_start_recording", lambda: pytest.fail("fallback recorder"))
    presented = []
    monkeypatch.setattr(app, "_show_notes_window", lambda **kwargs: presented.append(kwargs['activate']))
    async def quiet(*args): pass
    monkeypatch.setattr(app, "broadcast", quiet)
    asyncio.run(app._begin_meeting("Meeting", keep_meeting_mapping=True))
    assert not app.STATE.active and app.STATE.autostart_suppressed and notes.error
    assert presented == [True]


def test_api_never_leaks_keys_or_raw_by_default(notes, monkeypatch):
    monkeypatch.setattr(app, "STATE", app.AppState())
    review = start(notes); finish(notes, review)
    client = TestClient(app.api)
    response = client.get('/api/notes')
    assert response.headers['cache-control'] == 'no-store'
    assert 'VERTRAULICHES' not in response.text and 'test-chat-placeholder' not in response.text
    assert client.post('/api/notes/configure', json={'enabled': True}, headers={'Origin': 'https://evil.example'}).status_code == 403
    invalid = client.post('/api/notes/configure', json={'enabled': True, 'chatKey': ['SECRET']})
    assert 'SECRET' not in invalid.text
    assert '/api/notes/{review_id}/preview' not in client.get('/openapi.json').json()['paths']
    assert response.json()['autoStart'] is True


def test_settings_payload_does_not_persist_keys(notes):
    payload = app._persistable_settings()
    assert payload['meeting_notes_enabled'] is True
    assert 'test-chat-placeholder' not in json.dumps(payload)
    assert 'speechKey' not in payload['meeting_notes_options']


@pytest.mark.parametrize("finish_reason,content,success", [
    ("stop", json.dumps(DRAFT), True),
    ("length", json.dumps(DRAFT), False),
    ("stop", json.dumps(DRAFT | {"rawTranscript": "BAD"}), False),
    ("stop", "not json", False),
])
def test_chat_request_contract(monkeypatch, finish_reason, content, success):
    import httpx
    import openai
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0,
            "model": "test-model", "choices": [{"index": 0, "finish_reason": finish_reason,
            "message": {"role": "assistant", "content": content}}]})
    def factory(**kwargs):
        kwargs['http_client'].close()
        kwargs['http_client'] = httpx.Client(transport=httpx.MockTransport(respond))
        return openai.OpenAI(**kwargs)
    monkeypatch.setattr(mn, 'OpenAI', factory)
    provider = {"endpoint": "https://example.openai.azure.com", "key": "test-placeholder", "model": "test-model"}
    if success:
        assert mn.generate_draft('synthetic only', provider) == DRAFT
    else:
        with pytest.raises(ValueError):
            mn.generate_draft('synthetic only', provider)
    assert len(requests) == 1
    assert str(requests[0].url) == 'https://example.openai.azure.com/openai/v1/chat/completions'
    data = json.loads(requests[0].content)
    assert data['store'] is False and 'tools' not in data
    assert data['response_format'] == {'type': 'json_object'}


def test_unattended_meetings_do_not_require_saving_or_finalizing(notes):
    for _ in range(8):
        review = start(notes); finish(notes, review)
    assert len(list(notes.state_folder.glob("*.json"))) == 8
    assert len(notes.reviews) == 8
    assert notes.current is None
    assert start(notes)


def test_one_retry_after_summary_error(notes, monkeypatch):
    def fail(*_): raise RuntimeError("simulated failure")
    monkeypatch.setattr(mn, "generate_draft", fail)
    review = start(notes); finish(notes, review)
    assert review.public()["canSummarize"]
    monkeypatch.setattr(mn, "generate_draft", lambda *_: DRAFT)
    asyncio.run(notes.summarize(review))
    assert review.draft == DRAFT and not review.error
    assert not review.raw_deadline


def test_removed_task_is_not_saved(notes, tmp_path):
    review = start(notes); finish(notes, review)
    result = notes.export(review.id, {"draft": DRAFT | {"tasks": []}, "revision": review.revision})
    assert json.loads(result["text"])["tasks"] == []


def test_restart_restores_notes_and_edits_without_raw(notes):
    review = start(notes); finish(notes, review)
    edited = DRAFT | {"summary": "Manuell korrigiert", "tasks": []}
    notes.export(review.id, {"draft": edited, "revision": review.revision})
    folder, rid = notes.folder, review.id
    notes.shutdown()
    restored = mn.Notes(folder)
    result = restored.reviews[rid]
    assert result.draft == edited and result.edited and result.public()["editable"]
    assert not result.provider and not result.session.transcript.characters
    assert result.public()["savedPath"]
    assert not restored.options["speechKey"]
    restored.shutdown()


def test_disk_failure_retains_edits_and_recovers_automatically(notes, monkeypatch):
    from pathlib import Path
    review = start(notes); finish(notes, review)
    original = Path.replace
    def fail(*_): raise OSError("private path/key must not leak")
    monkeypatch.setattr(Path, "replace", fail)
    result = notes.export(review.id, {"draft": DRAFT | {"summary": "Korrektur"}, "revision": review.revision})
    assert result["storeError"] and review.store_error
    assert "private" not in review.store_error
    monkeypatch.setattr(Path, "replace", original)
    review.store_retry = 0
    notes.sweep()
    assert not review.store_error
    assert json.loads(Path(review.saved_path).read_text(encoding="utf-8"))["summary"] == "Korrektur"


def test_stale_editor_cannot_overwrite_newer_changes(notes):
    review = start(notes); finish(notes, review)
    old = review.revision
    notes.export(review.id, {"draft": DRAFT | {"summary": "Neu"}, "revision": old})
    with pytest.raises(ValueError):
        notes.export(review.id, {"draft": DRAFT, "revision": old})
    assert review.draft["summary"] == "Neu"


def test_live_notes_are_incremental_saved_readonly_and_finalized(notes, monkeypatch):
    calls = []
    def generate(text, provider):
        calls.append(text)
        return json.loads(json.dumps(DRAFT))
    monkeypatch.setattr(mn, "generate_draft", generate)
    async def run():
        review = start(notes)
        await notes.live_preview(review)
        assert review.draft and review.saved_path and not review.public()["editable"]
        assert review.session.transcript.characters and review.provider
        with pytest.raises(ValueError):
            notes.export(review.id, {"draft": DRAFT, "revision": review.revision})
        review.session.transcript.add("mixed", "Guest-2", "NEUER ABSCHNITT", 20)
        await notes.live_preview(review)
        assert "VERTRAULICHES" not in calls[1] and "NEUER ABSCHNITT" in calls[1]
        assert DRAFT["summary"] in calls[1]
        notes.current = None
        await notes.finish(review)
        assert "VERTRAULICHES" in calls[2] and "NEUER ABSCHNITT" in calls[2]
        assert review.public()["editable"] and not review.session.transcript.characters
        assert json.loads(mn.Path(review.saved_path).read_text(encoding="utf-8"))["phase"] == "complete"
    asyncio.run(run())


def test_finish_waits_for_live_result_then_runs_final_without_overlap(notes, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def generate(*_):
        calls.append(1)
        if len(calls) == 1:
            entered.set(); release.wait(3)
        return DRAFT
    monkeypatch.setattr(mn, "generate_draft", generate)
    async def run():
        review = start(notes)
        review.live_task = asyncio.create_task(notes.live_preview(review))
        await asyncio.to_thread(entered.wait, 2)
        notes.current = None
        ending = asyncio.create_task(notes.finish(review))
        await asyncio.sleep(.05)
        assert len(calls) == 1 and review.busy
        release.set()
        await ending
        assert len(calls) == 2 and review.phase == "complete"
    asyncio.run(run())


def test_live_interval_only_calls_model_for_new_content(notes, monkeypatch):
    calls = []
    monkeypatch.setattr(mn, "generate_draft", lambda *a: calls.append(a) or DRAFT)
    async def run():
        review = start(notes)
        review.session.transcript.add("mixed", "Guest-1", "Mehr Gespräch. " * 30)
        notes.tick()
        assert not review.live_task
        review.next_live = 0
        notes.tick()
        assert review.live_task
        await review.live_task
        review.next_live = 0
        notes.tick()
        assert not review.live_task and len(calls) == 1
    asyncio.run(run())


def test_interrupted_live_notes_survive_restart_as_incomplete(notes):
    review = start(notes)
    asyncio.run(notes.live_preview(review))
    restored = mn.Notes(notes.folder)
    result = restored.reviews[review.id]
    assert result.phase == "incomplete" and result.error
    assert result.draft == DRAFT and result.public()["editable"]
    restored.shutdown()


def test_failed_final_retries_without_user_and_preserves_preview(notes, monkeypatch):
    async def run():
        review = start(notes)
        await notes.live_preview(review)
        monkeypatch.setattr(mn, "generate_draft", lambda *_: (_ for _ in ()).throw(ValueError("failed")))
        notes.current = None
        await notes.finish(review)
        assert review.draft and review.retry_at and review.phase == "incomplete"
        monkeypatch.setattr(mn, "generate_draft", lambda *_: DRAFT)
        review.retry_at = time.monotonic() - 1
        notes.tick()
        await asyncio.gather(*notes.jobs)
        assert review.phase == "complete" and not review.error
    asyncio.run(run())


def test_api_saves_edits_with_revision_and_opens_only_known_folder(notes, monkeypatch):
    from engine import notes_api
    review = start(notes); finish(notes, review)
    opened = []
    monkeypatch.setattr(notes_api.os, "startfile", opened.append)
    client = TestClient(app.api)
    data = client.get('/api/notes').json()
    assert data['storagePath'] == str(notes.folder)
    assert client.post('/api/notes/open-folder', json={'path': 'ignored'}).json()['ok']
    assert opened == [str(notes.folder)]
    response = client.post(f'/api/notes/{review.id}/save', json={'draft': DRAFT, 'revision': review.revision})
    assert response.json()['ok']
    assert response.json()['revision'] == review.revision
    bad = client.post(f'/api/notes/{review.id}/save', json={'draft': DRAFT, 'revision': -1})
    assert not bad.json()['ok']


def test_retry_after_lost_save_reply_is_idempotent(notes):
    review = start(notes); finish(notes, review)
    body = {"draft": DRAFT | {"summary": "Korrektur"}, "revision": review.revision}
    first = notes.export(review.id, body)
    retry = notes.export(review.id, body)
    assert retry["revision"] == first["revision"]
    assert review.draft["summary"] == "Korrektur"


def test_notes_window_close_keeps_pending_edits_alive(notes, monkeypatch):
    from types import SimpleNamespace
    class Event:
        def __iadd__(self, callback): self.callback = callback; return self
    hidden = []
    window = SimpleNamespace(events=SimpleNamespace(closed=Event(), closing=Event(), loaded=Event(), resized=Event()), hide=lambda: hidden.append(True))
    monkeypatch.setattr(app, "STATE", app.AppState())
    monkeypatch.setattr(app.webview, "create_window", lambda *a, **k: window)
    app._show_notes_window()
    assert window.events.closing.callback() is False
    assert hidden and app.STATE.notes_window is window
    app.STATE.quitting = True
    assert window.events.closing.callback() is True


def test_notes_view_reuses_one_window_and_hides_legacy(notes, monkeypatch):
    class Event:
        def __iadd__(self, callback): self.callback = callback; return self
    created, hidden, presented = [], [], []
    window = SimpleNamespace(events=SimpleNamespace(closed=Event(), closing=Event(), loaded=Event(), resized=Event()), hide=lambda: hidden.append('notes'))
    state = app.AppState()
    state.window = SimpleNamespace(hide=lambda: hidden.append('legacy'), show=lambda: pytest.fail('legacy popup shown'))
    monkeypatch.setattr(app, 'STATE', state)
    monkeypatch.setattr(app.webview, 'create_window', lambda *a, **k: created.append(k) or window)
    monkeypatch.setattr(app, '_present_notes_window', presented.append)
    app._show_notes_window(activate=False)
    window.events.loaded.callback()
    assert presented == [False]
    assert created[0]['hidden'] and not created[0]['on_top'] and not created[0]['focus']
    assert created[0]['width'] == 680
    app._on_settings(None, None)
    assert state.notes_view == 'settings'
    app._on_notes_history(None, None)
    assert state.notes_view == 'history' and len(created) == 1
    assert 'legacy' in hidden and state.notes_window is window
    request = state.notes_view_request
    window.events.closing.callback()
    app._show_window()  # Repeated background status updates must remain quiet.
    assert not state.notes_visible and state.notes_view_request == request
    app._on_show(None, None)
    assert state.notes_visible and state.notes_view == 'meeting'


def test_notes_finish_does_not_reopen_hidden_window(notes, monkeypatch):
    monkeypatch.setattr(app, 'STATE', app.AppState())
    monkeypatch.setattr(app, '_show_notes_window', lambda *a, **k: pytest.fail('completion reopened window'))
    monkeypatch.setattr(app, '_refresh_tray', lambda: None)
    async def quiet(): pass
    monkeypatch.setattr(app, 'broadcast', quiet)
    async def run():
        review = start(notes)
        app.STATE.active = True
        app.STATE.title = 'Test'
        await app._end_meeting('presence inactive')
        for _ in range(60):
            if not review.busy: break
            await asyncio.sleep(.01)
        assert review.draft and review.saved_path and not app.STATE.notes_visible
    asyncio.run(run())


def test_notes_presentation_uses_managed_visibility_before_fitting(notes, monkeypatch):
    from engine import notes_window
    calls = []
    state = app.AppState()
    state.notes_window = object()
    state.notes_visible = True
    monkeypatch.setattr(app, 'STATE', state)
    monkeypatch.setattr(notes_window, 'present', lambda window, activate: calls.append(('present', activate)))
    monkeypatch.setattr(app, '_fit_notes_window', lambda height: calls.append(('fit', height)))
    app._present_notes_window(False)
    assert calls == [('present', False), ('fit', 700)]
    state.notes_visible = False
    app._present_notes_window(False)
    assert len(calls) == 2


def test_notes_fit_is_bounded_and_preserves_hidden_window(notes, monkeypatch):
    calls=[]
    state=app.AppState()
    from engine import notes_window
    state.notes_window = object()
    monkeypatch.setattr(notes_window, 'resize', lambda window, *size: calls.append(size))
    monkeypatch.setattr(app, 'STATE', state)
    app._fit_notes_window(9000)
    assert not calls and state.notes_content_height == 9000
    state.notes_visible=True
    app._fit_notes_window(9000)
    app._fit_notes_window(9000)
    assert calls == [(680, 750)]
    app._fit_notes_window(20)
    assert calls[-1] == (680, 400)


def test_notes_api_current_meeting_and_explicit_view(notes, monkeypatch):
    monkeypatch.setattr(app, 'STATE', app.AppState())
    review = start(notes)
    app.STATE.notes_view = 'settings'
    app.STATE.notes_view_request = 7
    client = TestClient(app.api)
    data = client.get('/api/notes').json()
    assert data['currentId'] == review.id and data['uiView'] == 'settings' and data['uiRequest'] == 7
    sizes=[]
    monkeypatch.setattr(app, '_fit_notes_window', sizes.append)
    assert client.post('/api/notes/fit', json={'height':440}).json()['ok']
    assert sizes == [440]
    assert not client.post('/api/notes/fit', json={'height':'SECRET'}).json()['ok']


def test_notes_auth_error_does_not_add_second_notification(notes, monkeypatch):
    monkeypatch.setattr(app, 'STATE', app.AppState())
    shown=[]
    monkeypatch.setattr(app, '_show_window', lambda: shown.append(True))
    monkeypatch.setattr(app, '_tray_notify', lambda *a: pytest.fail('extra notification'))
    async def quiet(): pass
    monkeypatch.setattr(app, 'broadcast', quiet)
    asyncio.run(app._set_health('auth'))
    assert shown and app.STATE.health == 'auth'
