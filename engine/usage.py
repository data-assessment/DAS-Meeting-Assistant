"""Metadata-only usage reporting for managed gateway installations.

Events are queued on disk before upload and removed only after the central
service accepts them. Event IDs make retries idempotent. No audio, transcript,
prompt, response text, email address, or display name is recorded.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
from pathlib import Path

import requests

import config
import paths

_QUEUE = paths.data_dir() / "usage_queue.jsonl"
_LOCK = threading.Lock()
_FLUSH_LOCK = threading.Lock()
_TIMEOUT = 10


def enabled() -> bool:
    return config.AI_MODE == "gateway" and bool(config.USAGE_SERVICE_ENDPOINT)


def _read_queue() -> list[dict]:
    if not _QUEUE.exists():
        return []
    events: list[dict] = []
    try:
        for line in _QUEUE.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict) and value.get("eventId"):
                    events.append(value)
            except Exception:
                continue
    except Exception as exc:
        print("usage queue read failed:", exc)
    return events


def _write_queue(events: list[dict]) -> None:
    _QUEUE.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in events)
    temp = Path(str(_QUEUE) + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(_QUEUE)


def record(
    operation: str,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    audio_seconds: float = 0,
    session_seconds: float = 0,
    request_id: str = "",
) -> None:
    if not enabled():
        return
    event = {
        "eventId": str(uuid.uuid4()),
        "occurredAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "operation": operation,
        "model": model,
        "inputTokens": max(0, int(input_tokens or 0)),
        "outputTokens": max(0, int(output_tokens or 0)),
        "totalTokens": max(0, int(total_tokens or 0)),
        "audioSeconds": max(0.0, round(float(audio_seconds or 0), 3)),
        "sessionSeconds": max(0.0, round(float(session_seconds or 0), 3)),
        "requestId": str(request_id or "")[:128],
    }
    with _LOCK:
        events = _read_queue()
        events.append(event)
        _write_queue(events)
    threading.Thread(target=flush, daemon=True).start()


def _headers(interactive: bool = False) -> dict[str, str]:
    from engine.ai_auth import gateway_token

    return {
        "Authorization": f"Bearer {gateway_token(interactive=interactive)}",
        "Content-Type": "application/json",
    }


def flush() -> int:
    """Upload queued events. Returns the number accepted in this attempt."""
    if not enabled() or not _FLUSH_LOCK.acquire(blocking=False):
        return 0
    accepted = 0
    try:
        with _LOCK:
            pending = _read_queue()
        remaining: list[dict] = []
        for index, event in enumerate(pending):
            try:
                response = requests.post(
                    f"{config.USAGE_SERVICE_ENDPOINT}/usage/events",
                    headers=_headers(interactive=False),
                    json=event,
                    timeout=_TIMEOUT,
                )
                if response.status_code not in (200, 201):
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
                accepted += 1
            except Exception as exc:
                print("usage upload deferred:", exc)
                remaining.extend(pending[index:])
                break
        with _LOCK:
            # Preserve events queued by another thread while this upload ran.
            current = _read_queue()
            pending_ids = {item.get("eventId") for item in pending}
            added = [item for item in current if item.get("eventId") not in pending_ids]
            _write_queue(remaining + added)
        return accepted
    finally:
        _FLUSH_LOCK.release()


def summary(period: str = "") -> dict:
    if not enabled():
        return {"enabled": False, "reason": "Usage reporting is available in managed gateway mode."}
    flush()
    response = requests.get(
        f"{config.USAGE_SERVICE_ENDPOINT}/usage/summary",
        headers=_headers(interactive=False),
        params={"period": period} if period else None,
        timeout=_TIMEOUT,
    )
    if response.status_code >= 300:
        raise RuntimeError(f"usage service HTTP {response.status_code}: {response.text[:300]}")
    data = response.json()
    data["enabled"] = True
    with _LOCK:
        data["pendingEvents"] = len(_read_queue())
    return data
