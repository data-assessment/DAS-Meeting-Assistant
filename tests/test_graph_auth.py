import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from engine import graph_auth


class _ChangedCache:
    has_state_changed = True

    def __init__(self, serialized: str):
        self.serialized = serialized

    def serialize(self) -> str:
        return self.serialized


def test_invalid_cache_is_preserved_and_rebuilt(tmp_path):
    cache_path = tmp_path / "token_cache.bin"
    invalid = '{"first": true}\n{"second": true}'
    cache_path.write_text(invalid, encoding="utf-8")

    recovered = graph_auth._load_cache(str(cache_path))

    assert list(recovered.search("AccessToken")) == []
    assert not cache_path.exists()
    backups = list(tmp_path.glob("token_cache.bin.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == invalid


def test_valid_cache_is_left_in_place(tmp_path):
    cache_path = tmp_path / "token_cache.bin"
    cache_path.write_text("{}", encoding="utf-8")

    graph_auth._load_cache(str(cache_path))

    assert cache_path.read_text(encoding="utf-8") == "{}"
    assert list(tmp_path.glob("token_cache.bin.corrupt-*")) == []


def test_recovery_does_not_quarantine_cache_replaced_by_another_process(tmp_path):
    cache_path = tmp_path / "token_cache.bin"
    cache_path.write_text("{}", encoding="utf-8")

    graph_auth._preserve_invalid_cache(
        str(cache_path), '{"old": "invalid"}', ValueError("bad JSON")
    )

    assert cache_path.read_text(encoding="utf-8") == "{}"
    assert list(tmp_path.glob("token_cache.bin.corrupt-*")) == []


def test_atomic_cache_writes_never_leave_interleaved_json(tmp_path):
    cache_path = tmp_path / "token_cache.bin"
    payloads = [json.dumps({"writer": index, "value": "x" * 100_000}) for index in range(12)]

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(
            lambda payload: graph_auth._save_cache(_ChangedCache(payload), str(cache_path)),
            payloads,
        ))

    final = cache_path.read_text(encoding="utf-8")
    assert final in payloads
    assert json.loads(final)["writer"] in range(12)
    assert list(tmp_path.glob(".token_cache.bin.*.tmp")) == []


def test_failed_atomic_replace_keeps_previous_cache(monkeypatch, tmp_path):
    cache_path = tmp_path / "token_cache.bin"
    cache_path.write_text('{"old": true}', encoding="utf-8")

    def fail_replace(source, target):
        raise PermissionError("replacement blocked")

    monkeypatch.setattr(graph_auth.os, "replace", fail_replace)

    with pytest.raises(PermissionError, match="replacement blocked"):
        graph_auth._save_cache(_ChangedCache('{"new": true}'), str(cache_path))

    assert cache_path.read_text(encoding="utf-8") == '{"old": true}'
    assert list(tmp_path.glob(".token_cache.bin.*.tmp")) == []
