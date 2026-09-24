"""The store behind "you chose this for this meeting last time"."""
import json

from engine.choice_memory import ChoiceMemory

KEYS = {"series": "series-1", "group": "group-1", "title": "weekly sync"}


def test_nothing_remembered_yet(tmp_path):
    memory = ChoiceMemory(tmp_path / "choices.json")

    assert memory.recall("unused.scope", KEYS) is None


def test_a_choice_survives_a_restart(tmp_path):
    path = tmp_path / "choices.json"
    ChoiceMemory(path).remember("onenote.notebook", KEYS, "book-1")

    assert ChoiceMemory(path).recall("onenote.notebook", KEYS) == (
        "book-1", "series",
    )


def test_the_strongest_available_key_answers(tmp_path):
    memory = ChoiceMemory(tmp_path / "choices.json")
    memory.remember("onenote.notebook", KEYS, "book-1")

    # The same people meeting again without a series id is still recognised.
    assert memory.recall("onenote.notebook", {"group": "group-1"}) == (
        "book-1", "group",
    )
    assert memory.recall("onenote.notebook", {"title": "weekly sync"}) == (
        "book-1", "title",
    )


def test_a_later_choice_replaces_the_earlier_one(tmp_path):
    """This is how an override is learned — there is no rejection list."""
    memory = ChoiceMemory(tmp_path / "choices.json")
    memory.remember("onenote.notebook", KEYS, "book-1")

    memory.remember("onenote.notebook", KEYS, "book-9")

    assert memory.recall("onenote.notebook", KEYS) == ("book-9", "series")


def test_scopes_do_not_leak_into_each_other(tmp_path):
    memory = ChoiceMemory(tmp_path / "choices.json")
    memory.remember("onenote.notebook", KEYS, "book-1")
    memory.remember("onenote.section:book-1", KEYS, "Meetings")

    assert memory.recall("onenote.notebook", KEYS)[0] == "book-1"
    assert memory.recall("onenote.section:book-1", KEYS)[0] == "Meetings"
    assert memory.recall("unused.scope", KEYS) is None


def test_a_meeting_with_no_keys_is_not_stored(tmp_path):
    memory = ChoiceMemory(tmp_path / "choices.json")

    memory.remember("onenote.notebook", {}, "book-1")

    assert len(memory) == 0


def test_empty_values_are_ignored(tmp_path):
    memory = ChoiceMemory(tmp_path / "choices.json")

    memory.remember("onenote.notebook", KEYS, "")
    memory.remember("", KEYS, "book-1")

    assert len(memory) == 0


def test_the_oldest_entries_fall_off(tmp_path):
    memory = ChoiceMemory(tmp_path / "choices.json", limit=4)

    for index in range(6):
        memory.remember("onenote.notebook", {"series": f"s{index}"}, f"v{index}")

    assert len(memory) == 4
    assert memory.recall("onenote.notebook", {"series": "s0"}) is None
    assert memory.recall("onenote.notebook", {"series": "s5"}) == ("v5", "series")


def test_reusing_a_meeting_keeps_it_from_being_trimmed(tmp_path):
    memory = ChoiceMemory(tmp_path / "choices.json", limit=3)
    memory.remember("onenote.notebook", {"series": "keep"}, "v")

    for index in range(3):
        memory.remember("onenote.notebook", {"series": f"other{index}"}, "v")
        memory.remember("onenote.notebook", {"series": "keep"}, "v")

    assert memory.recall("onenote.notebook", {"series": "keep"}) == ("v", "series")


def test_a_corrupt_file_does_not_break_the_dialogs(tmp_path):
    path = tmp_path / "choices.json"
    path.write_text("{not json", encoding="utf-8")
    memory = ChoiceMemory(path)

    assert memory.recall("unused.scope", KEYS) is None

    memory.remember("onenote.notebook", KEYS, "book-1")
    assert memory.recall("onenote.notebook", KEYS) == ("book-1", "series")


def test_repointing_a_system_can_drop_its_scope(tmp_path):
    memory = ChoiceMemory(tmp_path / "choices.json")
    memory.remember("onenote.notebook", KEYS, "book-1")
    memory.remember("onenote.section:book-1", KEYS, "Meetings")

    memory.forget_scope("onenote.notebook")

    assert memory.recall("unused.scope", KEYS) is None
    assert memory.recall("onenote.section:book-1", KEYS)[0] == "Meetings"


def test_the_file_is_plain_readable_json(tmp_path):
    path = tmp_path / "choices.json"
    ChoiceMemory(path).remember("onenote.notebook", {"series": "s1"}, "book-1")

    written = json.loads(path.read_text(encoding="utf-8"))

    assert "entries" in written
    assert any("book-1" == v for v in written["entries"].values())
