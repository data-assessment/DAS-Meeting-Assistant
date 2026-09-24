"""Remember which target the user picked, per meeting, across restarts.

Recording a choice is also how an override is learned: `engine.suggest` ranks a
remembered value above every inference, so correcting a wrong suggestion once
is enough to make it stick. There is no separate list of rejections.

Kept out of the settings file on purpose. This grows with use and is keyed by
opaque hashes, whereas settings stay small and hand-editable.
"""
import json
import os
import tempfile
from pathlib import Path

# Keys in the order they are trusted: an exact recurring-series match beats a
# group of the same people, which beats a meeting that merely shares a name.
KEY_ORDER = ("series", "group", "title")

_DEFAULT_LIMIT = 200


class ChoiceMemory:
    """A bounded value store: scope + meeting key -> the chosen value.

    Entries are least-recently-used; the oldest fall off once the limit is hit,
    so a long-running install never grows this file without end.
    """

    def __init__(self, path: str | Path, limit: int = _DEFAULT_LIMIT) -> None:
        self.path = Path(path)
        self.limit = max(1, limit)
        self._entries: dict[str, str] = {}
        self._loaded = False

    # -- persistence ------------------------------------------------------- #
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as exc:
            # A corrupt file must never stop the dialogs from opening; the
            # worst case is forgetting past choices.
            print("choice memory unreadable, starting fresh:", exc)
            return
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if isinstance(entries, dict):
            self._entries = {
                str(k): str(v) for k, v in entries.items() if k and v
            }

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp",
                dir=self.path.parent,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"entries": self._entries}, stream, indent=1)
            # Same-directory replace is atomic, so a crash mid-write cannot
            # leave half a file behind.
            os.replace(temporary, self.path)
        except Exception as exc:
            print("could not save choice memory:", exc)

    # -- api --------------------------------------------------------------- #
    @staticmethod
    def _slot(scope: str, key_name: str, key_value: str) -> str:
        return f"{scope}\n{key_name}\n{key_value}"

    def recall(
        self, scope: str, keys: dict[str, str],
    ) -> tuple[str, str] | None:
        """Best remembered value, as (value, which key matched)."""
        self._load()
        for key_name in KEY_ORDER:
            key_value = keys.get(key_name)
            if not key_value:
                continue
            value = self._entries.get(self._slot(scope, key_name, key_value))
            if value:
                return value, key_name
        return None

    def remember(self, scope: str, keys: dict[str, str], value: str) -> None:
        """Record a choice under every key this meeting offers.

        All of them, not just the strongest: the next occurrence may arrive
        without a series id (an ad-hoc re-invite) and should still be
        recognised by its participants or its name.
        """
        if not scope or not value:
            return
        self._load()
        touched = False
        for key_name in KEY_ORDER:
            key_value = keys.get(key_name)
            if not key_value:
                continue
            slot = self._slot(scope, key_name, key_value)
            # Re-insert so recent entries survive trimming.
            self._entries.pop(slot, None)
            self._entries[slot] = str(value)
            touched = True
        if not touched:
            return
        while len(self._entries) > self.limit:
            self._entries.pop(next(iter(self._entries)))
        self._save()

    def forget_scope(self, scope: str) -> None:
        """Drop a scope, for when its target system is repointed elsewhere."""
        self._load()
        prefix = f"{scope}\n"
        removed = [k for k in self._entries if k.startswith(prefix)]
        for key in removed:
            del self._entries[key]
        if removed:
            self._save()

    def __len__(self) -> int:
        self._load()
        return len(self._entries)
