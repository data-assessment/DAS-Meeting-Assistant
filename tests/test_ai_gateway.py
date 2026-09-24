import importlib

import config
import paths
from engine import ai_auth
from engine.realtime import RealtimeSession


def _reload(monkeypatch, tmp_path, **values):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for key in (
        "AI_MODE", "AI_GATEWAY_ENDPOINT", "AI_GATEWAY_SCOPE",
        "AI_CLIENT_ID", "AI_TENANT_ID", "USAGE_SERVICE_ENDPOINT",
        "AOAI_ENDPOINT", "AOAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    importlib.reload(config)


def test_default_mode_remains_local(monkeypatch, tmp_path):
    _reload(
        monkeypatch, tmp_path,
        AOAI_ENDPOINT="https://local.openai.azure.com/",
        AOAI_API_KEY="local-key",
    )
    try:
        assert config.AI_MODE == "local"
        assert ai_auth.available() == (True, None)
        provider = config.chat_providers()[0]
        assert provider["endpoint"] == "https://local.openai.azure.com/"
        assert provider["auth"] == "key"
        assert ai_auth.openai_auth_kwargs(provider) == {"api_key": "local-key"}
    finally:
        importlib.reload(config)


def test_gateway_mode_uses_entra_provider_without_key(monkeypatch, tmp_path):
    _reload(
        monkeypatch, tmp_path,
        AI_MODE="gateway",
        AI_GATEWAY_ENDPOINT="https://gateway.example/ai/",
        AI_GATEWAY_SCOPE="api://gateway/access_as_user",
        AI_CLIENT_ID="desktop-client",
        AI_TENANT_ID="organizations",
        USAGE_SERVICE_ENDPOINT="https://control.example",
    )
    try:
        assert ai_auth.available() == (True, None)
        provider = config.chat_providers()[0]
        assert provider["endpoint"] == "https://gateway.example/ai"
        assert provider["auth"] == "entra"
        assert provider["key"] == ""
        assert "azure_ad_token_provider" in ai_auth.openai_auth_kwargs(provider)
        available, reason = RealtimeSession.available()
        assert not available
        assert "Gateway live transcription is not included" in reason
    finally:
        importlib.reload(config)


def test_gateway_mode_requires_scope(monkeypatch, tmp_path):
    _reload(
        monkeypatch, tmp_path,
        AI_MODE="gateway",
        AI_GATEWAY_ENDPOINT="https://gateway.example/ai",
        AI_CLIENT_ID="desktop-client",
    )
    try:
        available, reason = ai_auth.available()
        assert not available
        assert reason == "AI_GATEWAY_SCOPE not set"
    finally:
        importlib.reload(config)


def test_gateway_bundle_wins_over_stale_local_ai_settings(monkeypatch, tmp_path):
    resources = tmp_path / "resources"
    user_data = tmp_path / "user-data"
    resources.mkdir()
    user_data.mkdir()
    (resources / ".env").write_text(
        "GRAPH_TENANT_ID=organizations\n"
        "GRAPH_CLIENT_ID=new-desktop\n"
        "AI_MODE=gateway\n"
        "AI_GATEWAY_ENDPOINT=https://gateway.example/ai\n"
        "AI_GATEWAY_SCOPE=api://gateway/access_as_user\n"
        "AI_CLIENT_ID=new-desktop\n"
        "AI_TENANT_ID=organizations\n"
        "STT_BACKEND=azure_openai\n"
        "STT_MODE=batch\n"
        "TRANSCRIBE_MODELS=gpt-4o-transcribe\n"
        "LIVE_MODELS=gpt-4o-transcribe\n"
        "SUMMARY_MODELS=gpt-5.4\n"
        "AOAI_API_VERSION=2024-10-21\n",
        encoding="utf-8",
    )
    (user_data / ".env").write_text(
        "GRAPH_CLIENT_ID=old-desktop\n"
        "AI_MODE=local\n"
        "TRANSCRIBE_MODELS=whisper\n"
        "AOAI_ENDPOINT=https://old.openai.azure.com\n"
        "AOAI_API_KEY=old-key\n",
        encoding="utf-8",
    )
    with monkeypatch.context() as patch:
        patch.setattr(paths, "resource_path", lambda *parts: resources.joinpath(*parts))
        patch.setattr(paths, "data_dir", lambda: user_data)
        importlib.reload(config)
        assert config.AI_MODE == "gateway"
        assert config.GRAPH_CLIENT_ID == "new-desktop"
        assert config.TRANSCRIBE_MODELS == ["gpt-4o-transcribe"]
        assert config.AOAI_ENDPOINT == ""
        assert config.AOAI_API_KEY == ""
    importlib.reload(config)
