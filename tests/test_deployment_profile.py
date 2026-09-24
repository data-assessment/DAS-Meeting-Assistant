import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from deployment_profile import load_profile, validate_profile, ProfileError
import paths

ROOT = Path(__file__).resolve().parents[1]

def direct(**settings):
    return {"schema_version": 1, "mode": "managed", "settings": {
        "AI_ACCESS_MODE": "entra",
        "GRAPH_CLIENT_ID": "11111111-1111-4111-8111-111111111111",
        "GRAPH_TENANT_ID": "22222222-2222-4222-8222-222222222222",
        "SPEECH_ENDPOINT": "https://company-speech.cognitiveservices.azure.com",
        "AOAI_ENDPOINT": "https://company-text.openai.azure.com",
        "SUMMARY_MODELS": "summary-model", **settings,
    }}

@pytest.mark.parametrize("settings", [
    {"GRAPH_TENANT_ID": "organizations"}, {"AOAI_API_KEY": "forbidden"},
    {"AI_TENANT_ID": "33333333-3333-4333-8333-333333333333"},
    {"SPEECH_ENDPOINT": "https://westeurope.api.cognitive.microsoft.com"},
    {"AOAI_ENDPOINT": "https://proxy.example"}, {"USAGE_SERVICE_ENDPOINT": "https://control.example"},
    {"AI_GATEWAY_ENDPOINT": "https://gateway.example"},
])
def test_direct_profile_rejects_unsafe_or_mixed_routing(settings):
    with pytest.raises(ProfileError):
        validate_profile(direct(**settings))

def test_direct_profile_uses_managed_namespace_without_gateway():
    profile = validate_profile(direct())
    assert profile.environment()["AI_MODE"] == "entra"
    assert profile.data_namespace == "Managed"

def managed(**settings):
    return {"schema_version": 1, "mode": "managed", "settings": {
        "GRAPH_CLIENT_ID": "11111111-1111-4111-8111-111111111111",
        "GRAPH_TENANT_ID": "organizations",
        "AI_GATEWAY_ENDPOINT": "https://gateway.example/ai",
        "AI_GATEWAY_SCOPE": "api://22222222-2222-4222-8222-222222222222/access_as_user",
        "USAGE_SERVICE_ENDPOINT": "https://control.example",
        "TRANSCRIBE_MODELS": "speech-model",
        "SUMMARY_MODELS": "summary-model",
        **settings,
    }}

@pytest.mark.parametrize("key", ["AOAI_API_KEY", "AOAI_API_KEY_2", "SERVICE_CLIENT_SECRET",
                                "CLIENT_SECRET", "REFRESH_TOKEN", "STT_DICTIONARY", "HOST"])
def test_no_secret_or_arbitrary_env_field_can_enter_profile(key):
    with pytest.raises(ProfileError) as error:
        validate_profile(managed(**{key: "DO-NOT-LOG-THIS-VALUE"}))
    assert "DO-NOT-LOG-THIS-VALUE" not in str(error.value)

@pytest.mark.parametrize("url", ["http://gateway.example", "https://user:secret@gateway.example",
                                 "https://gateway.example?sig=secret", "https://gateway.example#secret",
                                 "https://gateway.example/\nSECRET=x", "https://gateway.example:bad"])
def test_credential_bearing_or_invalid_urls_rejected_without_echo(url):
    with pytest.raises(ProfileError) as error:
        validate_profile(managed(AI_GATEWAY_ENDPOINT=url))
    assert url not in str(error.value)

def test_duplicate_json_keys_fail(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text('{"schema_version":1,"mode":"community","mode":"managed","settings":{}}')
    with pytest.raises(ProfileError, match="Duplicate"):
        load_profile(path)

@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"schema_version": 2}, {"extra": "value"},
    {"mode": "other"}, {"settings": []},
])
def test_malformed_profiles_fail(change):
    with pytest.raises(ProfileError):
        validate_profile({**managed(), **change})

def test_managed_profile_requires_real_configuration():
    with pytest.raises(ProfileError, match="requires"):
        load_profile(ROOT / "packaging/profiles/managed.example.json")

def test_managed_rejects_direct_azure_fallback():
    with pytest.raises(ProfileError):
        validate_profile(managed(AOAI_ENDPOINT="https://direct.openai.azure.com"))


def test_community_rejects_managed_routing():
    document = managed()
    document["mode"] = "community"
    with pytest.raises(ProfileError, match="Community"):
        validate_profile(document)

def test_namespaces_ports_and_executables_are_separate():
    community = load_profile(ROOT / "packaging/profiles/community.json")
    service = validate_profile(managed())
    assert community.port != service.port
    assert community.data_namespace != service.data_namespace
    assert community.executable_name != service.executable_name

def test_frozen_app_requires_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "resource_path", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    with pytest.raises(ProfileError, match="requires"):
        paths.current_profile()

def test_source_defaults_to_community(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "resource_path", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert paths.current_profile().mode == "community"


def test_clean_app_profile_preserves_browser_environment(monkeypatch, tmp_path):
    normal = tmp_path / "windows-local-data"
    isolated = tmp_path / "signin-test"
    monkeypatch.setenv("LOCALAPPDATA", str(normal))
    monkeypatch.setenv("VOICE_TRANSCRIBER_DATA_ROOT", str(isolated))
    monkeypatch.setattr(paths, "current_profile", lambda: validate_profile(direct()))
    data = paths.data_dir()
    assert data == isolated / "MeetingTranscriber" / "Managed"
    assert not normal.exists()
    # The external browser must inherit its original Windows profile location.
    inherited = subprocess.check_output([sys.executable, "-c",
        "import os; print(os.environ['LOCALAPPDATA'])"], text=True).strip()
    assert inherited == str(normal)
    assert not (data / "token_cache.bin").exists()


def test_data_root_override_rejects_relative_paths(monkeypatch):
    monkeypatch.setenv("VOICE_TRANSCRIBER_DATA_ROOT", "relative-profile")
    with pytest.raises(ValueError, match="absolute"):
        paths.data_dir()

def test_data_paths_do_not_reuse_legacy_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for document, label in [(managed(), "Managed"),
                            ({"schema_version": 1, "mode": "community", "settings": {}}, "Community")]:
        monkeypatch.setattr(paths, "current_profile", lambda d=document: validate_profile(d))
        assert paths.data_dir() == tmp_path / "MeetingTranscriber" / label

@pytest.mark.parametrize("mode", ["managed", "community", "entra"])
def test_packaged_profile_isolates_runtime_routing(tmp_path, mode):
    resources = tmp_path / "resources"
    resources.mkdir()
    document = direct() if mode == "entra" else managed() if mode == "managed" else {
        "schema_version": 1, "mode": "community", "settings": {}}
    (resources / "deployment-profile.json").write_text(json.dumps(document))
    # A stray .env beside the installed program must never be read.
    (resources / ".env").write_text("GRAPH_CLIENT_ID=bundled-env-should-be-ignored\n")
    label = "Managed" if mode == "entra" else mode.capitalize()
    user = tmp_path / "MeetingTranscriber" / label
    user.mkdir(parents=True)
    (user / ".env").write_text(
        "AI_MODE=local\nAOAI_ENDPOINT=https://own.openai.azure.com\nAOAI_API_KEY=own-test-key\n"
        "AI_GATEWAY_ENDPOINT=https://stale.example/ai\nUSAGE_SERVICE_ENDPOINT=https://stale.example\n")
    other = tmp_path / "MeetingTranscriber" / ("Community" if mode in {"managed", "entra"} else "Managed")
    other.mkdir(parents=True)
    (other / ".env").write_text("SUMMARY_LANGUAGE=fr\n")
    program = """
import json, sys, pathlib
import paths
resources = pathlib.Path(sys.argv[1])
paths.resource_path = lambda *parts: resources.joinpath(*parts)
paths.is_frozen = lambda: True
import config
print(json.dumps({"mode": config.AI_MODE, "key": config.AOAI_API_KEY,
 "endpoint": config.AOAI_ENDPOINT, "speech": config.SPEECH_ENDPOINT, "managed": config.MANAGED_BUILD,
 "gateway": config.AI_GATEWAY_ENDPOINT, "usage": config.USAGE_SERVICE_ENDPOINT,
 "client": config.GRAPH_CLIENT_ID, "port": config.PORT,
 "language": config.SUMMARY_LANGUAGE}))
"""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AOAI_", "AI_", "GRAPH_", "STT_", "SUMMARY_", "USAGE_"))
           and k not in {"PORT", "HOST"}}
    env["LOCALAPPDATA"] = str(tmp_path)
    result = subprocess.run([sys.executable, "-c", program, str(resources)],
                            cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    actual = json.loads(result.stdout)
    assert actual["language"] != "fr"
    assert actual["client"] != "bundled-env-should-be-ignored"
    if mode == "entra":
        assert actual["mode"] == "entra" and actual["managed"]
        assert actual["key"] == actual["gateway"] == actual["usage"] == ""
        assert actual["endpoint"] == document["settings"]["AOAI_ENDPOINT"]
        assert actual["speech"] == document["settings"]["SPEECH_ENDPOINT"]
        assert actual["port"] == 8765
    elif mode == "managed":
        assert actual["mode"] == "gateway"
        assert actual["key"] == ""
        assert actual["gateway"] == "https://gateway.example/ai"
        assert actual["usage"] == "https://control.example"
        assert actual["port"] == 8765
    else:
        assert actual["mode"] == "local"
        assert actual["key"] == "own-test-key"
        assert actual["gateway"] == actual["usage"] == ""
        assert actual["port"] == 8766

def test_artifact_audit_rejects_env_and_profile_mismatch(tmp_path):
    spec = importlib.util.spec_from_file_location("profile_tool", ROOT / "packaging/profile_tool.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    profile = validate_profile(managed())
    internal = tmp_path / "_internal"
    internal.mkdir()
    bundled = internal / "deployment-profile.json"
    bundled.write_text(json.dumps(profile.document()))
    (tmp_path / (profile.executable_name + ".exe")).write_bytes(b"test")
    module.audit_artifact(tmp_path, profile)
    (internal / ".env").write_text("AOAI_API_KEY=do-not-log")
    with pytest.raises(ProfileError, match="Forbidden"):
        module.audit_artifact(tmp_path, profile)
    (internal / ".env").unlink()
    pem = internal / "certificate.pem"
    pem.write_text("-----BEGIN CERTIFICATE-----\npublic-test-data\n")
    module.audit_artifact(tmp_path, profile)
    pem.write_text("-----BEGIN PRIVATE KEY-----\nsynthetic-test-data\n")
    with pytest.raises(ProfileError, match="Private key"):
        module.audit_artifact(tmp_path, profile)
    pem.unlink()
    bundled.write_text(json.dumps({"schema_version":1,"mode":"community","settings":{}}))
    with pytest.raises(ProfileError, match="does not match"):
        module.audit_artifact(tmp_path, profile)
