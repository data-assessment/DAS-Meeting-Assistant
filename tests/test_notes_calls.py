import asyncio
import datetime as dt
from types import SimpleNamespace
import pytest
from engine import notes_calls as nc

START = "2026-09-14T10:00:00Z"
END = "2026-09-14T10:30:00Z"
MEMBERS = [dict(userId="me",displayName="Alex",email="me@example.org"), dict(userId="other",displayName="Robin",email="robin@example.org")]

def message(ended=False, call="call-1", stamp=None):
    event = {"@odata.type": "#microsoft.graph.callEndedEventMessageDetail" if ended else "#microsoft.graph.callStartedEventMessageDetail", "callId": call, "callEventType": "call"}
    if ended:
        event.update(callDuration="PT30M", callParticipants=[{"participant":{"user":{"id":"other","displayName":"Robin"}}}])
    return dict(messageType="systemEventMessage",createdDateTime=stamp or (END if ended else START),eventDetail=event)


def fake_graph(monkeypatch, chats, messages):
    monkeypatch.setattr(nc, "get_token_for_scopes", lambda *a, **kw: "placeholder")
    calls=[]
    def get(url,headers,params=None):
        calls.append((url,params))
        if url.endswith('/members'): return {'value': MEMBERS}
        return {"value":chats if url.endswith("/me/chats") else messages}
    monkeypatch.setattr(nc,"graph_json",get)
    return calls


def chat(preview=None, kind="oneOnOne"):
    return dict(id="chat/id",chatType=kind,members=MEMBERS,lastMessagePreview=preview or message())


def test_one_to_one_start_supplies_other_person(monkeypatch):
    fake_graph(monkeypatch,[chat()],[message()])
    people,note,access=nc.call_people(START)
    assert {p["name"] for p in people} == {"Alex","Robin"}
    assert not note and not access


def test_message_after_call_does_not_hide_participants(monkeypatch):
    preview=dict(messageType="message",createdDateTime="2026-09-14T10:31:00Z",body={"content":"PRIVATE CHAT DO NOT KEEP"})
    calls=fake_graph(monkeypatch,[chat(preview)],[message(True)])
    people,note,access=nc.call_people(START,END)
    assert [p["name"] for p in people] == ["Robin"]
    assert people[0]["email"] == "robin@example.org"
    assert "PRIVATE" not in str((people,note))
    assert calls[1][1]["$orderby"] == "lastModifiedDateTime desc"


def test_group_start_members_are_not_attendance(monkeypatch):
    fake_graph(monkeypatch,[chat(kind="group")],[message()])
    assert not any(p["source"] == "call" for p in nc.call_people(START)[0])


@pytest.mark.parametrize("messages", [[message(stamp="2026-09-14T09:00:00Z")], [message(call="a"),message(call="b")], []])
def test_unrelated_or_ambiguous_calls_do_not_assign(monkeypatch,messages):
    preview=dict(messageType="message",createdDateTime=START)
    fake_graph(monkeypatch,[chat(preview)],messages)
    assert not any(p["source"] == "call" for p in nc.call_people(START)[0])


def test_finished_other_call_not_matched():
    assert nc.event_match(message(True),nc.instant(START),nc.instant("2026-09-14T11:00:00Z")) is None
    assert nc.event_match(dict(messageType="message",createdDateTime=START),nc.instant(START)) is None


def test_no_permission_never_opens_browser(monkeypatch):
    calls=[]
    monkeypatch.setattr(nc,"get_token_for_scopes",lambda *a,**kw: calls.append(kw) or None)
    assert nc.call_people(START) == ([],"",True)
    assert calls == [{"interactive":False}]


def test_external_nextlink_rejected_before_network(monkeypatch):
    monkeypatch.setattr(nc.requests,"get",lambda *a,**k: pytest.fail("token forwarded"))
    with pytest.raises(ValueError): nc.graph_json("https://example.org/v1.0/chats",{"Authorization":"placeholder"})


def test_request_does_not_follow_redirect_or_log_error(monkeypatch,capsys):
    def get(*a,**kw):
        assert kw["allow_redirects"] is False
        return SimpleNamespace(status_code=403)
    monkeypatch.setattr(nc.requests,"get",get)
    with pytest.raises(PermissionError): nc.graph_json(nc.GRAPH+"/me/chats",{})
    assert not capsys.readouterr().out


def test_late_call_people_preserve_manual_edits_and_other_review(monkeypatch):
    person=dict(id="other",name="Robin",email="",source="call")
    monkeypatch.setattr(nc,"call_people",lambda *_: ([person],"",False))
    r=SimpleNamespace(onenote={},started=START,ended=END,discarded=False,people=[],people_note="",people_access_needed=True,draft={"manual":"untouched"})
    n=SimpleNamespace(closed=False,persist=lambda _:None)
    asyncio.run(nc.refresh_people(n,r))
    assert r.people == [person] and r.draft == {"manual":"untouched"}
    r.discarded=True
    monkeypatch.setattr(nc,"call_people",lambda *_: ([],"do not overwrite",True))
    asyncio.run(nc.refresh_people(n,r))
    assert not r.people_note and not r.people_access_needed


def test_merge_keeps_selected_id_and_self():
    existing=[dict(id="old",name="me@example.org",email="me@example.org",source="self")]
    result=nc.merge_people(existing,[dict(id="new",name="Alex",email="me@example.org",source="call")])
    assert result == [dict(id="old",name="Alex",email="me@example.org",source="self")]


def test_people_connect_route_preserves_recording_and_requires_origin(monkeypatch):
    import app
    from fastapi.testclient import TestClient
    from engine import graph_auth
    r=SimpleNamespace(onenote={},started=START,ended=END,discarded=False,people=[],people_note="",people_access_needed=True)
    notes=SimpleNamespace(reviews={"meeting":r},closed=False,persist=lambda _:None)
    monkeypatch.setattr(app,"NOTES",notes)
    monkeypatch.setattr(app.STATE,"active",True)
    monkeypatch.setattr(graph_auth,"get_token_for_scopes",lambda *a,**kw:"placeholder")
    monkeypatch.setattr(nc,"call_people",lambda *_:([],"",False))
    client=TestClient(app.api)
    path="/api/notes/meeting/people-connect"
    assert client.post(path,json={},headers={"Origin":"https://example.org"}).status_code == 403
    assert client.post(path,json={}).json()["ok"]
    assert app.STATE.active is True


def test_graph_person_roundtrips_in_saved_notes():
    from engine.notes_schema import Person
    person=Person(id="a",name="Robin",email="robin@example.org",source="call")
    assert Person.model_validate_json(person.model_dump_json()) == person


def test_group_members_are_available_without_a_matching_call(monkeypatch):
    preview = dict(messageType='message', createdDateTime=START)
    fake_graph(monkeypatch, [chat(preview, kind='group')], [])
    people, note, access = nc.call_people(START)
    assert {p['name'] for p in people} == {'Alex', 'Robin'}
    assert all(p['source'] == 'contact' for p in people)
    assert 'nicht bestätigt' in note and not access


def test_partial_recording_matches_ended_call(monkeypatch):
    fake_graph(monkeypatch, [chat(message(True), kind='meeting')], [message(True)])
    people, note, access = nc.call_people('2026-09-14T10:15:00Z', '2026-09-14T10:20:00Z')
    assert people[0]['name'] == 'Robin' and people[0]['source'] == 'call'
    assert not note and not access


def test_old_start_does_not_override_known_incompatible_end(monkeypatch):
    fake_graph(monkeypatch, [chat(message(True))], [message(), message(True)])
    people, _, _ = nc.call_people(START, '2026-09-14T11:00:00Z')
    assert people and all(p['source'] == 'contact' for p in people)


def test_full_member_pages_resolve_participant_email(monkeypatch):
    c = chat(message(True), kind='meeting')
    c['members'] = MEMBERS[:1]
    fake_graph(monkeypatch, [c], [message(True)])
    original = nc.graph_json
    def graph(url, headers, params=None):
        if url.endswith('/members'): return {'value': MEMBERS[:1], '@odata.nextLink': nc.GRAPH + '/next-members'}
        if url.endswith('/next-members'): return {'value': MEMBERS[1:]}
        return original(url, headers, params)
    monkeypatch.setattr(nc, 'graph_json', graph)
    people, _, _ = nc.call_people(START, END)
    assert people[0]['email'] == 'robin@example.org'


def test_additional_chat_contacts_remain_available_with_calendar(monkeypatch):
    p = dict(id='contact', name='Robin', email='robin@example.org', source='contact')
    monkeypatch.setattr(nc, 'call_people', lambda *_: ([p], 'unconfirmed', False))
    calendar = dict(id='invited', name='Anna', email='anna@example.org', source='calendar')
    r = SimpleNamespace(onenote={}, started=START, ended=END, discarded=False, people=[calendar], people_note='', people_access_needed=False)
    asyncio.run(nc.refresh_people(SimpleNamespace(closed=False, persist=lambda _: None), r))
    assert r.people == [calendar, p]
    assert not r.people_note
