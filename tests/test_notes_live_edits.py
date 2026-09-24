import asyncio
import copy
import json
import threading
import uuid
from pathlib import Path
import pytest
from test_meeting_notes import notes, start, finish, DRAFT
from engine import meeting_notes as mn
from engine.notes_edits import merge_generated
from engine.notes_credentials import CredentialError

def change(notes, review, **fields):
    return notes.patch(review.id, {"operationId": str(uuid.uuid4()), **fields})

def test_live_edits_survive_delayed_ai_and_finalization(notes, monkeypatch):
    review = start(notes)
    asyncio.run(notes.live_preview(review))
    task_id = review.draft["tasks"][0]["id"]
    began, release = threading.Event(), threading.Event()
    def delayed(*_):
        began.set(); assert release.wait(5)
        generated = copy.deepcopy(DRAFT)
        generated["summary"] = "Neuer Stand"
        generated["tasks"][0]["id"] = "model-changed-id"
        generated["tasks"][0]["owner"] = "Falsche Person"
        return generated
    monkeypatch.setattr(mn, "generate_draft", delayed)
    async def run():
        task = asyncio.create_task(notes.live_preview(review))
        assert await asyncio.to_thread(began.wait, 5)
        person = {"id":"manual", "name":"Robin", "email":"", "source":"manual"}
        change(notes, review, tasks={task_id:{"owner":"Robin","ownerId":"manual","title":"Manuelle Korrektur","included":False}}, people=[person])
        release.set(); await task
    asyncio.run(run())
    assert review.draft["summary"] == "Neuer Stand"
    assert len(review.draft["tasks"]) == 1
    task = review.draft["tasks"][0]
    assert task["id"] == task_id and task["title"] == "Manuelle Korrektur"
    assert task["owner"] == "Robin" and not task["included"]
    finish(notes, review)
    assert review.draft["tasks"][0] == task
    restored = mn.Notes(notes.folder).reviews[review.id]
    assert not restored.draft["tasks"][0]["included"]
    assert restored.draft["people"][0]["name"] == "Robin"
    assert not review.session.transcript.characters

def test_summary_locked_until_final_but_tasks_editable_while_busy(notes):
    review = start(notes); asyncio.run(notes.live_preview(review))
    review.busy = True
    assert review.public()["tasksEditable"] and not review.public()["editable"]
    identity = review.draft["tasks"][0]["id"]
    change(notes, review, tasks={identity:{"owner":"Moritz"}})
    with pytest.raises(ValueError): change(notes, review, text={"summary":"Too soon", "decisions":"", "openQuestions":""})
    review.busy = False; finish(notes, review)
    text = "Absatz mit korrigierter Erkennung.\n\n" * 600
    change(notes, review, text={"summary":text,"decisions":"","openQuestions":""})
    assert review.draft["summary"] == text
    assert mn.Notes(notes.folder).reviews[review.id].draft["summary"] == text

def test_lost_reply_retries_same_edit_without_overwriting_new_ai(notes):
    review = start(notes); asyncio.run(notes.live_preview(review))
    identity = review.draft["tasks"][0]["id"]
    payload = {"operationId":"retry", "tasks":{identity:{"included":False}}}
    notes.patch(review.id, payload)
    next_draft = copy.deepcopy(DRAFT); next_draft["summary"] = "Aktuelle KI-Zusammenfassung"
    merge_generated(review, next_draft); review.revision += 1
    revision = review.revision
    result = notes.patch(review.id, payload)
    assert result["revision"] == revision
    assert result["draft"]["summary"] == "Aktuelle KI-Zusammenfassung"
    assert not result["draft"]["tasks"][0]["included"]
    with pytest.raises(ValueError): notes.patch(review.id, payload | {"tasks":{identity:{"included":True}}})

def test_withdrawn_row_stays_visible_but_unselected_and_new_tasks_append(notes):
    review = start(notes); asyncio.run(notes.live_preview(review))
    identity = review.draft["tasks"][0]["id"]
    new = copy.deepcopy(DRAFT); new["tasks"][0].update(id="new", title="Release dokumentieren")
    merge_generated(review, new)
    assert [t["id"] for t in review.draft["tasks"]] == [identity, "new"]
    assert not review.draft["tasks"][0]["included"] and not review.draft["tasks"][0]["suggested"]
    change(notes, review, tasks={identity:{"included":True}})
    merge_generated(review, new)
    assert review.draft["tasks"][0]["included"]

def test_invalid_patch_atomic_and_raw_never_persisted(notes):
    review = start(notes); asyncio.run(notes.live_preview(review))
    before = copy.deepcopy(review.draft)
    identity = review.draft["tasks"][0]["id"]
    with pytest.raises(ValueError): change(notes, review, tasks={identity:{"title":"Changed","included":"false"}})
    assert review.draft == before and not review.task_edits
    with pytest.raises(ValueError): change(notes, review, transcript="private")
    change(notes, review, tasks={identity:{"included":False}})
    persisted = Path(review.saved_path).read_text(encoding="utf-8")
    assert "VERTRAULICHES GESPRÄCH" not in persisted and "test-chat-placeholder" not in persisted

def test_dpapi_restart_binding_remove_and_no_cleartext(notes):
    notes.save_credentials()
    encrypted = notes.credentials.path.read_bytes()
    assert b"test-speech-placeholder" not in encrypted and b"test-chat-placeholder" not in encrypted
    plain_settings = {k:v for k,v in notes.options.items() if not k.endswith("Key")}
    restored = mn.Notes(notes.folder)
    restored.configure({"enabled":True, **plain_settings}); restored.load_credentials()
    assert restored.options["speechKey"] == notes.options["speechKey"]
    assert restored.options["chatKey"] == notes.options["chatKey"]
    assert restored.public_options()["speechKeySaved"]
    restored.remove_credential("speechKey")
    again = mn.Notes(notes.folder); again.configure({"enabled":True, **plain_settings}); again.load_credentials()
    assert not again.options["speechKey"] and again.options["chatKey"]
    again.configure({"enabled":True,"endpoint":"https://other.openai.azure.com"}); again.load_credentials()
    assert not again.options["chatKey"]
    assert "test-chat-placeholder" not in json.dumps(again.public_options())

def test_dpapi_tampering_and_save_failure_do_not_fall_back_to_plaintext(notes, monkeypatch):
    notes.save_credentials()
    original = notes.credentials.path.read_bytes()
    damaged = bytearray(original); damaged[-1] ^= 1
    notes.credentials.path.write_bytes(damaged)
    restored = mn.Notes(notes.folder); restored.load_credentials()
    assert restored.credential_error and not restored.options["speechKey"]
    notes.credentials.path.write_bytes(original)
    monkeypatch.setattr(Path, "replace", lambda *_: (_ for _ in ()).throw(OSError("secret")))
    with pytest.raises(CredentialError): notes.save_credentials()
    assert "secret" not in notes.credential_error
    assert notes.credentials.path.read_bytes() == original
    assert b"test-chat-placeholder" not in notes.credentials.path.with_suffix(".tmp").read_bytes()


def test_startup_loads_configured_keys_and_namespaces_are_isolated(notes, monkeypatch, tmp_path):
    import app
    from engine.notes_credentials import CredentialStore
    notes.save_credentials()
    plain = {k:v for k,v in notes.options.items() if not k.endswith("Key")}
    restored = mn.Notes(notes.folder)
    monkeypatch.setattr(app, "NOTES", restored)
    monkeypatch.setattr(app, "STATE", app.AppState())
    app._apply_settings({"meeting_notes_enabled":True,"meeting_notes_options":plain})
    assert restored.enabled and restored.options["speechKey"] and restored.options["chatKey"]
    assert restored.public_options()["chatKeySaved"]
    other = CredentialStore(tmp_path / "Community" / "azure-notes.dpapi")
    other.path.parent.mkdir()
    other.path.write_bytes(notes.credentials.path.read_bytes())
    with pytest.raises(CredentialError): other.load()

def test_capture_failure_is_exposed_before_call_ends(notes):
    review = start(notes)
    review.session.error = "Speech-Verbindung fehlgeschlagen"
    assert review.public()["warning"] == "Speech-Verbindung fehlgeschlagen"
