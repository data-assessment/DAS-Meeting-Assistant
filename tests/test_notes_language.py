"""App language (uiLanguage) for Meeting Notes messages and the summary output."""
import asyncio
import json
import re
from pathlib import Path

import pytest

from engine import meeting_notes as mn
from engine import notes_i18n
from engine.notes_i18n import t

ROOT = Path(__file__).resolve().parent.parent
# Literal keys only: t("a.b") / t('a.b'); template keys (t(`...${x}`)) are dynamic and skipped.
PY_KEY = re.compile(r"""(?<![\w.])(?:notes_i18n\.)?t\(\s*(["'])([^"'\\]+)\1""")
# Also tIn(language, 'a.b'), which looks a key up in a meeting's notes language.
TSX_KEY = re.compile(r"""(?<![\w.])(?:t\(|tIn\([^,()]+,)\s*(["'])([^"'\\]+)\1""")


def _leaves(node, trail=""):
    keys = set()
    for key, value in node.items():
        keys |= _leaves(value, f"{trail}{key}.") if isinstance(value, dict) else {trail + key}
    return keys


def _catalog_keys(folder):
    return {lang: _leaves(json.loads((folder / f"{lang}.json").read_text(encoding="utf-8"))) for lang in ("de", "en")}


def _missing(pattern, files, keys):
    """Keys used in `files` that exist in neither form (plain or _one/_other) in a catalog."""
    missing = []
    for path in files:
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            key = match.group(2)
            for lang, known in keys.items():
                if key not in known and not {f"{key}_one", f"{key}_other"} <= known:
                    missing.append(f"{path.relative_to(ROOT).as_posix()}: {key} ({lang})")
    return missing


@pytest.mark.parametrize("folder", [ROOT / "engine" / "locales", ROOT / "frontend" / "src" / "locales"])
def test_catalogs_have_identical_key_sets(folder):
    keys = _catalog_keys(folder)
    assert keys["de"], folder
    assert keys["de"] - keys["en"] == set(), "missing in en"
    assert keys["en"] - keys["de"] == set(), "missing in de"


def test_backend_keys_used_in_code_exist_in_both_catalogs():
    files = [*sorted((ROOT / "engine").rglob("*.py")), ROOT / "app.py"]
    assert _missing(PY_KEY, files, _catalog_keys(ROOT / "engine" / "locales")) == []


def test_frontend_keys_used_in_code_exist_in_both_catalogs():
    files = sorted((ROOT / "frontend" / "src").rglob("*.tsx"))
    assert _missing(TSX_KEY, files, _catalog_keys(ROOT / "frontend" / "src" / "locales")) == []


def test_t_fills_placeholders_and_selects_plurals(monkeypatch):
    monkeypatch.setitem(notes_i18n._catalogs, "de", {"demo": {
        "greeting": "Hallo {{name}}, {{missing}}", "items_one": "{{count}} Eintrag", "items_other": "{{count}} Einträge"}})
    assert t("demo.greeting", name="Ada") == "Hallo Ada, {{missing}}"
    assert t("demo.items", count=1) == "1 Eintrag"
    assert t("demo.items", count=0) == "0 Einträge"
    assert t("demo.items", count=3) == "3 Einträge"
    assert t("demo.unknown") == "demo.unknown"


def test_t_renders_catalog_texts_with_parameters():
    assert t("speech.notices.deviceChanged", microphone="Headset", playback="Lautsprecher") == (
        "Audiogerät gewechselt. Die Aufnahme läuft weiter mit Mikrofon „Headset“ und Wiedergabe „Lautsprecher“.")
    assert t("speech.errors.testLimit", minutes=120) == "Testlimit von 120 Minuten erreicht."
    notes_i18n.set_language("en")
    assert t("speech.errors.azureSourceKey", source=t("speech.sources.playback")) == (
        "Azure error on playback. Check the region, key, network and Speech permission.")
    assert notes_i18n.variants("tray.pastMeetings") == ("Frühere Meetings", "Previous meetings")


@pytest.fixture(autouse=True)
def _reset_language():
    notes_i18n.set_language("de")
    yield
    notes_i18n.set_language("de")


@pytest.fixture
def notes(tmp_path):
    notes = mn.Notes(tmp_path / "notes")
    yield notes
    notes.shutdown()


def test_app_language_is_separate_from_the_notes_options(notes):
    assert notes_i18n.language() == "de"
    notes.set_ui_language("en")
    assert notes_i18n.language() == "en" and notes.public_options()["uiLanguage"] == "en"
    assert "uiLanguage" not in notes.options  # can never invalidate the saved notes options
    with pytest.raises(ValueError):
        notes.set_ui_language("fr")
    with pytest.raises(ValueError):  # not a notes option; the settings forms never send it
        notes.configure({"enabled": True, "uiLanguage": "de"})
    assert notes_i18n.language() == "en"


def test_configure_validation_message_follows_app_language(notes):
    with pytest.raises(ValueError, match="Bitte eine Sprache wie de-DE oder en-US auswählen."):
        notes.configure({"enabled": True, "language": "deutsch"})
    notes.set_ui_language("en")
    with pytest.raises(ValueError) as error:
        notes.configure({"enabled": True, "language": "deutsch"})
    assert str(error.value) == "Please select a language such as de-DE or en-US."
    with pytest.raises(ValueError, match="Invalid settings."):
        notes.configure({"enabled": True, "unknown": "x"})


def test_system_prompt_is_english_and_only_names_the_output_language(monkeypatch):
    from engine import prompts
    monkeypatch.setattr(mn.config, "STT_DICTIONARY", [])
    monkeypatch.setattr(mn.config, "COMPANY_CONTEXT", "")
    german, english = mn.system_prompt("de"), mn.system_prompt("en")
    assert "write every text value" in german and "in German, even if" in german
    assert "in English, even if" in english
    assert german.replace("German", "English") == english  # one rule set for every language
    assert "{language}" not in german and "Erstelle" not in german
    assert prompts.language_name("de-DE") == "German" and prompts.language_name("French") == "French"


def test_t_keeps_intentionally_empty_texts(monkeypatch):
    monkeypatch.setitem(notes_i18n._catalogs, "de", {"demo": {"suffix": ""}})
    assert t("demo.suffix") == ""


@pytest.fixture
def api(monkeypatch, tmp_path, notes):
    from fastapi.testclient import TestClient
    import app
    from engine import settings_store
    monkeypatch.setattr(app, "NOTES", notes)
    monkeypatch.setattr(app, "STATE", app.AppState())
    monkeypatch.setattr(settings_store, "_PATH", tmp_path / "settings.json")
    return app, settings_store, TestClient(app.api)


def test_language_choice_is_saved_for_the_next_start(api):
    app, settings_store, client = api
    assert client.post("/api/notes/ui-language", json={"language": "en"}).json() == {"ok": True}
    saved = settings_store.load()
    # Saved on its own: older releases reject unknown keys inside meeting_notes_options.
    assert saved["ui_language"] == "en" and "uiLanguage" not in saved["meeting_notes_options"]
    assert app._ui_language(saved) == "en"


def test_unsaved_language_choice_is_reported_and_rolled_back(api, monkeypatch, tmp_path):
    app, settings_store, client = api
    monkeypatch.setattr(settings_store, "_PATH", tmp_path)  # A directory cannot be written as a settings file.
    result = client.post("/api/notes/ui-language", json={"language": "en"}).json()
    assert result == {"ok": False, "error": "Sprache konnte nicht gespeichert werden. Speicherort prüfen und erneut versuchen."}
    assert notes_i18n.language() == "de" and app.NOTES.public_options()["uiLanguage"] == "de"


def test_saved_language_survives_an_invalid_other_option(api):
    app, _, _ = api
    # configure() rejects the endpoint; the language must be restored anyway.
    app._apply_settings({"meeting_notes_enabled": True, "ui_language": "en",
                         "meeting_notes_options": {"endpoint": "http://not-https.example"}})
    assert app.NOTES.options["endpoint"] == ""  # the saved options were rejected as a whole
    assert notes_i18n.language() == "en"


def test_unknown_saved_language_keeps_the_other_options(api, monkeypatch):
    app, _, _ = api
    monkeypatch.setattr(notes_i18n, "system_language", lambda: "de")
    app._apply_settings({"meeting_notes_enabled": True, "ui_language": "fr",
                         "meeting_notes_options": {"language": "en-US", "mic": "Headset"}})
    assert app.NOTES.options["language"] == "en-US" and app.NOTES.options["mic"] == "Headset"
    assert notes_i18n.language() == "de"


def test_a_new_installation_starts_in_the_windows_display_language(api, monkeypatch):
    app, _, _ = api
    monkeypatch.setattr(notes_i18n, "system_language", lambda: "en")
    assert app._ui_language({}) == "en"


def test_an_existing_installation_keeps_german_until_the_user_switches(api, monkeypatch):
    app, _, _ = api
    monkeypatch.setattr(notes_i18n, "system_language", lambda: "en")
    app._apply_settings({"meeting_notes_enabled": True, "meeting_notes_options": {"language": "en-US"}})
    assert notes_i18n.language() == "de"  # no silent switch of UI and notes after an update


def test_settings_from_early_builds_keep_their_options_and_language(api):
    app, _, _ = api
    app._apply_settings({"meeting_notes_enabled": True,
                         "meeting_notes_options": {"language": "en-US", "mic": "Headset", "uiLanguage": "en"}})
    assert app.NOTES.options["mic"] == "Headset" and notes_i18n.language() == "en"


def test_a_damaged_meeting_file_is_skipped_instead_of_stopping_the_app(tmp_path):
    folder = tmp_path / "notes"
    (folder / ".app-state").mkdir(parents=True)
    import uuid
    for content in ("null", "[]", json.dumps({"meetingId": str(uuid.uuid4()), "messages": [
            {"path": ["title"], "text": "x", "message": 7, "params": {}}]})):
        (folder / ".app-state" / f"Meeting-Notizen-{uuid.uuid4()}.json").write_text(content, encoding="utf-8")
    notes = mn.Notes(folder)
    try:
        assert notes.error == "Eine gespeicherte Notiz konnte nicht geladen werden. Die Datei bleibt unverändert im Speicherordner."
    finally:
        notes.shutdown()


def test_restore_ignores_malformed_and_deeply_nested_messages():
    nested = {"text": "x", "message": "a", "params": {}}
    for _ in range(600):
        nested = {"text": "x", "message": "a", "params": {"p": nested}}
    data = notes_i18n.restore({"a": "x", "b": "y"}, [
        {"path": ["a"], "text": "x", "message": 7, "params": {}},
        {"path": ["b"], "text": "y", **{k: v for k, v in nested.items() if k != "text"}}])
    assert type(data["a"]) is str and isinstance(data["b"], notes_i18n.Message)


def test_a_message_whose_key_was_removed_keeps_its_stored_text():
    old = notes_i18n.Message("Alter Text", "notes.errors.renamedLongAgo", {})
    assert notes_i18n.localize(old) == "Alter Text"


def test_the_combined_capture_note_can_be_shown_again_in_another_language():
    from engine.speech.mixed import MixedCapture
    capture = MixedCapture.__new__(MixedCapture)
    capture.interruptions, capture.silent_since, capture.silent_seconds = 1, None, 3
    capture.mixer = type("Mixer", (), {"dropped_samples": 16000 * 2})()
    note = capture.summary()
    assert isinstance(note, notes_i18n.Message)
    notes_i18n.set_language("en")
    assert notes_i18n.localize(note).startswith("Audio recording was interrupted 1× during the meeting")
    assert notes_i18n.localize(note).endswith("About 2 s of audio are missing due to a delay.")


def test_errors_raised_while_drafting_use_the_app_language(monkeypatch):
    from types import SimpleNamespace
    def fail(text, provider, language):
        assert language == "de"  # the meeting's language reaches the prompt
        raise RuntimeError(t("cloud.errors.signIn"))
    monkeypatch.setattr(mn, "generate_draft", fail)
    notes_i18n.set_language("en")
    review = SimpleNamespace(language="de", provider={})
    with pytest.raises(RuntimeError, match="Please sign in with Microsoft in the settings."):
        asyncio.run(mn.review_draft(review, "text"))


def test_setup_cannot_change_the_app_language(monkeypatch, tmp_path):
    import config
    from fastapi.testclient import TestClient
    import app
    from engine import settings_store
    monkeypatch.setattr(config, "AI_MODE", "entra")
    managed = mn.Notes(tmp_path / "managed")
    managed.setup_complete = True
    monkeypatch.setattr(app, "NOTES", managed)
    monkeypatch.setattr(app, "STATE", app.AppState())
    monkeypatch.setattr(settings_store, "_PATH", tmp_path / "settings.json")
    preferences = dict(enabled=True, language="de-DE", uiLanguage="en", mic="", loopback="", autoStart=False)
    try:
        assert not TestClient(app.api).post("/api/notes/finish-setup", json=preferences).json()["ok"]
        assert notes_i18n.language() == "de"
    finally:
        managed.shutdown()


def test_stored_messages_follow_the_current_language_and_survive_persisting():
    message = t("speech.errors.azureSourceKey", source=t("speech.sources.playback"))
    data, messages = notes_i18n.persist({"error": message, "onenote": {"reason": t("cloud.errors.signIn")}})
    assert type(data["error"]) is str and json.dumps(data)  # plain, readable JSON
    restored = notes_i18n.restore(json.loads(json.dumps(data)), json.loads(json.dumps(messages)))
    notes_i18n.set_language("en")
    assert notes_i18n.localize(restored) == {
        "error": "Azure error on playback. Check the region, key, network and Speech permission.",
        "onenote": {"reason": "Please sign in with Microsoft in the settings."}}
    assert str(ValueError(message)) == message and str(ValueError(message)).key == message.key


def test_public_review_shows_stored_messages_in_the_current_language():
    from types import SimpleNamespace
    from engine.speech.core import Transcript
    session = SimpleNamespace(id="m", transcript=Transcript("m", ""), stop=lambda: None)
    review = mn.Review(session, "Abstimmung", "2026-09-16T10:00:00", {}, language="de")
    review.error = t("notes.errors.completionFailed")
    notes_i18n.set_language("en")
    assert review.public()["error"] == "Completing the meeting failed; raw transcript discarded."


def test_a_missing_catalog_falls_back_instead_of_crashing(monkeypatch):
    monkeypatch.setitem(notes_i18n._catalogs, "en", {})
    notes_i18n.set_language("en")
    assert t("api.errors.languageChangeFailed") == "Sprache konnte nicht geändert werden."
    monkeypatch.setitem(notes_i18n._catalogs, "de", {})
    assert t("api.errors.languageChangeFailed") == "api.errors.languageChangeFailed"


def test_placeholders_ignore_inherited_names_and_a_key_parameter(monkeypatch):
    monkeypatch.setitem(notes_i18n._catalogs, "de", {"demo": {"text": "{{key}} {{constructor}}", "n_one": "eins", "n_other": "viele"}})
    assert t("demo.text", key="K") == "K {{constructor}}"
    assert t("demo.n", count=True) == "demo.n"  # a bool is not a count: no "eins"


def test_public_review_carries_its_notes_language():
    from types import SimpleNamespace
    from engine.speech.core import Transcript
    session = SimpleNamespace(id="m", transcript=Transcript("m", ""), stop=lambda: None)
    review = mn.Review(session, "Abstimmung", "2026-09-16T10:00:00", {}, language="en")
    assert review.public()["language"] == "en"
