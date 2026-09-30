"""Notes documents and OneNote pages keep their meeting's language; task sync reads both."""
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

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
    notes_i18n.set_language("en")
    review = start(notes); finish(notes, review)
    assert review.language == "en"
    owner_open(review)
    # The meeting keeps its notes language when the app language changes later.
    notes_i18n.set_language("de")
    text = render(review)
    for label in ("**Status:** Complete", "## Summary", "## Tasks", "  - Owner: To be clarified",
                  "  - Recipient: Kunde", "  - Due: Freitag"):
        assert label in text
    for german in ("Zusammenfassung", "Aufgaben", "Verantwortlich", "Noch zu klären", "Empfänger", "Termin:", "Stand"):
        assert german not in text
    assert re.search(r"\*\*Meeting:\*\* \d{4}-\d{2}-\d{2}, \d{2}:\d{2}", text)
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
    assert re.search(r"\*\*Meeting:\*\* \d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}", text)


def test_saved_document_keeps_the_meeting_language_across_restart(notes, tmp_path):
    notes_i18n.set_language("en")
    review = start(notes); finish(notes, review)
    path = Path(review.public()["savedPath"])
    assert "## Summary" in path.read_text(encoding="utf-8")
    notes_i18n.set_language("de")
    reloaded = type(notes)(notes.folder)
    again = reloaded.reviews[review.id]
    assert again.language == "en"
    reloaded.persist(again)
    text = path.read_text(encoding="utf-8")
    assert "## Summary" in text and "## Zusammenfassung" not in text


def test_meetings_saved_by_older_releases_are_german(notes):
    review = start(notes); finish(notes, review)
    state = next((notes.folder / ".app-state").glob("*.json"))
    data = json.loads(state.read_text(encoding="utf-8"))
    data.pop("language")
    state.write_text(json.dumps(data), encoding="utf-8")
    notes_i18n.set_language("en")
    assert type(notes)(notes.folder).reviews[review.id].language == "de"


def test_summary_prompt_uses_the_meeting_language(monkeypatch):
    from engine import meeting_notes
    seen = []
    monkeypatch.setattr(meeting_notes, "generate_draft", lambda text, provider: seen.append(meeting_notes.system_prompt()) or {})
    review = SimpleNamespace(language="de", provider={})
    notes_i18n.set_language("en")
    asyncio.run(meeting_notes.review_draft(review, "text"))
    assert seen == [meeting_notes.SYSTEM]


def test_onenote_page_and_task_lines_in_both_languages(setup):
    _, review = setup
    task = owner_open(review)
    review.language = "en"
    page = no.page_html(review, "Robin")
    assert "Notes by Robin" in page and "<h2>Summary</h2>" in page and "<h2>Tasks</h2>" in page
    assert "2026-09-16 · 10:00 · Notes by Robin" in page  # unambiguous date for English readers
    # Rendered in the meeting's language without changing the app language.
    assert task["title"] + " — Owner: to be clarified — Due: Freitag" in [line["text"] for line in no.task_lines(review).values()]
    assert notes_i18n.language() == "de"
    notes_i18n.set_language("en")
    lines = ts.snapshot(Draft.model_validate(review.draft).model_dump())
    texts = [line["text"] for line in lines.values()]
    assert task["title"] + " — Owner: to be clarified — Due: Freitag" in texts
    assert "Recipient: Kunde" in texts
    assert ts.snapshot({"tasks": []})["tasks-empty"]["text"] == "No tasks selected."
    notes_i18n.set_language("de")
    review.language = "de"
    page = no.page_html(review, "Robin")
    assert "Notizen von Robin" in page and "<h2>Zusammenfassung</h2>" in page and "<h2>Aufgaben</h2>" in page
    assert "16.09.2026 · 10:00 · Notizen von Robin" in page
    assert ts.snapshot({"tasks": []})["tasks-empty"]["text"] == "Keine Aufgaben ausgewählt."


@pytest.mark.parametrize("page_language, later_language", [("de", "en"), ("en", "de"), ("en", "en")])
def test_task_sync_recognizes_german_and_english_pages(setup, monkeypatch, page_language, later_language):
    notes, review = setup
    review.language = page_language
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
    # Only the edited line is replaced, and the page stays in one language.
    assert len(remote.writes[0][1]) == 1
    labels = ("Termin: 20.09.", "Empfänger: Contoso") if page_language == "de" else ("Due: 20.09.", "Recipient: Contoso")
    assert all(label in remote.text for label in labels)


def test_unrelated_task_changed_in_onenote_does_not_block_after_language_switch(setup, monkeypatch):
    notes, review = setup
    choose(notes, review)
    asyncio.run(notes.onenote.publish(review, review.revision))
    remote = Remote(FakeGraph.created[0][1])
    recipient = next(el for el in remote.root.iter("p") if "Empfänger: Contoso" in "".join(el.itertext()))
    recipient.text, recipient[:] = "Empfänger: Contoso, Einkauf", []  # edited directly in OneNote
    monkeypatch.setattr(FakeGraph, "get", lambda self, url: remote.get(url), raising=False)
    monkeypatch.setattr(FakeGraph, "update_tasks", lambda self, url, commands: remote.update(url, commands), raising=False)
    notes_i18n.set_language("en")
    edit(notes, review, owner="Customer", ownerId="p")
    sync(notes, review)
    assert review.onenote["taskSync"]["status"] == "saved", review.onenote["taskSync"]
    assert "Empfänger: Contoso, Einkauf" in remote.text and "Customer" in remote.main().text


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
