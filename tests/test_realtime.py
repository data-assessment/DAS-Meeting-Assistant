from engine.realtime import RealtimeSession, _clean_transcript_text


def test_clean_transcript_text_removes_realtime_audio_tokens():
    text = (
        "hello "
        "<|vq_hbr_audio_387|><|vq_hbr_audio_1824|>"
        " world"
    )

    assert _clean_transcript_text(text) == "hello  world"


def test_clean_transcript_text_preserves_normal_transcript_text():
    text = "Ja, das ist der naechste Schritt."

    assert _clean_transcript_text(text) == text


def test_clean_delta_removes_split_realtime_audio_tokens():
    session = RealtimeSession(lambda *_args: None)

    assert session._clean_delta("hello <|vq_hbr_audio_") == "hello "
    assert session._clean_delta("387|>world") == "world"


def test_direct_realtime_session_sends_dictionary_prompt(monkeypatch):
    monkeypatch.setattr("config.STT_DICTIONARY", ["CaféPlan", "Workboard"])
    session = RealtimeSession(lambda *_args: None, deployment="gpt-4o-transcribe")

    transcription = session._transcription_session_update()["session"][
        "input_audio_transcription"
    ]

    assert transcription["prompt"] == (
        "Important business terms and exact spellings: CaféPlan, Workboard."
    )


def test_realtime_translation_preserves_dictionary_spellings(monkeypatch):
    monkeypatch.setattr("config.STT_DICTIONARY", ["CaféPlan"])
    session = RealtimeSession(
        lambda *_args: None,
        deployment="gpt-realtime-translate",
        target_language="en",
    )

    assert "CaféPlan" in session._translate_session_update()["session"]["instructions"]



def test_removed_gateway_live_mode_fails_before_network_access(monkeypatch):
    import asyncio
    import pytest
    monkeypatch.setattr("config.AI_MODE", "gateway")
    available, reason = RealtimeSession.available()
    assert not available and "not included" in reason
    with pytest.raises(RuntimeError, match="Gateway live transcription is not included"):
        asyncio.run(RealtimeSession(lambda *_args: None).start())
