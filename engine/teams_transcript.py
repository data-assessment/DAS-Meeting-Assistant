"""Fetch the Teams meeting transcript via Graph.

Available only for meetings the signed-in user *organized* with transcription
turned on. Returns speaker-attributed segments parsed from the transcript's VTT
content, or (None, reason) so the caller can fall back to batch speech-to-text.

    GET /me/onlineMeetings/{id}/transcripts                 -> list
    GET /me/onlineMeetings/{id}/transcripts/{tid}/content   -> text/vtt
"""
import datetime
import re

import requests

from engine.attendance import GRAPH, graph_headers
from engine.graph_log import log_graph_call

_TIMEOUT = 30
_TRANSCRIPT_WINDOW_MARGIN = datetime.timedelta(minutes=15)
_TRANSCRIPT_CREATED_AFTER_END_MARGIN = datetime.timedelta(hours=4)


def _parse_graph_utc(raw: str | None) -> datetime.datetime | None:
    if not raw:
        return None
    raw = raw.strip().rstrip("Z")
    if "." in raw:
        head, frac = raw.split(".", 1)
        raw = f"{head}.{frac[:6]}"
    try:
        parsed = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _to_utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(datetime.timezone.utc)


def _matching_transcripts(
    items: list[dict],
    started_at: datetime.datetime | None,
    ended_at: datetime.datetime | None,
) -> list[dict]:
    if started_at is None or ended_at is None:
        return items
    window_start = _to_utc(started_at) - _TRANSCRIPT_WINDOW_MARGIN
    window_end = _to_utc(ended_at) + _TRANSCRIPT_CREATED_AFTER_END_MARGIN
    return [
        item for item in items
        if (created := _parse_graph_utc(item.get("createdDateTime")))
        and window_start <= created <= window_end
    ]


def _decode_vtt_content(content: requests.Response) -> str:
    try:
        return content.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.text


def _vtt_seconds(ts: str) -> float:
    try:
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return 0.0


def _cue_parts(block: str) -> tuple[str | None, str | None, list[str]]:
    start = None
    end = None
    content: list[str] = []
    for line in [ln for ln in block.strip().splitlines() if ln.strip()]:
        if line.startswith("WEBVTT"):
            continue
        if "-->" in line:
            start, raw_end = [part.strip() for part in line.split("-->", 1)]
            end = raw_end.split()[0]
            continue
        if re.fullmatch(r"\d+", line):
            continue
        content.append(line)
    return start, end, content


def _speaker_and_text(raw: str) -> tuple[str | None, str]:
    match = re.search(r"<v\s+([^>]+)>(.*?)</v>", raw, re.S)
    if match:
        speaker, body = match.group(1).strip(), match.group(2)
    else:
        speaker, body = None, raw
    return speaker, re.sub(r"<[^>]+>", "", body).strip()


def _parse_vtt(text: str) -> list[dict]:
    """VTT cues -> [{speaker, text, start}]."""
    segments: list[dict] = []
    for block in re.split(r"\n\s*\n", text):
        start_ts, end_ts, content = _cue_parts(block)
        if not content:
            continue
        speaker, body = _speaker_and_text(" ".join(content))
        if body:
            start = _vtt_seconds(start_ts) if start_ts else 0.0
            end = _vtt_seconds(end_ts) if end_ts else start
            segments.append({
                "speaker": speaker,
                "text": body,
                "start": start,
                "end": end,
            })
    return segments


def fetch_transcript_segments(
    meeting_id: str,
    started_at: datetime.datetime | None = None,
    ended_at: datetime.datetime | None = None,
) -> tuple[list[dict] | None, str | None]:
    """Return (segments, note). segments=None means fall back to batch STT."""
    headers = graph_headers()
    transcripts_url = f"{GRAPH}/me/onlineMeetings/{meeting_id}/transcripts"
    try:
        resp = requests.get(transcripts_url, headers=headers, timeout=_TIMEOUT)
        if resp.status_code >= 400:
            log_graph_call(
                "look for an existing Teams transcript for the "
                "resolved meeting",
                "GET",
                transcripts_url,
                status=resp.status_code,
                error=resp.text,
            )
        resp.raise_for_status()
    except requests.HTTPError as exc:
        if getattr(exc.response, "status_code", None) == 403:
            return None, (
                "Teams transcript is organizer-only "
                "(you didn't organize this meeting)"
            )
        return None, f"Teams transcript lookup failed: {exc}"

    items = resp.json().get("value", [])
    candidates = _matching_transcripts(items, started_at, ended_at)
    log_graph_call(
        "look for an existing Teams transcript for the resolved meeting",
        "GET",
        transcripts_url,
        status=resp.status_code,
        result={
            "meetingId": meeting_id,
            "transcriptCount": len(items),
            "matchingTranscriptCount": len(candidates),
            "created": [item.get("createdDateTime") for item in items],
        },
    )
    if not items:
        return None, "no Teams transcript (transcription wasn't turned on?)"
    if not candidates:
        return None, (
            "Teams transcripts exist for this meeting link, but none match "
            "the "
            "recorded time window"
        )
    latest = max(candidates, key=lambda t: t.get("createdDateTime") or "")

    content_url = (
        f"{GRAPH}/me/onlineMeetings/{meeting_id}/transcripts/"
        f"{latest['id']}/content"
    )
    try:
        content = requests.get(
            content_url,
            headers=headers, params={"$format": "text/vtt"}, timeout=_TIMEOUT,
        )
        if content.status_code >= 400:
            log_graph_call(
                "download the matching Teams transcript content",
                "GET",
                content_url,
                status=content.status_code,
                error=content.text,
            )
        content.raise_for_status()
    except requests.HTTPError as exc:
        return None, f"Teams transcript content failed: {exc}"

    vtt_text = _decode_vtt_content(content)
    segments = _parse_vtt(vtt_text)
    log_graph_call(
        "download the matching Teams transcript content",
        "GET",
        content_url,
        status=content.status_code,
        result={
            "meetingId": meeting_id,
            "transcriptId": latest.get("id"),
            "createdDateTime": latest.get("createdDateTime"),
            "segmentCount": len(segments),
            "speakers": sorted({
                s.get("speaker") for s in segments if s.get("speaker")
            }),
        },
    )
    if not segments:
        return None, "Teams transcript was empty/unparseable"
    return segments, None
