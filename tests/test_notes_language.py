"""App language (uiLanguage) for Meeting Notes messages and the summary output."""
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
TSX_KEY = re.compile(r"""(?<![\w.])t\(\s*(["'])([^"'\\]+)\1""")


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


def test_configure_and_set_ui_language_switch_app_language(notes):
    assert notes_i18n.language() == "de"
    notes.configure({"enabled": True, "uiLanguage": "en"})
    assert notes_i18n.language() == "en" and notes.options["uiLanguage"] == "en"
    notes.set_ui_language("de")
    assert notes_i18n.language() == "de" and notes.options["uiLanguage"] == "de"
    with pytest.raises(ValueError):
        notes.set_ui_language("fr")
    with pytest.raises(ValueError):
        notes.configure({"enabled": True, "uiLanguage": "fr"})
    assert notes_i18n.language() == "de" and notes.options["uiLanguage"] == "de"


def test_configure_validation_message_follows_app_language(notes):
    with pytest.raises(ValueError, match="Bitte eine Sprache wie de-DE oder en-US auswählen."):
        notes.configure({"enabled": True, "language": "deutsch"})
    notes.configure({"enabled": True, "uiLanguage": "en"})
    with pytest.raises(ValueError) as error:
        notes.configure({"enabled": True, "language": "deutsch"})
    assert str(error.value) == "Please select a language such as de-DE or en-US."
    with pytest.raises(ValueError, match="Invalid settings."):
        notes.configure({"enabled": True, "unknown": "x"})


def test_system_prompt_keeps_german_rules_and_switches_output_language():
    notes_i18n.set_language("de")
    assert mn.system_prompt() == mn.SYSTEM
    notes_i18n.set_language("en")
    prompt = mn.system_prompt()
    assert prompt != mn.SYSTEM
    assert "auf Englisch schreiben" in prompt
    assert "englische Meeting-Notizen" in prompt


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
    assert settings_store.load()["meeting_notes_options"]["uiLanguage"] == "en"
    assert app._saved_ui_language() == "en"


def test_unsaved_language_choice_is_reported_and_rolled_back(api, monkeypatch, tmp_path):
    app, settings_store, client = api
    monkeypatch.setattr(settings_store, "_PATH", tmp_path)  # A directory cannot be written as a settings file.
    result = client.post("/api/notes/ui-language", json={"language": "en"}).json()
    assert result == {"ok": False, "error": "Sprache konnte nicht gespeichert werden. Speicherort prüfen und erneut versuchen."}
    assert notes_i18n.language() == "de" and app.NOTES.options["uiLanguage"] == "de"


def test_saved_language_survives_an_invalid_other_option(api):
    app, _, _ = api
    # configure() rejects the endpoint; the language must be restored anyway.
    app._apply_settings({"meeting_notes_enabled": True,
                         "meeting_notes_options": {"endpoint": "http://not-https.example", "uiLanguage": "en"}})
    assert app.NOTES.options["endpoint"] == ""  # the saved options were rejected as a whole
    assert notes_i18n.language() == "en"
