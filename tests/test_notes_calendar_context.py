import asyncio
import datetime as dt
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_meeting_notes import notes, start, finish
from engine import notes_people as np, notes_calls as nc, attendance, meeting_notes as mn

RESOLVE = np.resolve_people

@pytest.fixture
def calendar(monkeypatch):
    monkeypatch.setattr(np,"get_token_for_scopes",lambda *a,**kw:"placeholder")
    monkeypatch.setattr(np,"signed_in_username",lambda:"")
    monkeypatch.setattr(np.requests,"get",lambda *a,**kw:SimpleNamespace(raise_for_status=lambda:None,json=lambda:{"displayName":"Ich","mail":"me@example.org"}))
    return attendance.CalendarCandidate("https://teams.microsoft.com/test","Projektbesprechung","2026-09-15T09:00:00+00:00","2026-09-15T09:50:00+00:00","Robin",[{"name":"Robin","email":"robin@example.org"}])

def test_early_join_metadata_and_minimal_permission(calendar, monkeypatch):
    scopes=[]
    monkeypatch.setattr(np,"get_token_for_scopes",lambda value,**kw:scopes.append(value) or "placeholder")
    monkeypatch.setattr(attendance,"list_calendar_candidates",lambda *a,**kw:[calendar])
    result=RESOLVE(dt.datetime.fromisoformat("2026-09-15T10:59:26+02:00"))
    assert result.title=="Projektbesprechung" and result.selected
    assert {p["source"] for p in result.people}=={"self","calendar"}
    assert scopes==[["Calendars.Read"]]

def test_multiple_calendar_candidates_require_choice(calendar, monkeypatch, notes):
    second=attendance.CalendarCandidate("https://teams.microsoft.com/other","Anderer Termin",calendar.start,calendar.end,"Moritz",[{"name":"Moritz","email":"moritz@example.org"}])
    monkeypatch.setattr(attendance,"list_calendar_candidates",lambda *a,**kw:[calendar,second])
    result=RESOLVE(dt.datetime.fromisoformat("2026-09-15T09:10:00Z"))
    assert not result.selected and len(result.candidates)==2 and len(result.people)==1
    review=start(notes);review.calendar_candidates=result.candidates
    np.select_calendar(notes,review,result.candidates[1]["id"])
    assert review.title=="Anderer Termin" and review.people[0]["name"]=="Moritz"
    assert review.public()["calendarSelected"]

def test_late_calendar_title_renames_markdown_preserving_old(notes, monkeypatch):
    review=start(notes);finish(notes,review)
    old=Path(review.document_path)
    person=np.person("Robin","robin@example.org","calendar")
    result=np.PeopleResult([person],"","Projektbesprechung",[],"selected")
    monkeypatch.setattr(np,"resolve_people",lambda *_:result)
    asyncio.run(np.load_people(notes,review))
    assert review.title=="Projektbesprechung" and review.calendar_selected
    assert "Projektbesprechung" in review.document_name
    assert Path(review.document_path).exists() and not old.exists()
    assert (notes.state_folder/"superseded"/old.name).exists()
    restored=mn.Notes(notes.folder).reviews[review.id]
    assert restored.calendar_selected and restored.people[0]["name"]=="Robin"

def test_missing_chat_access_does_not_stop_calendar_retries(notes, monkeypatch):
    review=start(notes);hits=[]
    async def load(*_): hits.append(True)
    async def refresh(*_): review.people_access_needed=True
    async def sleep(*_): notes.closed=True
    monkeypatch.setattr(np,"load_people",load);monkeypatch.setattr(nc,"refresh_people",refresh);monkeypatch.setattr(nc.asyncio,"sleep",sleep)
    asyncio.run(nc.watch_people(notes,review))
    assert len(hits)>=2

def test_calendar_without_grant_has_explicit_access_state(monkeypatch):
    monkeypatch.setattr(np,"get_token_for_scopes",lambda *a,**kw:None)
    monkeypatch.setattr(np,"signed_in_username",lambda:"")
    monkeypatch.setattr(np.requests,"get",lambda *a,**kw:pytest.fail("Unauthenticated request"))
    result=RESOLVE(dt.datetime.now().astimezone())
    assert result.access_needed and not result.selected

def test_contacts_are_not_claimed_as_call_participants(monkeypatch):
    monkeypatch.setattr(nc,"get_token_for_scopes",lambda *a,**kw:"placeholder")
    def graph(url,headers,params=None):
        assert headers["Prefer"]=="include-unknown-enum-members"
        return {"value":[{"chatType":"oneOnOne","members":[{"displayName":"Robin","email":"robin@example.org"}]}]} if url.endswith('/me/chats') else {"value":[]}
    monkeypatch.setattr(nc,"graph_json",graph)
    people,note,access=nc.call_people("2026-09-15T10:00:00Z")
    assert len(people)==1 and people[0]["source"]=="contact" and "Teilnahme ist nicht bestätigt" in note
    assert not access
    merged=nc.merge_people(people,[np.person("Robin","robin@example.org","calendar")])
    assert len(merged)==1 and merged[0]["source"]=="calendar"


def test_outlook_invitation_without_online_link_is_used(calendar, monkeypatch):
    calendar.join_url = ''
    def candidates(*args, **kwargs):
        assert kwargs['online_only'] is False
        return [calendar]
    monkeypatch.setattr(attendance, 'list_calendar_candidates', candidates)
    result = RESOLVE(dt.datetime.fromisoformat('2026-09-15T09:10:00Z'))
    assert result.selected and result.title == calendar.subject
    assert result.candidates[0]['organizer'] == 'Robin'


def test_ambiguous_invitation_survives_restart_and_exports_roster(notes, calendar, monkeypatch):
    from engine.notes_markdown import render
    from engine.notes_onenote import page_html
    calendar.invitees += [{'name': 'Ich', 'email': 'me@example.org'}]
    second = attendance.CalendarCandidate('', 'Anderer Termin', calendar.start, calendar.end, 'Moritz', [{'name': 'Moritz', 'email': 'moritz@example.org'}])
    monkeypatch.setattr(attendance, 'list_calendar_candidates', lambda *a, **kw: [calendar, second])
    result = RESOLVE(dt.datetime.fromisoformat('2026-09-15T09:10:00Z'))
    monkeypatch.setattr(np, 'resolve_people', lambda *_: result)
    review = start(notes); finish(notes, review)
    asyncio.run(np.load_people(notes, review))
    assert not review.calendar_selected and 'Robin' not in render(review)
    restarted = mn.Notes(notes.folder)
    restored = restarted.reviews[review.id]
    assert len(restored.public()['calendarCandidates']) == 2 and restored.calendar_note
    np.select_calendar(restarted, restored, result.candidates[0]['id'])
    assert restored.title == calendar.subject
    assert {p['name'] for p in restored.people} == {'Robin', 'Ich'}
    again = mn.Notes(notes.folder).reviews[review.id]
    assert again.public()['calendarContext']['organizer'] == 'Robin'
    for output in (render(again), page_html(again, 'Author')):
        assert 'Teilnehmer laut Outlook-Einladung' in output
        assert 'robin@example.org' in output and 'me@example.org' in output
        assert 'Moritz' not in output and output.count('robin@example.org') == 1
    assert again.draft == review.draft


def test_one_note_snapshot_cannot_be_reassigned(notes, calendar):
    review = start(notes); finish(notes, review)
    review.calendar_candidates = [{'id': 'event', 'title': calendar.subject, 'start': calendar.start, 'people': []}]
    review.onenote['status'] = 'saved'
    with pytest.raises(ValueError): np.select_calendar(notes, review, 'event')
    assert not review.calendar_selected


def test_late_lookup_cannot_overwrite_manual_calendar_choice(notes, monkeypatch):
    review = start(notes)
    review.calendar_candidates = [{'id': 'chosen', 'title': 'Gewählter Termin', 'start': '', 'people': []}]
    def resolve(*_):
        np.select_calendar(notes, review, 'chosen')
        return np.PeopleResult([], '', 'Falscher Termin', [], 'wrong')
    monkeypatch.setattr(np, 'resolve_people', resolve)
    asyncio.run(np.load_people(notes, review))
    assert review.title == 'Gewählter Termin' and review.calendar_selected == 'chosen'
    assert review.calendar_candidates[0]['id'] == 'chosen'


def test_calendar_people_not_truncated_by_many_recent_contacts(notes):
    review = start(notes)
    review.people = [np.person(f'Kontakt {i}', f'contact{i}@example.org', 'contact') for i in range(100)]
    invitees = [np.person(f'Person {i}', f'person{i}@example.org', 'calendar') for i in range(150)]
    review.calendar_candidates = [{'id': 'event', 'title': 'Großes Meeting', 'start': '', 'people': invitees}]
    np.select_calendar(notes, review, 'event')
    again = mn.Notes(notes.folder).reviews[review.id]
    assert len([p for p in again.people if p['source'] == 'calendar']) == 150
    assert len(again.public()['calendarContext']['people']) == 150


def test_calendar_refresh_available_without_task_and_rejects_cross_origin(notes, monkeypatch):
    import app
    from fastapi.testclient import TestClient
    review = start(notes)
    client = TestClient(app.api)
    calls = []
    async def load(*_): calls.append(True)
    monkeypatch.setattr(np, 'load_people', load)
    route = f'/api/notes/{review.id}/calendar-refresh'
    assert client.post(route, json={}, headers={'Origin': 'https://example.org'}).status_code == 403
    assert not calls
    assert client.post(route, json={}).json()['ok'] and calls
