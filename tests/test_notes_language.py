"""App language (uiLanguage) for Meeting Notes messages and the summary output."""
import pytest

from engine import meeting_notes as mn
from engine import notes_i18n


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
