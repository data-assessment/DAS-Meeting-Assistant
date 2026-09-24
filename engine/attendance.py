"""Look up the Teams attendance report for a finished meeting (delegated Graph).

Presence detection only tells us *that* a meeting happened, not which one — so we
reconstruct the meeting from the signed-in user's calendar:

    calendar event overlapping the meeting window
      -> onlineMeeting.joinUrl
      -> GET /me/onlineMeetings?$filter=JoinWebUrl eq '...'   (the meeting id)
      -> GET /me/onlineMeetings/{id}/attendanceReports/{reportId}?$expand=attendanceRecords

Limits (by Graph design, not this code):
  - delegated /me only sees attendance reports for meetings the user *organized*;
  - a report is generated only when the meeting *session* ends (last participant
    leaves) — not when you leave or when the scheduled slot ends — so it may not
    exist yet; hence the retries;
  - ad-hoc / unscheduled calls have no calendar event and can't be resolved.

The lower-level helpers (graph_headers, resolve_meeting_id, list_attendance_reports,
get_attendance_report) are also used by attendance_cli.py.
"""
import datetime
import time
import urllib.parse
from dataclasses import dataclass

import requests

import config
from engine.graph_auth import get_token
from engine.graph_log import log_graph_call

GRAPH = "https://graph.microsoft.com/v1.0"
_TIMEOUT = 15


@dataclass
class AttendeeLookup:
    """Result of resolving + fetching a meeting's attendance.

    attendees:  list of {name,email,role,seconds} on success; [] when the meeting
                was resolved but is terminally unavailable (ad-hoc / not organizer);
                None while still waiting on the report (retryable).
    note:       human-readable explanation for an empty/None result.
    meeting_id: the resolved onlineMeeting id, when known (handy for the CLI).
    subject:    the calendar event subject, when known.
    """
    attendees: list[dict] | None
    note: str | None
    meeting_id: str | None = None
    subject: str | None = None
    source: str = "Teams attendance report"  # or "calendar invite" for the fallback


@dataclass
class CalendarCandidate:
    """A calendar event that could be the meeting currently being recorded."""
    join_url: str
    subject: str | None
    start: str | None
    end: str | None
    organizer: str | None
    invitees: list[dict]
    # Identifies the meeting across occurrences, so the export dialogs can
    # remember a choice per recurring meeting rather than per meeting name.
    # seriesMasterId is only set on an instance of a recurring series;
    # iCalUId is stable per event and is the fallback for one-offs.
    series_id: str = ""
    ical_uid: str = ""
    response: str = ""


# --------------------------------------------------------------------------- #
# Reusable Graph helpers (shared with the CLI)
# --------------------------------------------------------------------------- #
def graph_headers(interactive: bool = True) -> dict:
    token = get_token(interactive=interactive)
    if not token:
        raise RuntimeError("Microsoft sign-in required")
    return {"Authorization": f"Bearer {token}"}


def resolve_meeting_id(headers: dict, join_url: str) -> str | None:
    quoted = urllib.parse.quote(join_url, safe="")
    url = f"{GRAPH}/me/onlineMeetings?$filter=JoinWebUrl%20eq%20'{quoted}'"
    resp = requests.get(
        url,
        headers=headers, timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        log_graph_call(
            "resolve the Teams online meeting id from the calendar join link",
            "GET",
            url,
            status=resp.status_code,
            error=resp.text,
        )
        resp.raise_for_status()
    value = resp.json().get("value", [])
    log_graph_call(
        "resolve the Teams online meeting id from the calendar join link",
        "GET",
        url,
        status=resp.status_code,
        result={
            "matches": len(value),
            "meetingId": value[0].get("id") if value else None,
            "subject": value[0].get("subject") if value else None,
        },
    )
    return value[0]["id"] if value else None


def list_attendance_reports(headers: dict, meeting_id: str) -> list[dict]:
    """All attendance reports for a meeting (one per session), without records."""
    url = f"{GRAPH}/me/onlineMeetings/{meeting_id}/attendanceReports"
    resp = requests.get(
        url,
        headers=headers, timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        log_graph_call(
            "check whether Teams attendance reports exist for the meeting",
            "GET",
            url,
            status=resp.status_code,
            error=resp.text,
        )
        resp.raise_for_status()
    reports = resp.json().get("value", [])
    log_graph_call(
        "check whether Teams attendance reports exist for the meeting",
        "GET",
        url,
        status=resp.status_code,
        result={
            "meetingId": meeting_id,
            "reportCount": len(reports),
            "sessions": [
                {
                    "start": report.get("meetingStartDateTime"),
                    "end": report.get("meetingEndDateTime"),
                }
                for report in reports
            ],
        },
    )
    return reports


def get_attendance_report(headers: dict, meeting_id: str, report_id: str) -> dict:
    """A single report with its attendanceRecords inlined.

    Expanding on the *collection* endpoint is unreliable (returns reports with
    empty records), so records are fetched per-report by id — the documented form.
    """
    url = (f"{GRAPH}/me/onlineMeetings/{meeting_id}/attendanceReports/{report_id}"
           "?$expand=attendanceRecords")
    resp = requests.get(
        url,
        headers=headers, timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        log_graph_call(
            "read attendance records for a matching meeting session",
            "GET",
            url,
            status=resp.status_code,
            error=resp.text,
        )
        resp.raise_for_status()
    report = resp.json()
    records = report.get("attendanceRecords", [])
    log_graph_call(
        "read attendance records for a matching meeting session",
        "GET",
        url,
        status=resp.status_code,
        result={
            "meetingId": meeting_id,
            "reportId": report_id,
            "participantCount": len(records),
        },
    )
    return report


# --------------------------------------------------------------------------- #
# Calendar -> meeting resolution
# --------------------------------------------------------------------------- #
def _to_utc_iso(dt: datetime.datetime) -> str:
    """datetime.now() is naive local time; tag it local, convert to UTC, emit ISO Z."""
    if dt.tzinfo is None:
        dt = dt.astimezone()  # assume local tz
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_graph_utc(raw: str) -> datetime.datetime:
    """Parse a Graph dateTimeTimeZone value into an aware UTC datetime.

    calendarView returns start.dateTime with no offset (the zone is a separate
    field, UTC by default) and 7-digit fractional seconds, which fromisoformat
    rejects on older Pythons — so normalize both before parsing.
    """
    raw = raw.strip().rstrip("Z")
    if "." in raw:
        head, frac = raw.split(".", 1)
        raw = f"{head}.{frac[:6]}"  # truncate sub-microsecond digits
    dt = datetime.datetime.fromisoformat(raw)
    return dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt


def _is_forbidden(exc: requests.HTTPError) -> bool:
    r = getattr(exc, "response", None)
    return r is not None and r.status_code == 403


def _event_invitees(ev: dict) -> list[dict]:
    """Invitees from a calendar event (organizer + attendees) — the fallback when
    the attendance report isn't available. role carries the RSVP/role; no duration."""
    out, seen = [], set()
    org = (ev.get("organizer") or {}).get("emailAddress") or {}
    if org.get("address"):
        out.append({"name": org.get("name") or "(unknown)", "email": org["address"],
                    "role": "organizer", "seconds": 0})
        seen.add(org["address"].lower())
    for a in ev.get("attendees") or []:
        if a.get("type") == "resource": continue
        em = a.get("emailAddress") or {}
        addr = em.get("address") or ""
        if addr.lower() in seen:
            continue
        seen.add(addr.lower())
        out.append({"name": em.get("name") or "(unknown)", "email": addr,
                    "role": (a.get("status") or {}).get("response") or "", "seconds": 0})
    return out


def _event_summary(ev: dict) -> dict:
    return {
        "subject": ev.get("subject") or "(no subject)",
        "start": (ev.get("start") or {}).get("dateTime"),
        "hasJoinUrl": bool((ev.get("onlineMeeting") or {}).get("joinUrl")),
        "inviteeCount": len(ev.get("attendees") or []),
    }


def _event_candidate(ev: dict) -> CalendarCandidate:
    org = (ev.get("organizer") or {}).get("emailAddress") or {}
    start = (ev.get("start") or {}).get("dateTime")
    end = (ev.get("end") or {}).get("dateTime")
    return CalendarCandidate(
        join_url=(ev.get("onlineMeeting") or {}).get("joinUrl") or "",
        subject=ev.get("subject"),
        start=_parse_graph_utc(start).isoformat() if start else None,
        end=_parse_graph_utc(end).isoformat() if end else None,
        organizer=org.get("name") or org.get("address"),
        invitees=_event_invitees(ev),
        series_id=str(ev.get("seriesMasterId") or ""),
        ical_uid=str(ev.get("iCalUId") or ""),
        response=str((ev.get("responseStatus") or {}).get("response") or ""),
    )


def _candidate_distance(candidate: CalendarCandidate,
                        started_at: datetime.datetime) -> datetime.timedelta:
    raw = candidate.start
    if not raw:
        return datetime.timedelta.max
    target = (started_at.astimezone(datetime.timezone.utc)
              if started_at.tzinfo is None else started_at)
    return abs(_parse_graph_utc(raw) - target)


def list_calendar_candidates(headers: dict, started_at: datetime.datetime,
                             ended_at: datetime.datetime, *, diagnostics: bool = True,
                             online_only: bool = True) -> list[CalendarCandidate]:
    """Calendar instances in the window; attendance lookup still needs online URLs."""
    margin = datetime.timedelta(minutes=5)
    params = {
        "startDateTime": _to_utc_iso(started_at - margin),
        "endDateTime": _to_utc_iso(ended_at + margin),
        "$select": (
            "subject,isOnlineMeeting,onlineMeeting,start,end,attendees,"
            "organizer,seriesMasterId,iCalUId,isCancelled,isAllDay,responseStatus"
        ),
        "$top": "100",
    }
    url = f"{GRAPH}/me/calendarView"
    events, seen = [], set()
    while url:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com"
                or not parsed.path.startswith("/v1.0/") or parsed.fragment
                or url in seen or len(seen) >= 100):
            raise ValueError("Invalid calendar pagination")
        seen.add(url)
        resp = requests.get(url, headers={**headers, "Prefer": 'outlook.timezone="UTC"'}, params=params, timeout=_TIMEOUT, allow_redirects=False)
        if resp.status_code >= 400 and diagnostics:
            log_graph_call("find calendar meetings", "GET", url, status=resp.status_code, error=resp.text)
        resp.raise_for_status()
        if resp.status_code != 200: raise ValueError("Calendar request redirected")
        data = resp.json()
        events.extend(e for e in data.get("value", [])
                      if not e.get("isCancelled") and not e.get("isAllDay")
                      and (e.get("responseStatus") or {}).get("response") != "declined"
                      and (not online_only or (e.get("onlineMeeting") or {}).get("joinUrl")))
        url, params = data.get("@odata.nextLink"), None
    if diagnostics: log_graph_call(
        "find scheduled online meetings overlapping the recorded window",
        "GET",
        f"{GRAPH}/me/calendarView",
        status=resp.status_code,
        result={
            "matchingOnlineEvents": len(events),
            "events": [_event_summary(event) for event in events],
        },
    )
    candidates = [_event_candidate(event) for event in events]
    return sorted(candidates, key=lambda item: _candidate_distance(item, started_at))


def choose_calendar_candidate(candidates: list[CalendarCandidate],
                              started_at: datetime.datetime) -> CalendarCandidate | None:
    return min(candidates, key=lambda item: _candidate_distance(item, started_at)) if candidates else None


def _candidate_parts(candidate) -> tuple[str | None, str | None, list[dict]]:
    if isinstance(candidate, CalendarCandidate):
        return candidate.join_url, candidate.subject, candidate.invitees
    if isinstance(candidate, dict):
        return (
            candidate.get("joinUrl") or candidate.get("join_url"),
            candidate.get("subject"),
            candidate.get("invitees") or [],
        )
    return None, None, []


def _find_join_url(headers: dict, started_at: datetime.datetime,
                   ended_at: datetime.datetime):
    """Find the online-meeting calendar event overlapping the meeting window.

    Returns (joinUrl, subject, invitees, error). Widens the window by 5 min each
    side so a meeting joined slightly early/late still matches.
    """
    candidates = list_calendar_candidates(headers, started_at, ended_at)
    if not candidates:
        return None, None, [], "no online-meeting calendar event matched this time window (ad-hoc call?)"
    best = choose_calendar_candidate(candidates, started_at)
    log_graph_call(
        "choose the calendar event closest to the recording start time",
        "LOCAL",
        f"{GRAPH}/me/calendarView",
        result={"chosen": {"subject": best.subject, "start": best.start}},
    )
    return best.join_url, best.subject, best.invitees, None


# A meeting can have several attendance reports — one per *session* (each time the
# call empties and someone rejoins). Highest role wins when merging a person who
# appears across sessions.
_ROLE_PRIORITY = {"Organizer": 3, "Coorganizer": 2, "Presenter": 1, "Attendee": 0}
_WINDOW_MARGIN = datetime.timedelta(minutes=2)


def _session_overlaps(report: dict, win_start: datetime.datetime,
                      win_end: datetime.datetime) -> bool:
    """True if the report's session window overlaps [win_start, win_end] (UTC)."""
    s, e = report.get("meetingStartDateTime"), report.get("meetingEndDateTime")
    if not s or not e:
        return False
    return (_parse_graph_utc(s) <= win_end + _WINDOW_MARGIN
            and _parse_graph_utc(e) >= win_start - _WINDOW_MARGIN)


def _aggregate_attendees(record_lists: list[list[dict]]) -> list[dict]:
    """Union attendanceRecords across sessions, deduped by identity: sum time in
    call, keep the highest-priority role seen."""
    by_key: dict[str, dict] = {}
    for records in record_lists:
        for rec in records:
            ident = rec.get("identity") or {}
            email = rec.get("emailAddress") or ""
            key = ident.get("id") or email or ident.get("displayName") or "(unknown)"
            role = rec.get("role") or ""
            secs = rec.get("totalAttendanceInSeconds") or 0
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = {
                    "name": ident.get("displayName") or "(unknown)",
                    "email": email,
                    "role": role,
                    "seconds": secs,
                }
            else:
                existing["seconds"] += secs
                if _ROLE_PRIORITY.get(role, -1) > _ROLE_PRIORITY.get(existing["role"], -1):
                    existing["role"] = role
    return sorted(by_key.values(), key=lambda a: a["seconds"], reverse=True)


def _attempt(headers: dict, started_at: datetime.datetime,
             ended_at: datetime.datetime, candidate=None,
             skip_calendar: bool = False) -> AttendeeLookup:
    """One lookup attempt. attendees=None signals a retryable wait; a list (even
    empty) is terminal. meeting_id is filled in as soon as it's known."""
    if skip_calendar:
        return AttendeeLookup([], "calendar meeting mapping rejected by user")
    if candidate is not None:
        join_url, subject, invitees = _candidate_parts(candidate)
        err = None if join_url else "selected calendar meeting had no Teams join link"
    else:
        join_url, subject, invitees, err = _find_join_url(headers, started_at, ended_at)
    if err:
        return AttendeeLookup([], err)

    def _invitee_fallback(reason: str, meeting_id: str | None = None) -> AttendeeLookup:
        # The attendance report is organizer-only; for meetings we were invited to,
        # fall back to the calendar invitee list (who was invited, not who attended).
        if invitees:
            return AttendeeLookup(invitees, reason, meeting_id, subject, source="calendar invite")
        return AttendeeLookup([], reason, meeting_id, subject)

    try:
        meeting_id = resolve_meeting_id(headers, join_url)
    except requests.HTTPError as exc:
        if _is_forbidden(exc):
            return _invitee_fallback("attendance report is organizer-only; showing calendar invitees")
        raise
    if not meeting_id:
        return _invitee_fallback(f"could not resolve online meeting for '{subject}'")

    try:
        reports = list_attendance_reports(headers, meeting_id)
    except requests.HTTPError as exc:
        if _is_forbidden(exc):
            return _invitee_fallback("attendance report is organizer-only (you were not the "
                                     "organizer); showing calendar invitees", meeting_id)
        raise
    if not reports:
        return AttendeeLookup(None, "attendance report not generated yet (meeting session "
                              "may still be running)", meeting_id, subject)

    # Only the sessions that overlap the meeting we actually recorded — not earlier
    # or later sessions of the same meeting id.
    win_start = started_at.astimezone(datetime.timezone.utc)
    win_end = ended_at.astimezone(datetime.timezone.utc)
    matching = [r for r in reports if _session_overlaps(r, win_start, win_end)]
    if not matching:
        return AttendeeLookup(None, f"{len(reports)} report(s) exist but none overlap the "
                              "recorded window yet", meeting_id, subject)

    record_lists = [
        get_attendance_report(headers, meeting_id, r["id"]).get("attendanceRecords", [])
        for r in matching
    ]
    attendees = _aggregate_attendees(record_lists)
    if not attendees:
        return AttendeeLookup(None, "attendance report present but records not populated yet",
                              meeting_id, subject)
    return AttendeeLookup(attendees, None, meeting_id, subject, source="Teams attendance report")


def collect_attendees(started_at: datetime.datetime,
                      ended_at: datetime.datetime, candidate=None,
                      skip_calendar: bool = False) -> AttendeeLookup:
    """Resolve the meeting and fetch its attendance report, retrying while the
    report is still being generated. Always returns an AttendeeLookup; meeting_id
    is populated whenever the meeting was resolved, even if the report wasn't ready."""
    headers = graph_headers()
    last = AttendeeLookup(None, "attendance report not available")
    for attempt in range(1, config.ATTENDEE_RETRIES + 1):
        try:
            res = _attempt(headers, started_at, ended_at, candidate, skip_calendar)
        except requests.HTTPError as exc:
            return AttendeeLookup(None, f"Graph request failed: {exc}",
                                  last.meeting_id, last.subject)
        if res.attendees is not None:
            return res
        last = res
        if attempt < config.ATTENDEE_RETRIES:
            time.sleep(config.ATTENDEE_RETRY_DELAY_SECONDS)
    return AttendeeLookup(None, f"{last.note} (gave up after {config.ATTENDEE_RETRIES} attempts)",
                          last.meeting_id, last.subject)
