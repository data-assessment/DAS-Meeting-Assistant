"""Notes documents and OneNote pages follow the app language; task sync reads both."""
import asyncio
from pathlib import Path

import pytest
from engine import notes_i18n, notes_onenote as no, notes_onenote_tasks as ts
from engine.notes_markdown import render
from engine.notes_schema import Draft
from test_meeting_notes import notes, start, finish
from test_notes_onenote import setup, choose, FakeGraph
from test_notes_onenote_tasks import Remote, edit, sync


@pytest.fixture(autouse=True)
def german_default():
    notes_i18n.set_language("de")
    yield
    notes_i18n.set_language("de")


def owner_open(review):
    task = review.draft["tasks"][0]
    # Question labels are meeting content, not document labels.
    task.update(owner="", included=True, due="Freitag", recipient="Kunde", questions=[], uncertainty="")
    return task


def test_markdown_document_in_english(notes):
    review = start(notes); finish(notes, review)
    owner_open(review)
    notes_i18n.set_language("en")
    text = render(review)
    for label in ("**Status:** Complete", "## Summary", "## Tasks", "  - Owner: To be clarified",
                  "  - Recipient: Kunde", "  - Due: Freitag"):
        assert label in text
    for german in ("Zusammenfassung", "Aufgaben", "Verantwortlich", "Noch zu klären", "Empfänger", "Termin:", "Stand"):
        assert german not in text
    for task in review.draft["tasks"]:
        task["included"] = False
    assert "No tasks selected." in render(review)


def test_markdown_document_in_german(notes):
    review = start(notes); finish(notes, review)
    owner_open(review)
    text = render(review)
    for label in ("**Stand:** Abgeschlossen", "## Zusammenfassung", "## Aufgaben", "  - Verantwortlich: Noch zu klären",
                  "  - Empfänger: Kunde", "  - Termin: Freitag"):
        assert label in text
    assert "## Tasks" not in text and "Owner:" not in text


def test_saved_document_follows_language_at_write_time(notes):
    notes_i18n.set_language("en")
    review = start(notes); finish(notes, review)
    text = Path(review.public()["savedPath"]).read_text(encoding="utf-8")
    assert "## Summary" in text and "## Tasks" in text and "## Zusammenfassung" not in text


def test_onenote_page_and_task_lines_in_both_languages(setup):
    _, review = setup
    task = owner_open(review)
    notes_i18n.set_language("en")
    page = no.page_html(review, "Robin")
    assert "Notes by Robin" in page and "<h2>Summary</h2>" in page and "<h2>Tasks</h2>" in page
    lines = ts.snapshot(Draft.model_validate(review.draft).model_dump())
    texts = [line["text"] for line in lines.values()]
    assert task["title"] + " — Owner: to be clarified — Due: Freitag" in texts
    assert "Recipient: Kunde" in texts
    assert ts.snapshot({"tasks": []})["tasks-empty"]["text"] == "No tasks selected."
    notes_i18n.set_language("de")
    page = no.page_html(review, "Robin")
    assert "Notizen von Robin" in page and "<h2>Zusammenfassung</h2>" in page and "<h2>Aufgaben</h2>" in page
    assert ts.snapshot({"tasks": []})["tasks-empty"]["text"] == "Keine Aufgaben ausgewählt."


@pytest.mark.parametrize("page_language, later_language", [("de", "en"), ("en", "de"), ("en", "en")])
def test_task_sync_recognizes_german_and_english_pages(setup, monkeypatch, page_language, later_language):
    notes, review = setup
    notes_i18n.set_language(page_language)
    choose(notes, review)
    asyncio.run(notes.onenote.publish(review, review.revision))
    content = FakeGraph.created[0][1]
    assert ("<h2>Aufgaben</h2>" if page_language == "de" else "<h2>Tasks</h2>") in content
    remote = Remote(content)
    monkeypatch.setattr(FakeGraph, "get", lambda self, url: remote.get(url), raising=False)
    monkeypatch.setattr(FakeGraph, "update_tasks", lambda self, url, commands: remote.update(url, commands), raising=False)
    notes_i18n.set_language(later_language)
    edit(notes, review, owner="Customer", ownerId="p")
    sync(notes, review)
    assert review.onenote["taskSync"]["status"] == "saved", review.onenote["taskSync"]
    assert len(remote.writes) == 1 and "Customer" in remote.main().text


def test_task_headings_come_from_the_catalog_in_every_language():
    assert ts.task_headings() == notes_i18n.variants("document.headings.tasks") == ("Aufgaben", "Tasks")
    notes_i18n.set_language("en")
    assert notes_i18n.t("document.headings.tasks") == "Tasks"
    assert ts.task_headings() == ("Aufgaben", "Tasks")


def test_task_page_without_recognized_heading_still_fails_closed():
    content = ('<html><body><p id="m" data-id="meeting-x">x</p><h2 id="h">Tâches</h2>'
               '<p id="p" data-id="tasks-empty">Keine Aufgaben ausgewählt.</p></body></html>')
    with pytest.raises(ts.Conflict):
        ts.plan(content, "x", {"tasks-empty": {"text": "Keine Aufgaben ausgewählt.", "todo": False}}, {})


def test_english_conflict_message():
    notes_i18n.set_language("en")
    assert str(ts.Conflict()).startswith("This task was changed in OneNote")
    notes_i18n.set_language("de")
    assert str(ts.Conflict()).startswith("Diese Aufgabe wurde in OneNote geändert")


def test_english_legacy_owner_uncertainty_is_an_owner_question():
    draft = Draft.model_validate({"summary": "", "decisions": "", "openQuestions": "", "tasks": [
        {"title": "A", "owner": "", "recipient": "", "due": "", "uncertainty": "Owner missing."},
        {"title": "B", "owner": "", "recipient": "", "due": "", "uncertainty": "Zuständigkeit fehlt."}]})
    assert [task.questions[0].field for task in draft.tasks] == ["owner", "owner"]
