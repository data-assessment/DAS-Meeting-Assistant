import asyncio
import copy
import datetime as dt
import json
from types import SimpleNamespace

import pytest
from engine import meeting_notes as mn, notes_people
from engine.notes_schema import Draft
from test_meeting_notes import notes, start, finish, DRAFT


@pytest.fixture(autouse=True)
def no_account_cache(monkeypatch):
    monkeypatch.setattr(notes_people, "signed_in_username", lambda: "")


def test_old_notes_upgrade_and_selection_survive_restart(notes):
    review = start(notes); finish(notes, review)
    path = mn.Path(review.saved_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["schemaVersion"] = 2
    saved.pop("people", None)
    for task in saved["tasks"]:
        for key in ("id", "ownerId", "questions"): task.pop(key, None)
    path.write_text(json.dumps(saved), encoding="utf-8")
    loaded = mn.Notes(notes.folder)
    r = loaded.reviews[review.id]
    task = r.draft["tasks"][0]
    task_id = task["id"]
    task["owner"] = "Robin"
    task["questions"] = [{"id": "order", "label": "Review-Reihenfolge", "field": "context", "options": ["Vor Migration", "Nach Migration"], "answer": "Vor Migration"}]
    loaded.export(r.id, {"draft": r.draft, "revision": r.revision})
    again = mn.Notes(notes.folder).reviews[r.id]
    assert again.draft["tasks"][0]["id"] == task_id
    assert again.draft["tasks"][0]["questions"][0]["answer"] == "Vor Migration"
    assert again.draft["tasks"][0]["owner"] == "Robin"


def test_manual_people_and_calendar_survive_restart(notes):
    r = start(notes); finish(notes, r)
    r.people = [{"id": "calendar-1", "name": "Robin", "email": "robin@example.org", "source": "calendar"}]
    d = copy.deepcopy(r.draft)
    d["people"] = [{"id": "manual-1", "name": "Klara", "email": "", "source": "manual"}]
    d["tasks"][0].update(owner="Klara", ownerId="manual-1")
    notes.export(r.id, {"draft": d, "revision": r.revision})
    restored = mn.Notes(notes.folder).reviews[r.id]
    assert restored.people == r.people
    assert restored.draft == d


def test_answer_can_be_changed_without_losing_title_or_owner(notes):
    r = start(notes); finish(notes, r)
    d = copy.deepcopy(r.draft)
    d["tasks"][0].update(title="Manuell bearbeitet", owner="Moritz", questions=[dict(id="q", label="Wann?", field="context", options=["Vorher", "Nachher"], answer="Vorher")])
    notes.export(r.id, {"draft": d, "revision": r.revision})
    d = copy.deepcopy(r.draft)
    d["tasks"][0]["questions"][0]["answer"] = "Nachher"
    notes.export(r.id, {"draft": d, "revision": r.revision})
    assert r.draft["tasks"][0]["title"] == "Manuell bearbeitet"
    assert r.draft["tasks"][0]["owner"] == "Moritz"
    assert r.draft["tasks"][0]["questions"][0]["answer"] == "Nachher"


def test_invalid_choice_and_duplicate_ids_rejected():
    d = copy.deepcopy(DRAFT)
    d["tasks"][0]["questions"] = [dict(id="q", label="Wann?", options=["A", "B"], answer="C")]
    with pytest.raises(ValueError): Draft.model_validate(d)
    d = copy.deepcopy(DRAFT); d["tasks"].append(copy.deepcopy(d["tasks"][0]))
    with pytest.raises(ValueError): Draft.model_validate(d)


def candidate(start, end, name="Calendar person"):
    return SimpleNamespace(start=start, end=end, invitees=[dict(name=name,email="person@example.org")])

@pytest.mark.parametrize("count,calendar_expected", [(0, False), (1, True), (2, False)])
def test_calendar_uses_only_unique_overlapping_event(monkeypatch, count, calendar_expected):
    from engine import attendance
    monkeypatch.setattr(notes_people, "get_token_for_scopes", lambda *a, **kw: "placeholder")
    monkeypatch.setattr(attendance, "list_calendar_candidates", lambda *a, **kw: [candidate("2026-09-14T10:00:00Z", "2026-09-14T11:00:00Z")] * count)
    monkeypatch.setattr(notes_people.requests, "get", lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None,json=lambda: dict(displayName="Ich",mail="me@example.org")))
    values, note = notes_people.resolve_people(dt.datetime(2026,9,14,10,30,tzinfo=dt.timezone.utc))
    assert any(p["source"] == "self" for p in values)
    assert any(p["source"] == "calendar" for p in values) is calendar_expected
    assert bool(note) is not calendar_expected


def test_early_join_uses_unique_nearby_calendar_event(monkeypatch):
    from engine import attendance
    monkeypatch.setattr(notes_people, "get_token_for_scopes", lambda *a, **kw: "placeholder")
    monkeypatch.setattr(attendance, "list_calendar_candidates", lambda *a, **kw: [candidate("2026-09-14T10:00:00Z", "2026-09-14T11:00:00Z")])
    monkeypatch.setattr(notes_people.requests, "get", lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None,json=lambda: {}))
    values, note = notes_people.resolve_people(dt.datetime(2026,9,14,9,58,tzinfo=dt.timezone.utc))
    assert len(values) == 1 and values[0]["source"] == "calendar" and not note


def test_late_lookup_updates_original_meeting_only(notes, monkeypatch):
    r = start(notes); finish(notes, r)
    second = start(notes)
    monkeypatch.setattr(notes_people, "resolve_people", lambda _: ([dict(id="x",name="Robin",email="",source="calendar")], ""))
    asyncio.run(notes_people.load_people(notes, r))
    assert r.people and not second.people
    r.discarded = True
    monkeypatch.setattr(notes_people, "resolve_people", lambda _: ([], "changed"))
    asyncio.run(notes_people.load_people(notes, r))
    assert r.people and not r.people_note


def test_self_available_without_profile_permission(monkeypatch):
    from engine import attendance
    monkeypatch.setattr(notes_people, "get_token_for_scopes", lambda *a, **kw: "placeholder")
    monkeypatch.setattr(attendance, "list_calendar_candidates", lambda *a, **kw: [])
    monkeypatch.setattr(notes_people.requests, "get", lambda *a, **k: (_ for _ in ()).throw(ValueError("403")))
    monkeypatch.setattr(notes_people, "signed_in_username", lambda: "me@example.org")
    values, note = notes_people.resolve_people(dt.datetime.now().astimezone())
    assert len(values) == 1 and values[0]["source"] == "self"
    assert values[0]["email"] == "me@example.org"
