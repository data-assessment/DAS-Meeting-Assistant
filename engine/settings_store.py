"""Persist user-chosen settings as JSON in the per-user data dir.

Stored next to token_cache.bin in paths.data_dir(), so the chosen settings survive
both app restarts AND reinstalls — the installer doesn't wipe the per-user data dir.
Best-effort: a missing/corrupt file just yields {} and a write failure is logged, so
persistence never breaks the app.
"""
import json

import paths

_PATH = paths.data_dir() / "settings.json"


def load() -> dict:
    """Return the saved settings, or {} if none/unreadable."""
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # corrupt file etc. — don't let it break startup
        print("settings load failed:", exc)
        return {}


def save(data: dict, *, strict: bool = False) -> None:
    """Persist settings; explicit setup completion must surface write failures."""
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:
        if strict:
            raise
        print("settings save failed:", exc)
