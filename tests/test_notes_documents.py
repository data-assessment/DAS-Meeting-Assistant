import asyncio
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from test_meeting_notes import notes, start, finish, DEVICES, FakeSession
from engine import meeting_notes as mn, notes_window
from engine.notes_markdown import render
import app

def test_markdown_readable_named_and_selected_tasks_only(notes):
    review = start(notes); finish(notes, review)
    document = Path(review.public()["savedPath"])
    assert document.name == "2026-09-11 10-00-00 – Test-Meeting.md"
    text = document.read_text(encoding="utf-8")
    assert "# 11.09.2026 · 10:00 · Test-Meeting" in text and "## Zusammenfassung" in text and "- [ ] Angebot erstellen" in text
    assert "VERTRAULICHES GESPRÄCH" not in text
    assert not list(notes.folder.glob("*.json"))
    assert 'test-chat-placeholder' not in text
    identity = review.draft["tasks"][0]["id"]
    notes.patch(review.id, {"operationId":"exclude","tasks":{identity:{"included":False}}})
    assert "Angebot erstellen" not in document.read_text(encoding="utf-8")
    assert "Keine Aufgaben ausgewählt" in render(review)
    assert len(review.draft["tasks"]) == 1
    notes.patch(review.id, {"operationId":"include","tasks":{identity:{"included":True,"owner":"Robin"}}})
    assert "Verantwortlich: Robin" in document.read_text(encoding="utf-8")

def test_legacy_migration_preserves_original_and_edits(notes):
    review = start(notes); finish(notes, review)
    original = Path(review.saved_path)
    data = json.loads(original.read_text(encoding="utf-8"))
    data.pop("documentName"); data["schemaVersion"] = 4
    legacy = notes.folder / original.name
    legacy.write_text(json.dumps(data), encoding="utf-8")
    original.unlink()
    Path(review.document_path).unlink()
    restored = mn.Notes(notes.folder)
    result = restored.reviews[review.id]
    assert result.draft == review.draft
    assert Path(result.document_path).exists()
    assert not legacy.exists()
    backup = restored.state_folder / "legacy" / legacy.name
    assert json.loads(backup.read_text(encoding="utf-8")) == data
    filename = result.document_name
    again = mn.Notes(notes.folder)
    assert again.reviews[review.id].document_name == filename
    assert len(list(notes.folder.glob("*.md"))) == 1

def test_unsafe_titles_and_same_time_never_overwrite(notes):
    first = notes.start('../../ACME: Budget? <Test>', '2026-09-11T10:00:00', DEVICES, FakeSession); finish(notes, first)
    second = notes.start(first.title, first.started, DEVICES, FakeSession); finish(notes, second)
    assert first.document_name != second.document_name
    for review in (first,second):
        assert Path(review.document_path).resolve().parent == notes.folder.resolve()
        assert not any(c in review.document_name for c in '/\\:<>?')

def test_markdown_failure_is_not_reported_as_saved_and_recovers(notes, monkeypatch):
    review=start(notes);finish(notes,review)
    saved = review.saved_at
    original = Path.replace
    def fail_markdown(source,target):
        if str(target).endswith('.md'): raise OSError('Cannot write')
        return original(source,target)
    monkeypatch.setattr(Path,'replace',fail_markdown)
    response=notes.patch(review.id,{"operationId":"edit","text":{"summary":"Korrigiert","decisions":"","openQuestions":""}})
    assert response['storeError'] and response['savedAt'] == saved
    assert 'Korrigiert' not in Path(review.document_path).read_text(encoding='utf-8')
    monkeypatch.setattr(Path,'replace',original)
    review.store_retry=0;notes.sweep()
    assert not review.store_error and 'Korrigiert' in Path(review.document_path).read_text(encoding='utf-8')

def test_copy_native_route_confirms_success_and_reports_failure(notes, monkeypatch):
    review=start(notes);finish(notes,review)
    monkeypatch.setattr(app,'STATE',app.AppState())
    copied=[]
    monkeypatch.setattr(notes_window,'copy_text',lambda window,text:copied.append(text))
    client=TestClient(app.api)
    response=client.post(f'/api/notes/{review.id}/copy',json={})
    assert response.json()['ok'] and copied == [render(review)]
    assert copied[0].startswith('# 11.09.2026 · 10:00 · Test-Meeting')
    monkeypatch.setattr(notes_window,'copy_text',lambda *_: (_ for _ in ()).throw(RuntimeError('sensitive detail')))
    response=client.post(f'/api/notes/{review.id}/copy',json={})
    assert not response.json()['ok'] and 'sensitive' not in response.text
    assert client.post(f'/api/notes/{review.id}/copy',json={},headers={'origin':'https://other.example'}).status_code==403


def test_structured_summary_survives_edit_storage_reload_and_copy(notes, monkeypatch):
    review = start(notes); finish(notes, review)
    text = "Ergebnis: Die Migration wird vorbereitet.\n\nTechnik:\n- Bestandsdaten zuerst prüfen.\n- Rollback vorsehen.\n\nDer nächste Termin dient der Abstimmung."
    notes.patch(review.id, {"operationId":"structured-summary", "text":{"summary":text,"decisions":"","openQuestions":""}})
    assert text in Path(review.document_path).read_text(encoding="utf-8")
    restored = mn.Notes(notes.folder).reviews[review.id]
    assert restored.draft["summary"] == text
    monkeypatch.setattr(app, 'STATE', app.AppState())
    copied = []
    monkeypatch.setattr(notes_window, 'copy_text', lambda window, value: copied.append(value))
    assert TestClient(app.api).post(f'/api/notes/{review.id}/copy',json={}).json()['ok']
    assert text in copied[0]
