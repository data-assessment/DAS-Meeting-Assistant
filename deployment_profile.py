"""Validated, public installer configuration. This module uses only the stdlib."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import UUID

PROFILE_FILENAME = "deployment-profile.json"
_ENUMS = {
    "AI_ACCESS_MODE": {"gateway", "entra"},
    "GRAPH_AUTH_MODE": {"interactive", "device_code"},
    "STT_BACKEND": {"azure_openai", "none"},
    "STT_MODE": {"batch", "realtime"},
}
_UUIDS = {"GRAPH_CLIENT_ID", "AI_CLIENT_ID"}
_TENANTS = {"GRAPH_TENANT_ID", "AI_TENANT_ID"}
_URLS = {"AOAI_ENDPOINT", "AOAI_ENDPOINT_2", "AI_GATEWAY_ENDPOINT", "SPEECH_ENDPOINT",
         "USAGE_SERVICE_ENDPOINT"}
_MODELS = {"TRANSCRIBE_MODELS", "LIVE_MODELS", "SUMMARY_MODELS"}
_VERSIONS = {"AOAI_API_VERSION", "AOAI_API_VERSION_2", "STT_REALTIME_API_VERSION"}
_BOOLS = {"FETCH_ATTENDEES", "USE_TEAMS_TRANSCRIPT", "SUMMARIZE",
          "CLEAN_TRANSCRIPT", "ONENOTE_ENABLED"}
_LANGS = {"STT_LANGUAGE", "SUMMARY_LANGUAGE"}
ALLOWED_SETTINGS = frozenset().union(
    _ENUMS, _UUIDS, _TENANTS, _URLS, _MODELS, _VERSIONS, _BOOLS, _LANGS,
    {"AI_GATEWAY_SCOPE"})
_MANAGED_ONLY = {"AI_ACCESS_MODE", "SPEECH_ENDPOINT", "AI_CLIENT_ID", "AI_TENANT_ID", "AI_GATEWAY_ENDPOINT",
                 "AI_GATEWAY_SCOPE", "USAGE_SERVICE_ENDPOINT"}

class ProfileError(ValueError):
    """Messages identify the problem without echoing configuration values."""

@dataclass(frozen=True)
class DeploymentProfile:
    mode: str
    settings: dict[str, str]

    @property
    def data_namespace(self) -> str:
        return self.mode.capitalize()

    @property
    def display_name(self) -> str:
        return "DAS Meeting Assistant" + (" Community" if self.mode == "community" else "")

    @property
    def executable_name(self) -> str:
        return "MeetingTranscriber" + self.data_namespace

    @property
    def port(self) -> int:
        return 8765 if self.mode == "managed" else 8766

    def environment(self) -> dict[str, str]:
        return {**self.settings,
                "AI_MODE": (self.settings.get("AI_ACCESS_MODE") or "gateway") if self.mode == "managed" else "local"}

    def document(self) -> dict:
        return {"schema_version": 1, "mode": self.mode, "settings": self.settings}

def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError("Duplicate JSON field in deployment profile")
        result[key] = value
    return result

def _guid(value):
    try:
        return str(UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False

def validate_profile(document) -> DeploymentProfile:
    if not isinstance(document, dict) or set(document) != {"schema_version", "mode", "settings"}:
        raise ProfileError("Profile requires exactly schema_version, mode and settings")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ProfileError("Unsupported profile schema version")
    mode, settings = document["mode"], document["settings"]
    if mode not in ("community", "managed"):
        raise ProfileError("Profile mode must be community or managed")
    if not isinstance(settings, dict):
        raise ProfileError("Profile settings must be an object")
    # Older installers shipped these disabled switches. Ignore only their exact
    # disabled values so existing organization profiles keep working; no removed
    # integration settings or credentials reach the runtime.
    settings = dict(settings)
    for obsolete in ("JIRA_ENABLED", "CRM_ENABLED"):
        if settings.get(obsolete) == "false":
            del settings[obsolete]
    if set(settings) - ALLOWED_SETTINGS:
        raise ProfileError("Unsupported profile setting; secrets and arbitrary environment fields are forbidden")
    for name, value in settings.items():
        if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 32 for c in value):
            raise ProfileError(f"Invalid public configuration in {name}")
        if value != value.strip():
            raise ProfileError(f"Unexpected whitespace in {name}")
        if not value:
            continue
        valid = True
        if name in _ENUMS:
            valid = value in _ENUMS[name]
        elif name in _UUIDS:
            valid = _guid(value)
        elif name in _TENANTS:
            valid = _guid(value) or value in {"common", "organizations", "consumers"}
        elif name in _URLS:
            try:
                url = urlsplit(value)
                valid = (url.scheme == "https" and bool(url.hostname)
                         and url.username is None and url.password is None
                         and not url.query and not url.fragment
                         and re.fullmatch(r"https://[A-Za-z0-9./:_-]+", value) is not None
                         and url.port in (None, 443))
            except ValueError:
                valid = False
        elif name in _MODELS:
            valid = all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", m)
                        for m in value.split(","))
        elif name in _VERSIONS:
            valid = re.fullmatch(r"\d{4}-\d{2}-\d{2}(-preview)?", value) is not None
        elif name in _BOOLS:
            valid = value in {"true", "false"}
        elif name in _LANGS:
            valid = re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{2,4})?", value) is not None
        elif name == "AI_GATEWAY_SCOPE":
            match = re.fullmatch(r"api://([^/]+)/([A-Za-z][A-Za-z0-9_.-]*)", value)
            valid = bool(match and _guid(match[1]))
        if not valid:
            raise ProfileError(f"Invalid public configuration in {name}")
    if mode == "managed":
        direct = settings.get("AI_ACCESS_MODE") == "entra"
        required = {"GRAPH_CLIENT_ID", "GRAPH_TENANT_ID", "SUMMARY_MODELS"}
        required |= ({"AOAI_ENDPOINT", "SPEECH_ENDPOINT"} if direct else
                     {"AI_GATEWAY_ENDPOINT", "AI_GATEWAY_SCOPE", "USAGE_SERVICE_ENDPOINT", "TRANSCRIBE_MODELS"})
        for name in required:
            if not settings.get(name):
                raise ProfileError(f"Managed profile requires {name}")
        if direct:
            if not _guid(settings["GRAPH_TENANT_ID"]):
                raise ProfileError("Direct Entra access requires a fixed company tenant")
            if settings.get("AI_TENANT_ID", settings["GRAPH_TENANT_ID"]) != settings["GRAPH_TENANT_ID"]:
                raise ProfileError("Direct Entra access must use the company tenant")
            if any(settings.get(n) for n in {"AI_GATEWAY_ENDPOINT", "AI_GATEWAY_SCOPE", "USAGE_SERVICE_ENDPOINT", "AOAI_ENDPOINT_2"}):
                raise ProfileError("Direct Entra access cannot contain gateway or fallback routing")
            for name, suffix in (("SPEECH_ENDPOINT", "cognitiveservices.azure.com"), ("AOAI_ENDPOINT", "openai.azure.com")):
                if not re.fullmatch(r"https://[A-Za-z0-9-]+\." + re.escape(suffix) + r"/?", settings[name]):
                    raise ProfileError(f"Direct Entra access requires an Azure resource endpoint in {name}")
        elif any(settings.get(name) for name in {"AOAI_ENDPOINT", "AOAI_ENDPOINT_2", "SPEECH_ENDPOINT"}):
            raise ProfileError("Managed profile cannot contain direct Azure endpoints")
    elif any(settings.get(name) for name in _MANAGED_ONLY):
        raise ProfileError("Community profile cannot contain managed-service routing")
    return DeploymentProfile(mode, dict(settings))

def load_profile(path: Path) -> DeploymentProfile:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ProfileError("Cannot read deployment profile JSON") from None
    return validate_profile(document)
