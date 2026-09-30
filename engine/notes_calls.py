"""Delegated Teams call-event lookup. Chat text never leaves the HTTP response.

Presence provides no call ID. Only one unambiguous, time-correlated system call
is accepted; chat activity alone and group-chat membership are not attendance.
"""
import asyncio
import datetime as dt
import re
import time
import uuid
from urllib.parse import quote, urlsplit

import requests
from engine.graph_auth import get_token_for_scopes
from engine.notes_i18n import tr
from engine.notes_schema import Person

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Chat.Read"]
TOLERANCE = dt.timedelta(minutes=3)


def instant(value):
    value = dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    return value.astimezone(dt.timezone.utc)


def duration(value):
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", value or "")
    if not match or not any(match.groups()): raise ValueError("Invalid call duration")
    hours, minutes, seconds = (float(v or 0) for v in match.groups())
    return dt.timedelta(hours=hours, minutes=minutes, seconds=seconds)


def event_match(message, started, ended=None):
    if message.get("messageType") != "systemEventMessage": return None
    event = message.get("eventDetail") or {}
    kind = event.get("@odata.type", "").rsplit(".", 1)[-1]
    if kind not in ("callStartedEventMessageDetail", "callEndedEventMessageDetail"): return None
    if not event.get("callId") or event.get("callEventType") not in ("call", "meeting"): return None
    try:
        stamp = instant(message["createdDateTime"])
        beginning = stamp - duration(event.get("callDuration")) if kind.startswith("callEnded") else stamp
        if kind.startswith("callEnded"):
            # A recording may cover only part of the call (late join/early stop).
            if beginning > started + TOLERANCE or stamp < started or (ended and stamp < ended - TOLERANCE): return None
        elif abs(beginning - started) > TOLERANCE:
            return None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return event


def graph_json(url, headers, params=None):
    # Never forward a bearer token to a redirected host or untrusted nextLink.
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com" or not parsed.path.startswith("/v1.0/"):
        raise ValueError("Invalid Graph URL")
    response = requests.get(url, headers=headers, params=params, timeout=10, allow_redirects=False)
    if response.status_code in (401, 403): raise PermissionError("Teams permission required")
    if response.status_code != 200: raise RuntimeError("Teams lookup unavailable")
    return response.json()


def call_people(started, ended=None):
    """Bounded lookup; returns only person candidates, status and access flag."""
    try:
        token = get_token_for_scopes(SCOPES, interactive=False)
        if not token: return [], "", True
        headers = {"Authorization": "Bearer " + token, "Prefer": "include-unknown-enum-members"}
        anchor, stop = instant(started), instant(ended) if ended else None
        cutoff = (anchor - TOLERANCE).isoformat().replace("+00:00", "Z")
        chats = graph_json(GRAPH + "/me/chats", headers, {
            "$expand": "members,lastMessagePreview", "$top": "25",
            "$orderby": "lastMessagePreview/createdDateTime desc"})
        # Group/meeting chat members are also selectable contacts. Membership
        # alone never establishes attendance or who said "I" in the audio.
        from engine.notes_people import person
        contacts = {}
        for chat in chats.get("value", [])[:25]:
            for member in chat.get("members") or []:
                p = person(member.get("displayName"), member.get("email"), "contact")
                if p: contacts[p["id"]] = p
        matches, finished = {}, set()
        checked = 0
        incomplete = False
        for chat in chats.get("value", [])[:25]:
            preview = chat.get("lastMessagePreview") or {}
            try:
                if instant(preview["createdDateTime"]) < anchor - TOLERANCE: continue
            except (KeyError, ValueError, TypeError):
                continue
            if checked >= 8:
                incomplete = True
                break
            checked += 1
            # Read bounded recent pages so a regular message after the call
            # does not hide its system event. Bodies are never inspected.
            messages = [preview]
            url = GRAPH + "/chats/" + quote(chat["id"], safe="") + "/messages"
            params = {"$top": "50", "$orderby": "lastModifiedDateTime desc",
                      "$filter": "lastModifiedDateTime gt " + cutoff}
            try:
                for _ in range(2):
                    page = graph_json(url, headers, params)
                    messages.extend(page.get("value", []))
                    url, params = page.get("@odata.nextLink"), None
                    if not url: break
                if url: incomplete = True
            except (PermissionError, RuntimeError, requests.RequestException):
                incomplete = True
            for message in messages:
                detail = message.get("eventDetail") or {}
                if detail.get("@odata.type", "").endswith(".callEndedEventMessageDetail"):
                    finished.add(detail.get("callId"))
                event = event_match(message, anchor, stop)
                if not event: continue
                key = event["callId"]
                # The ended event has actual participants; prefer it to start.
                old = matches.get(key)
                if old is None or event.get("callParticipants"):
                    matches[key] = (event, chat)
        matches = {key: value for key, value in matches.items() if key not in finished
                   or value[0].get("@odata.type", "").endswith(".callEndedEventMessageDetail")}
        if len(matches) != 1 or incomplete:
            return list(contacts.values())[:100], tr("Anruf nicht eindeutig erkannt. Kontakte aus Einzel-, Gruppen- und Meeting-Chats stehen zur Auswahl; ihre Teilnahme ist nicht bestätigt.",
                                                     "Call not identified unambiguously. Contacts from one-on-one, group and meeting chats are available; their attendance is not confirmed."), False
        event, chat = next(iter(matches.values()))
        members = chat.get("members") or []
        # Expanded members may be truncated. Read all pages of the matched chat
        # to resolve participant IDs to names/email, without directory-wide grants.
        url = GRAPH + "/chats/" + quote(chat["id"], safe="") + "/members"
        try:
            complete_members = []
            for _ in range(10):
                page = graph_json(url, headers)
                complete_members.extend(page.get("value", []))
                url = page.get("@odata.nextLink")
                if not url: break
            members = complete_members + members
        except (PermissionError, RuntimeError, requests.RequestException):
            pass
        # Prioritize this chat's candidates over less relevant recent contacts.
        relevant = {}
        for member in members:
            p = person(member.get("displayName"), member.get("email"), "contact")
            if p: relevant[p["id"]] = p
        contacts = relevant | {key: value for key, value in contacts.items() if key not in relevant}
        participants = event.get("callParticipants") or []
        if participants:
            identities = [(p.get("participant") or {}).get("user") or {} for p in participants]
            values = []
            for identity in identities:
                match = next((m for m in members if m.get("userId") and m["userId"] == identity.get("id")), {})
                values.append((identity.get("displayName") or match.get("displayName"), match.get("email"), identity.get("id")))
        elif chat.get("chatType") == "oneOnOne" and len(relevant) == 2:
            values = [(m.get("displayName"), m.get("email"), m.get("userId")) for m in members]
        else:
            return list(contacts.values())[:100], tr("Personen aus dem Meeting-Chat stehen zur Auswahl. Bestätigte Anrufteilnehmer können nach Gesprächsende verfügbar werden.",
                                                     "People from the meeting chat are available. Confirmed call participants may become available after the meeting ends."), False
        people = {}
        for name, email, user_id in values:
            email = str(email or "").strip().lower()
            name = str(name or email).strip()
            if not name: continue
            key = email or user_id or name.casefold()
            person = Person(id=str(uuid.uuid5(uuid.NAMESPACE_URL, "person:" + key)),
                name=name[:200], email=email[:300], source="call").model_dump()
            people[person["id"]] = person
        return list(people.values())[:100], "", False
    except PermissionError:
        return [], "", True
    except Exception:
        # No API error bodies, chat text, IDs or credentials in logs/UI.
        return [], tr("Anrufteilnehmer gerade nicht verfügbar. Personen können ergänzt werden.",
                      "Call participants currently unavailable. People can be added manually."), False


def merge_people(existing, incoming):
    result = list(existing)
    for person in incoming:
        old = next((p for p in result if p["id"] == person["id"] or
                    (p["email"] and p["email"].casefold() == person["email"].casefold())), None)
        if old:
            if old["source"] == "contact" and person["source"] in ("calendar", "call"):
                old["source"], old["name"] = person["source"], person["name"]
            if old["source"] == "self" and old["name"] == old["email"]:
                old["name"] = person["name"]
        else: result.append(person)
    return sorted(result, key=lambda p: p["source"] == "contact")[:600]


async def refresh_people(notes, review):
    if review.onenote.get("status") in ("preparing", "sending", "uncertain", "saved"): return
    values, note, access = await asyncio.to_thread(call_people, review.started, review.ended or None)
    if notes.closed or review.discarded or review.onenote.get("status") in ("preparing", "sending", "uncertain", "saved"): return
    review.people_access_needed = access
    if values:
        review.people = merge_people(review.people, values)
        review.people_note = note if not any(p["source"] in ("calendar", "call") for p in review.people) else ""
    elif note and not any(p["source"] in ("calendar", "call") for p in review.people):
        review.people_note = note
    notes.persist(review)


async def watch_people(notes, review):
    from engine.notes_people import load_people
    await load_people(notes, review)
    deadline = time.monotonic() + 2 * 60 * 60 + 180
    while not notes.closed and not review.discarded and time.monotonic() < deadline:
        await refresh_people(notes, review)
        if not review.calendar_selected and not review.calendar_access_needed:
            await load_people(notes, review)
        # Calendar retries must continue even without the independent Chat.Read grant.
        if review.ended and (dt.datetime.now().astimezone() - instant(review.ended)).total_seconds() > 180: return
        await asyncio.sleep(45)


def schedule_people(notes, review):
    if review.people_job and not review.people_job.done(): return
    job = asyncio.create_task(watch_people(notes, review))
    review.people_job = job
    notes.jobs.add(job)
    job.add_done_callback(notes.jobs.discard)
