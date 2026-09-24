"""Poll the signed-in user's presence via Microsoft Graph.

GET /me/presence -> activity in {InACall, InAConferenceCall, Presenting} means
a call/meeting is active. InAMeeting is deliberately ignored because Graph may
set it from a current calendar event even when the user is not actually in
Teams. Synchronous by design; the caller runs it in a worker thread.
"""
import datetime
from dataclasses import dataclass

import requests

import config
from engine.graph_auth import get_token
from engine.graph_log import log_graph_call

GRAPH = "https://graph.microsoft.com/v1.0"
GRAPH_ME_PRESENCE = f"{GRAPH}/me/presence"


@dataclass(frozen=True)
class PresenceSnapshot:
    observed_at: str
    availability: str
    activity: str
    active: bool
    raw: dict


def _now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def _iso(dt: datetime.datetime | None) -> str:
    if dt is None:
        return ""
    return dt.isoformat(timespec="seconds")


def get_presence_snapshot() -> PresenceSnapshot:
    # Never pop a browser from the poll loop.
    token = get_token(interactive=False)
    if not token:
        raise RuntimeError("sign-in required")
    observed = _now()
    resp = requests.get(
        GRAPH_ME_PRESENCE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp.status_code >= 400:
        log_graph_call(
            "detect whether Teams presence says a call or meeting is active",
            "GET",
            GRAPH_ME_PRESENCE,
            status=resp.status_code,
            error=resp.text,
        )
        resp.raise_for_status()
    raw = resp.json()
    activity = raw.get("activity", "")
    snapshot = PresenceSnapshot(
        observed_at=_iso(observed),
        availability=raw.get("availability", ""),
        activity=activity,
        active=activity in config.ACTIVE_ACTIVITIES,
        raw=raw,
    )
    return snapshot


def is_meeting_active() -> bool:
    return get_presence_snapshot().active
