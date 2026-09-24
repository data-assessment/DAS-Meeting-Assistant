"""Small structured logs for Microsoft Graph calls.

The logs are intentionally end-user oriented: why the call was made, whether it
succeeded, and the useful result summary. Authorization headers, tokens, and full
Teams join URLs are never logged here.
"""
import datetime
import json
from urllib.parse import urlparse


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _endpoint(url: str) -> str:
    parsed = urlparse(url)
    return parsed.path.replace("/v1.0", "") or parsed.path


def log_graph_call(
    purpose: str,
    method: str,
    url: str,
    *,
    status: int | None = None,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    entry = {
        "observed_at": _now(),
        "purpose": purpose,
        "method": method,
        "endpoint": _endpoint(url),
    }
    if status is not None:
        entry["status"] = status
    if result is not None:
        entry["result"] = result
    if error:
        entry["error"] = error[:300]
    print(
        "graph api call:",
        json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
