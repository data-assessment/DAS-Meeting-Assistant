"""Calendar context and named candidates, independent of attendance-report grants."""
import asyncio
import datetime as dt
import uuid
import re
import unicodedata
from dataclasses import dataclass, field
import requests
from engine.notes_schema import Person
from engine.graph_auth import signed_in_username, get_token_for_scopes
from engine.notes_i18n import t

SCOPES = ["Calendars.Read"]

@dataclass
class PeopleResult:
    people: list
    note: str
    title: str = ""
    candidates: list = field(default_factory=list)
    selected: str = ""
    access_needed: bool = False
    def __iter__(self):
        yield self.people
        yield self.note

def person(name, email, source):
    email = str(email or "").strip().lower()
    name = str(name or email).strip()
    if not name: return None
    return Person(id=str(uuid.uuid5(uuid.NAMESPACE_URL, "person:" + (email or name.casefold()))),
                  name=name[:200], email=email[:300], source=source).model_dump()


def calendar_context(review):
    """Invitation metadata is rendered deterministically, never invented by AI."""
    if not review.calendar_selected: return None
    selected = next((c for c in review.calendar_candidates if c["id"] == review.calendar_selected), None)
    if selected: return selected
    # Earlier saved versions kept people and title but not the invitation itself.
    return {"title": review.title, "start": "", "end": "", "organizer": "",
            "people": [p for p in review.people if p["source"] in ("calendar", "self")]}


def meeting_title(review):
    context = calendar_context(review)
    return (context or {}).get("title") or review.title


def meeting_start(review):
    context = calendar_context(review)
    return dt.datetime.fromisoformat((context or {}).get("start") or review.started).astimezone()


def meeting_heading(review):
    return meeting_start(review).strftime(t("document.formats.date") + " · %H:%M · ") + meeting_title(review)


def reconcile_owners(review):
    """Link explicitly named owners to one unique meeting person, never a voice ID."""
    if not review.draft or review.onenote.get("status") in ("preparing", "sending", "uncertain", "saved"): return False
    def words(value):
        return tuple(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))
    people = [p for p in review.people if p["source"] in ("calendar", "call", "self")]
    changed = False
    for task in review.draft["tasks"]:
        if task.get("ownerId") or not task["owner"].strip(): continue
        if {"owner", "ownerId"} & review.task_edits.get(task["id"], {}).keys(): continue
        owner = words(task["owner"])
        if owner in (("ich",), ("wir",), ("i",), ("we",)): continue
        exact, first = [], []
        for p in people:
            name = p["name"]
            # Outlook also uses "Surname, Given name" display names.
            reordered = " ".join(reversed(name.split(",", 1))) if "," in name else name
            tokens = words(reordered)
            if owner in (words(name), tokens) or task["owner"].strip().casefold() == p.get("email", "").casefold():
                exact.append(p)
            elif len(owner) == 1 and tokens and owner[0] == tokens[0]:
                first.append(p)
        matches = exact or first
        # Same person can arrive via several Graph sources; distinct identities
        # with the same name remain ambiguous even if their spelling is identical.
        unique = {p.get("email", "").casefold() or p["id"]: p for p in matches}
        if len(unique) == 1:
            p = next(iter(unique.values()))
            task.update(owner=p["name"], ownerId=p["id"])
            changed = True
    return changed


def restore_candidates(values):
    result = []
    for c in values[:20]:
        value = {key: str(c.get(key) or "")[:500] for key in ("id", "title", "start", "end", "organizer", "response")}
        value["people"] = [Person.model_validate(p).model_copy(update={"source": "calendar"}).model_dump() for p in c.get("people", [])[:500]]
        if value["id"]: result.append(value)
    return result


def invitation_lines(review):
    context = calendar_context(review)
    if not context: return []
    lines = []
    if context.get("start"):
        try:
            start = dt.datetime.fromisoformat(context["start"]).astimezone()
            end = dt.datetime.fromisoformat(context["end"]).astimezone() if context.get("end") else None
            lines.append(t("document.invitation.meeting") + ": " + start.strftime(t("document.formats.date") + ", %H:%M") + (" – " + end.strftime("%H:%M") if end else ""))
        except ValueError: pass
    if context.get("organizer"): lines.append(t("document.invitation.organizer") + ": " + context["organizer"])
    lines.append(t("document.invitation.attendees"))
    lines.extend(p["name"] + (" <" + p["email"] + ">" if p.get("email") else "") for p in context["people"])
    return lines


def resolve_people(started):
    from engine.attendance import list_calendar_candidates
    people = {}
    def add(name, email, source):
        p = person(name, email, source)
        if p: people[p["id"]] = p
    try:
        token = get_token_for_scopes(SCOPES, interactive=False)
        headers = {"Authorization": "Bearer " + token} if token else {}
    except Exception:
        headers = {}
    if headers:
        try:
            response = requests.get("https://graph.microsoft.com/v1.0/me", headers=headers,
                params={"$select": "displayName,mail,userPrincipalName"}, timeout=10, allow_redirects=False)
            response.raise_for_status(); me = response.json()
            add(me.get("displayName"), me.get("mail") or me.get("userPrincipalName"), "self")
        except Exception: pass
    if not people:
        username = signed_in_username()
        if username: add(username, username, "self")
    if not headers:
        return PeopleResult(list(people.values()), t("calendar.notes.accessMissing"), access_needed=True)
    try:
        # No attendance or transcript permission is needed for invitation details.
        candidates = list_calendar_candidates(headers, started, started + dt.timedelta(minutes=1), diagnostics=False, online_only=False)
        def moment(value):
            value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value
        anchor = started.astimezone(dt.timezone.utc)
        current = [c for c in candidates if c.start and c.end and moment(c.start) <= anchor <= moment(c.end)]
        nearby = [c for c in candidates if c.start and c.end and moment(c.start) - dt.timedelta(minutes=5) <= anchor <= moment(c.end) + dt.timedelta(minutes=5)]
        choices = current if current else nearby
        if len(choices) > 20:
            return PeopleResult(list(people.values()), t("calendar.notes.tooManyEvents"))
        values = []
        for c in choices:
            identity = str(uuid.uuid5(uuid.NAMESPACE_URL, str(getattr(c, "join_url", "")) + str(c.start) + str(getattr(c, "subject", ""))))
            values.append({"id":identity,"title":str(getattr(c,"subject","") or t("calendar.fallbackTitle")),"start":c.start,
                           "end":c.end,"organizer":getattr(c,"organizer","") or "","response":getattr(c,"response","") or "",
                           "people":[p for v in c.invitees if (p := person(v.get("name"),v.get("email"),"calendar"))]})
        if len(values) == 1:
            selected = values[0]
            for p in selected["people"]:
                if p["id"] not in people: people[p["id"]] = p
            return PeopleResult(list(people.values()), "", selected["title"], values, selected["id"])
        note = (t("calendar.notes.severalMatches") if values
                else t("calendar.notes.noMatch"))
        return PeopleResult(list(people.values())[:100], note, candidates=values)
    except requests.HTTPError as exc:
        access = exc.response is not None and exc.response.status_code in (401,403)
        return PeopleResult(list(people.values()), t("calendar.notes.accessMissing") if access else t("calendar.notes.unavailable"), access_needed=access)
    except Exception:
        return PeopleResult(list(people.values()), t("calendar.notes.unavailable"))

async def load_people(notes, review):
    if review.calendar_selected or review.onenote.get("status") in ("preparing", "sending", "uncertain", "saved"): return
    try:
        result = await asyncio.to_thread(resolve_people, dt.datetime.fromisoformat(review.started))
        people, note = result
    except Exception:
        result = None; people, note = [], t("calendar.notes.unavailable")
    if notes.closed or review.discarded or review.calendar_selected or review.onenote.get("status") in ("preparing", "sending", "uncertain", "saved"): return
    from engine.notes_calls import merge_people
    review.calendar_access_needed = getattr(result, "access_needed", False)
    review.calendar_candidates = getattr(result, "candidates", [])
    selected = getattr(result, "selected", "")
    review.people = merge_people(review.people, people)
    if selected and not review.calendar_selected:
        review.calendar_selected = selected
        review.revision += 1
        notes.set_meeting_title(review, result.title)
    review.calendar_note = note
    if selected: review.people_note = ""
    elif not any(p["source"] in ("call", "contact") for p in review.people): review.people_note = note
    notes.persist(review)

def select_calendar(notes, review, identity):
    if review.onenote.get("status") in ("preparing", "sending", "uncertain", "saved"):
        raise ValueError(t("calendar.errors.editedInOneNote"))
    selected = next((c for c in review.calendar_candidates if c["id"] == identity), None)
    if not selected: raise ValueError(t("calendar.errors.eventUnavailable"))
    from engine.notes_calls import merge_people
    # Do not silently replace a previously selected invitation.
    if review.calendar_selected and review.calendar_selected != identity: raise ValueError(t("calendar.errors.alreadyAssigned"))
    if review.calendar_selected == identity: return
    review.calendar_selected = identity
    review.revision += 1
    review.calendar_note = ""
    review.people = merge_people(review.people, selected["people"])
    review.people_note = ""
    notes.set_meeting_title(review, selected["title"])
    notes.persist(review)
