"""Business dictionary and company context reach speech recognition and the notes model."""
import asyncio
from types import SimpleNamespace as NS

import pytest
from dotenv import dotenv_values

import config
from engine import meeting_notes as mn, settings_schema, summarize
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


def test_company_context_is_a_user_setting_in_managed_builds(monkeypatch):
    monkeypatch.setattr(config, "MANAGED_BUILD", True)
    keys = {field["key"] for group in settings_schema._schema_for_installation() for field in group["fields"]}
    assert {"COMPANY_CONTEXT", "STT_DICTIONARY"} <= keys


def test_notes_prompt_carries_context_and_dictionary_as_background(vocabulary):
    prompt = mn.system_prompt("de")
    assert prompt.startswith(mn.SYSTEM)
    assert "UNTERNEHMENSKONTEXT" in prompt and CONTEXT in prompt
    assert "Nichts daraus in die Notizen übernehmen, was im Gespräch nicht vorkam" in prompt
    assert "decídalo, Review-Agent" in prompt
    assert "englische Meeting-Notizen" in mn.system_prompt("en") and CONTEXT in mn.system_prompt("en")


def test_notes_prompt_is_unchanged_without_context(monkeypatch):
    monkeypatch.setattr(config, "STT_DICTIONARY", [])
    monkeypatch.setattr(config, "COMPANY_CONTEXT", "")
    assert mn.system_prompt("de") == mn.SYSTEM


def test_legacy_summary_gets_the_same_background(vocabulary, monkeypatch):
    seen = []
    monkeypatch.setattr(summarize, "_enabled", lambda: (True, ""))
    monkeypatch.setattr(summarize, "_chat", lambda messages: seen.append(messages) or ("ok", None))
    monkeypatch.setattr(config, "COMPANY_CONTEXT", CONTEXT + " {kein Platzhalter}")
    assert summarize.summarize("Transkript").text == "ok"
    assert CONTEXT + " {kein Platzhalter}" in seen[0][0]["content"]


def test_phrase_list_is_added_to_both_native_recognizers():
    import azure.cognitiveservices.speech as sdk
    speech = sdk.SpeechConfig(subscription="synthetic", region="westeurope")
    stream = sdk.audio.PushAudioInputStream()
    try:
        audio = sdk.audio.AudioConfig(stream=stream)
        for factory in (sdk.SpeechRecognizer, sdk.transcription.ConversationTranscriber):
            recognizer = factory(speech_config=speech, audio_config=audio)
            apply_phrase_list(sdk, recognizer, ["decídalo", "Review-Agent"])  # no network involved
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
