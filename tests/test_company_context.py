"""Business dictionary and company context reach speech recognition and the notes model."""
import asyncio
import contextlib
import importlib
import os
from types import SimpleNamespace as NS

import pytest
from dotenv import dotenv_values

import config
from engine import meeting_notes as mn, prompts, settings_schema, summarize
from engine.speech.mixed import MixedSession
from engine.speech.session import apply_phrase_list
from test_audio_device_recovery import (HEADSET_MIC, HEADSET_OUT, LAPTOP_MIC, LAPTOP_OUT, Machine,
                                        speech_sdk, wait)
from test_meeting_notes import DRAFT, notes  # noqa: F401 - fixture

CONTEXT = ("Data Assessment Solutions GmbH entwickelt decídalo.\n\n"
           "Teams: Plattform, Beratung.\nWerkzeuge: Jira, \"Teams\" #intern")


@pytest.fixture
def vocabulary(monkeypatch):
    monkeypatch.setattr(config, "STT_DICTIONARY", ["decídalo", "Review-Agent"])
    monkeypatch.setattr(config, "COMPANY_CONTEXT", CONTEXT)


def test_company_context_keeps_paragraphs_and_rejects_overlong_text():
    text = config.normalize_company_context("  A\r\n\r\n\r\n\r\nB\x07  \n")
    assert text == "A\n\nB"
    assert len(config.normalize_company_context("x" * 5000)) == config.COMPANY_CONTEXT_MAX_LENGTH
    with pytest.raises(ValueError, match="at most 4000"):
        config.normalize_company_context("x" * 5000, strict=True)


def test_multi_line_context_survives_the_settings_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(settings_schema, "_ENV_PATH", env)
    changed = settings_schema.write_env({"COMPANY_CONTEXT": CONTEXT, "STT_DICTIONARY": ["decídalo", "PoC"]})
    assert set(changed) == {"COMPANY_CONTEXT", "STT_DICTIONARY"}
    saved = dotenv_values(env)
    assert saved["COMPANY_CONTEXT"] == CONTEXT and saved["STT_DICTIONARY"] == "decídalo,PoC"
    assert settings_schema.write_env({"COMPANY_CONTEXT": CONTEXT}) == []  # unchanged on a second save


@contextlib.contextmanager
def _exact_environment():
    """Restore os.environ exactly, including absent versus empty values. load_dotenv writes
    keys that monkeypatch never saw, so monkeypatch alone cannot undo them."""
    before = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)
        importlib.reload(config)


def _save_reload_and_save_again(tmp_path):
    """Save the context, reload as the app does, make an unrelated save, reload again."""
    import paths
    text = "Kosten ${UNSET_VARIABLE} und ${EXISTING_VARIABLE:-standard} sowie ${EXISTING_VARIABLE}\nZweite Zeile"
    with _exact_environment(), pytest.MonkeyPatch.context() as patch:
        patch.setenv("LOCALAPPDATA", str(tmp_path))
        patch.setenv("EXISTING_VARIABLE", "SECRET-VALUE")
        patch.delenv("UNSET_VARIABLE", raising=False)
        patch.setattr(settings_schema, "_ENV_PATH", paths.data_dir() / ".env")
        settings_schema.write_env({"COMPANY_CONTEXT": text})
        importlib.reload(config)  # what the app does after saving and at the next start
        assert config.COMPANY_CONTEXT == text
        settings_schema.write_env({"SUMMARIZE": False})  # an unrelated later save
        importlib.reload(config)
        assert config.COMPANY_CONTEXT == text
        assert dotenv_values(settings_schema._ENV_PATH, interpolate=False)["COMPANY_CONTEXT"] == text
        assert "SECRET-VALUE" not in settings_schema._ENV_PATH.read_text(encoding="utf-8")


def test_context_with_dollar_braces_stays_literal_through_reload_and_later_saves(tmp_path):
    """Quoting alone does not stop python-dotenv's ${...} expansion (both quote styles)."""
    before = dict(os.environ)
    _save_reload_and_save_again(tmp_path)
    assert dict(os.environ) == before and config.COMPANY_CONTEXT == ""


def test_the_literal_check_restores_pre_existing_and_empty_values(tmp_path):
    """The scenario rewrites SUMMARIZE and COMPANY_CONTEXT; the caller's values must come back."""
    with _exact_environment():
        os.environ["SUMMARIZE"] = "false"   # e.g. exported by a developer or CI job
        os.environ["COMPANY_CONTEXT"] = ""  # present but empty, which is not the same as absent
        os.environ.pop("STT_DICTIONARY", None)
        before = dict(os.environ)
        _save_reload_and_save_again(tmp_path)
        assert dict(os.environ) == before
        assert os.environ["SUMMARIZE"] == "false" and os.environ["COMPANY_CONTEXT"] == ""
        assert "STT_DICTIONARY" not in os.environ
    assert config.COMPANY_CONTEXT == ""


def test_company_context_is_a_user_setting_in_managed_builds(monkeypatch):
    monkeypatch.setattr(config, "MANAGED_BUILD", True)
    keys = {field["key"] for group in settings_schema._schema_for_installation() for field in group["fields"]}
    assert {"COMPANY_CONTEXT", "STT_DICTIONARY"} <= keys


def test_notes_prompt_carries_context_and_dictionary_as_background(vocabulary):
    prompt = mn.system_prompt("de")
    rules = prompts.NOTES.replace("{language}", "German")
    assert prompt.startswith(rules)
    assert "COMPANY CONTEXT" in prompt and CONTEXT in prompt
    assert "Do not add anything from it to the notes that was not said in the conversation" in prompt
    assert "decídalo, Review-Agent" in prompt
    assert "in English, even if" in mn.system_prompt("en") and CONTEXT in mn.system_prompt("en")


def test_notes_prompt_is_unchanged_without_context(monkeypatch):
    monkeypatch.setattr(config, "STT_DICTIONARY", [])
    monkeypatch.setattr(config, "COMPANY_CONTEXT", "")
    assert mn.system_prompt("de") == prompts.NOTES.replace("{language}", "German")


def test_legacy_summary_gets_the_same_background(vocabulary, monkeypatch):
    seen = []
    monkeypatch.setattr(summarize, "_enabled", lambda: (True, ""))
    monkeypatch.setattr(summarize, "_chat", lambda messages: seen.append(messages) or ("ok", None))
    monkeypatch.setattr(config, "COMPANY_CONTEXT", CONTEXT + " {kein Platzhalter}")
    assert summarize.summarize("Transkript", language="de").text == "ok"
    system = seen[0][0]["content"]
    assert CONTEXT + " {kein Platzhalter}" in system
    assert "Answer only in German" in system and "{language}" not in system
    assert seen[0][1]["content"] == "Participants: unknown\n\nTranscript:\nTranskript"


def test_phrase_list_is_added_to_both_native_recognizers():
    """The shipped SDK accepts the phrase list for both recognizers (no network involved).
    apply_phrase_list swallows errors, so its result must be asserted, not just its absence of
    exceptions. Whether the service then applies it remains to be verified on a real resource."""
    import azure.cognitiveservices.speech as sdk
    speech = sdk.SpeechConfig(subscription="synthetic", region="westeurope")
    stream = sdk.audio.PushAudioInputStream()
    try:
        audio = sdk.audio.AudioConfig(stream=stream)
        for factory in (sdk.SpeechRecognizer, sdk.transcription.ConversationTranscriber):
            recognizer = factory(speech_config=speech, audio_config=audio)
            assert apply_phrase_list(sdk, recognizer, ["decídalo", "Review-Agent"]) is True, factory.__name__
    finally:
        stream.close()


def test_a_failing_phrase_list_never_stops_capture(capsys):
    broken = NS(PhraseListGrammar=NS(from_recognizer=lambda recognizer: (_ for _ in ()).throw(RuntimeError("x"))))
    apply_phrase_list(broken, object(), ["decídalo"])
    assert "business dictionary not applied" in capsys.readouterr().out


def test_a_meeting_biases_recognition_towards_the_dictionary(notes, monkeypatch, vocabulary):
    machine, recognizers, phrases = Machine(), [], []
    monkeypatch.setattr(mn, "generate_draft", lambda *_: dict(DRAFT))
    sdk = speech_sdk(recognizers)
    sdk.PhraseListGrammar = NS(from_recognizer=lambda recognizer: NS(
        addPhrase=lambda phrase: phrases.append((recognizer, phrase))))
    factory = lambda name: MixedSession(name, sdk, machine.factory, lambda: list(machine.devices), machine.names)
    review = notes.start("Test-Meeting", "2026-10-01T10:00:00", [HEADSET_MIC, HEADSET_OUT, LAPTOP_MIC, LAPTOP_OUT], factory)
    wait(lambda: review.session.phase == "Läuft")
    assert phrases == [(recognizers[0], "decídalo"), (recognizers[0], "Review-Agent")]
    notes.current = None
    asyncio.run(notes.finish(review))


@pytest.mark.parametrize("language, term_error, context_error", [
    ("de", "Der Begriff „Müller & Partner, Berlin“ kann nicht gespeichert werden", "höchstens 4000 Zeichen"),
    ("en", "The term “Müller & Partner, Berlin” cannot be saved", "at most 4000 characters")])
def test_settings_errors_are_translated_and_name_the_term(tmp_path, monkeypatch, language, term_error, context_error):
    from engine import notes_i18n
    monkeypatch.setattr(settings_schema, "_ENV_PATH", tmp_path / ".env")
    notes_i18n.set_language(language)
    with pytest.raises(ValueError, match=term_error):
        settings_schema.write_env({"STT_DICTIONARY": ["decídalo", "Müller & Partner, Berlin"]})
    with pytest.raises(ValueError, match=context_error):
        settings_schema.write_env({"COMPANY_CONTEXT": "x" * 4001})
    with pytest.raises(ValueError, match="100"):
        settings_schema.write_env({"STT_DICTIONARY": [f"Begriff {i}" for i in range(101)]})
    assert not (tmp_path / ".env").exists()  # nothing half-written


def test_a_failed_dictionary_is_shown_during_and_after_the_meeting(notes, monkeypatch, vocabulary):
    machine, recognizers = Machine(), []
    monkeypatch.setattr(mn, "generate_draft", lambda *_: dict(DRAFT))
    sdk = speech_sdk(recognizers)
    sdk.PhraseListGrammar = NS(from_recognizer=lambda recognizer: (_ for _ in ()).throw(RuntimeError("unsupported")))
    factory = lambda name: MixedSession(name, sdk, machine.factory, lambda: list(machine.devices), machine.names)
    review = notes.start("Test-Meeting", "2026-10-01T10:00:00", [HEADSET_MIC, HEADSET_OUT, LAPTOP_MIC, LAPTOP_OUT], factory)
    wait(lambda: review.session.phase == "Läuft")
    warning = "Das Wörterbuch konnte der Spracherkennung nicht übergeben werden"
    assert warning in review.public()["warning"]  # the meeting still runs, with a visible notice
    recognizers[0].say("Wir besprechen den Review-Agent.", 1)  # captured despite the warning
    notes.current = None
    asyncio.run(notes.finish(review))
    assert warning in review.warning and review.status == "Gespräch beendet"
