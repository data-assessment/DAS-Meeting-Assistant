import datetime
from types import SimpleNamespace
import pytest

from engine import attendance


def test_attempt_uses_selected_calendar_candidate(monkeypatch):
    calls = []
    candidate = {
        "joinUrl": "https://teams.example/join",
        "subject": "Chosen meeting",
        "invitees": [{"name": "Ada", "email": "ada@example.com"}],
    }

    def fake_resolve(_headers, join_url):
        calls.append(join_url)
        return None

    monkeypatch.setattr(attendance, "resolve_meeting_id", fake_resolve)

    result = attendance._attempt(
        {},
        datetime.datetime(2026, 7, 7, 10, 0),
        datetime.datetime(2026, 7, 7, 10, 30),
        candidate,
    )

    assert calls == ["https://teams.example/join"]
    assert result.subject == "Chosen meeting"
    assert result.attendees == candidate["invitees"]


def test_attempt_can_skip_calendar_mapping():
    result = attendance._attempt(
        {},
        datetime.datetime(2026, 7, 7, 10, 0),
        datetime.datetime(2026, 7, 7, 10, 30),
        skip_calendar=True,
    )

    assert result.meeting_id is None
    assert result.attendees == []
    assert "rejected" in (result.note or "")


def test_calendar_pages_include_normal_outlook_events_and_skip_cancelled(monkeypatch):
    def event(title, **extra):
        return dict(subject=title, start={'dateTime': '2026-09-17T11:00:00.0000000'},
                    end={'dateTime': '2026-09-17T11:30:00.0000000'},
                    organizer={'emailAddress': {'name': 'Anna', 'address': 'anna@example.org'}},
                    attendees=[{'emailAddress': {'name': 'Robin', 'address': 'robin@example.org'}},
                               {'type': 'resource', 'emailAddress': {'name': 'Raum', 'address': 'room@example.org'}}], **extra)
    pages = [{'value': [event('Abgesagt', isCancelled=True), event('Ganztägig', isAllDay=True)], '@odata.nextLink': attendance.GRAPH + '/next'},
             {'value': [event('Vor Ort'), event('Abgelehnt', responseStatus={'response': 'declined'})]}]
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        data = pages.pop(0)
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: data)
    monkeypatch.setattr(attendance.requests, 'get', get)
    start = datetime.datetime.fromisoformat('2026-09-17T13:00:00+02:00')
    result = attendance.list_calendar_candidates({}, start, start, diagnostics=False, online_only=False)
    assert [c.subject for c in result] == ['Vor Ort']
    assert {p['name'] for p in result[0].invitees} == {'Anna', 'Robin'}
    assert result[0].start == '2026-09-17T11:00:00+00:00'
    assert calls[0][1]['params']['startDateTime'] == '2026-09-17T10:55:00.000Z'
    assert calls[1][1]['params'] is None
    assert all(c[1]['allow_redirects'] is False for c in calls)


def test_calendar_foreign_pagination_does_not_forward_token(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
            json=lambda: {'value': [], '@odata.nextLink': 'https://evil.example/v1.0/calendar'})
    monkeypatch.setattr(attendance.requests, 'get', get)
    with pytest.raises(ValueError):
        attendance.list_calendar_candidates({}, datetime.datetime.now(), datetime.datetime.now(), diagnostics=False)
    assert len(calls) == 1


def test_attendance_lookup_still_requires_online_url(monkeypatch):
    monkeypatch.setattr(attendance.requests, 'get', lambda *a, **kw: SimpleNamespace(status_code=200,
        raise_for_status=lambda: None, json=lambda: {'value': [{'subject': 'Vor Ort', 'isOnlineMeeting': False}]}))
    assert attendance.list_calendar_candidates({}, datetime.datetime.now(), datetime.datetime.now(), diagnostics=False) == []
