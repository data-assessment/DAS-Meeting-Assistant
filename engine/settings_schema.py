"""Schema + read/write for the in-app Settings window — a GUI over the per-user .env.

The Settings window edits the same keys as bundle.env. Values are written to the
per-user .env in the data dir (which config.py loads with override=True), then
config.reload() re-reads them so changes apply without a restart (except HOST/PORT).

Mental model: two **connections** (primary + optional failover) are just plumbing —
endpoint/key/version. Each **capability** (Transcription, Live, Summary) has ONE
ordered model list; the top is the default/active model and the rest are fallbacks.
The connection is chosen automatically (primary preferred, failover if the primary
can't serve the model), so lists hold plain model names — no per-model connection.

Field types: text | number | bool | select | combo | list | secret.
- list:   ordered strings (add / remove / reorder); the FIRST is the default.
- secret: never sent to the client; an empty value on save means "leave unchanged".
- restart: only takes effect after an app restart.
- visibleWhenContains {key, value}: only show when another field contains `value`.
"""
from dotenv import dotenv_values

import config
import paths

_ENV_PATH = paths.data_dir() / ".env"

_LANGS = ["", "de", "en", "fr", "es", "it"]          # "" = auto-detect (STT hint)
_SUMMARY_LANGS = ["de", "en", "fr", "es", "it"]


def _ensure_env_marker() -> None:
    if _ENV_PATH.exists():
        return
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ENV_PATH.write_text(
        "# User settings override for MeetingTranscriber.\n"
        "# The app can also load bundled defaults baked into the installer.\n"
        "# Saving the Settings window writes values below this comment.\n",
        encoding="utf-8",
    )


def _env_info() -> dict:
    return {
        "userEnvPath": str(_ENV_PATH),
        "userEnvExists": _ENV_PATH.exists(),
        "bundledEnvExists": paths.has_deployment_profile(),
        "managedBuild": config.MANAGED_BUILD,
        "version": config.VERSION,
    }


def _f(key, label, ftype, **extra):
    return {"key": key, "label": label, "type": ftype, **extra}


SCHEMA = [
    {"group": "Microsoft sign-in (Graph)", "fields": [
        _f("GRAPH_TENANT_ID", "Tenant ID", "text"),
        _f("GRAPH_CLIENT_ID", "Client ID", "text"),
        _f("GRAPH_AUTH_MODE", "Auth mode", "select", options=["device_code", "interactive"]),
    ]},
    {"group": "AI access", "fields": [
        _f("AI_MODE", "Mode", "select", options=["local", "gateway"],
           help="Local uses the endpoint and key below. Gateway uses Microsoft "
                "sign-in and keeps Azure credentials off this PC."),
        _f("AI_GATEWAY_ENDPOINT", "Gateway endpoint", "text",
           visibleWhenEquals={"key": "AI_MODE", "value": "gateway"}),
        _f("AI_GATEWAY_SCOPE", "Gateway Entra scope", "text",
           visibleWhenEquals={"key": "AI_MODE", "value": "gateway"}),
        _f("AI_CLIENT_ID", "Desktop client ID (blank = Graph Client ID)", "text",
           visibleWhenEquals={"key": "AI_MODE", "value": "gateway"}),
        _f("AI_TENANT_ID", "Sign-in tenant", "text",
           help="Use organizations for customer tenants.",
           visibleWhenEquals={"key": "AI_MODE", "value": "gateway"}),
    ]},
    {"group": "Meeting detection", "fields": [
        _f("POLL_INTERVAL_SECONDS", "Presence poll interval (s)", "number"),
        _f("MAX_RECORDING_MINUTES", "Max recording minutes (0 = off)", "number"),
        _f("FETCH_ATTENDEES", "Fetch Teams attendance report", "bool"),
        _f("USE_TEAMS_TRANSCRIPT", "Prefer Teams transcript when available", "bool",
           help="For meetings you organized with transcription on, use Teams' own "
                "transcript (named speakers, correct language) instead of speech-to-text."),
    ]},
    {"group": "Azure OpenAI — primary connection", "fields": [
        _f("AOAI_ENDPOINT", "Endpoint", "text",
           visibleWhenEquals={"key": "AI_MODE", "value": "local"}),
        _f("AOAI_API_KEY", "API key", "secret",
           visibleWhenEquals={"key": "AI_MODE", "value": "local"}),
        _f("AOAI_API_VERSION", "API version", "text"),
    ]},
    {"group": "Azure OpenAI — failover connection (optional)", "fields": [
        _f("AOAI_ENDPOINT_2", "Endpoint", "text",
           help="A backup connection. Any model below is tried on the primary first, "
                "then here automatically — no separate model list.",
           visibleWhenEquals={"key": "AI_MODE", "value": "local"}),
        _f("AOAI_API_KEY_2", "API key", "secret",
           visibleWhenEquals={"key": "AI_MODE", "value": "local"}),
        _f("AOAI_API_VERSION_2", "API version (blank = reuse primary)", "text",
           visibleWhenEquals={"key": "AI_MODE", "value": "local"}),
    ]},
    {"group": "Transcription — batch", "fields": [
          _f("STT_BACKEND", "Speech-to-text provider", "select",
              options=["azure_openai", "none"],
              help="Provider implementation, not a model/deployment name."),
        _f("TRANSCRIBE_MODELS", "Models (first = default, rest = fallbacks)", "list",
           help="Reorder to set the default; models are tried top-down, each on the "
                "primary connection then the failover."),
        _f("STT_REQUEST_TIMEOUT_SECONDS", "Request timeout (s)", "number",
           help="Maximum time for one Azure transcription request before retry/failover."),
        _f("STT_MAX_RETRIES", "Retry attempts per provider", "number"),
        _f("STT_RETRY_MAX_DELAY_SECONDS", "Max retry delay (s)", "number"),
        _f("STT_LANGUAGE", "Language", "select", options=_LANGS,
           help="Force the transcription language, or Auto to detect."),
        _f("STT_DICTIONARY", "Important business terms", "list",
           help="Exact names and jargon to guide compatible batch and live "
                "transcription models. Stored only in your user settings. "
                "The diarization model does not support prompt guidance."),
        _f("CLEAN_TRANSCRIPT", "Clean plain transcripts with the chat model", "bool"),
    ]},
    {"group": "Live transcript — realtime", "fields": [
        _f("LIVE_MODELS", "Live models (first = default)", "list",
           help="Reorder to set the default live model. Live uses the primary "
                "connection only (the realtime API has no failover)."),
        _f("STT_REALTIME_API_VERSION", "Realtime API version", "text"),
        _f("STT_REALTIME_WHISPER_DELAY", "Whisper delay", "select",
           options=["", "minimal", "low", "medium", "high", "xhigh"],
           visibleWhenContains={"key": "LIVE_MODELS", "value": "whisper"}),
    ]},
    {"group": "Summary", "fields": [
        _f("SUMMARIZE", "Generate a summary", "bool"),
        _f("SUMMARY_MODELS", "Chat models (first = default, rest = fallbacks)", "list",
           help="Chat model(s) for summary / cleanup / translate; tried top-down, "
                "primary connection then failover."),
        _f("SUMMARY_LANGUAGE", "Summary language", "select", options=_SUMMARY_LANGS),
    ]},
    {"group": "OneNote", "fields": [
        _f("ONENOTE_ENABLED", "Offer 'Save to OneNote' after meetings", "bool"),
        _f("ONENOTE_NOTEBOOK", "Notebook", "text",
           visibleWhenEquals={"key": "ONENOTE_ENABLED", "value": "true"}),
        _f("ONENOTE_SECTION", "Section", "text",
           visibleWhenEquals={"key": "ONENOTE_ENABLED", "value": "true"}),
        _f("ONENOTE_SITE_PATHS", "SharePoint sites", "list",
           help="Optional SharePoint site URLs whose OneNote notebooks should be "
            "listed. Use an exact site URL, e.g. "
            "https://tenant.sharepoint.com/sites/Team, or a tenant base URL "
            "to search accessible sites under that SharePoint host.",
           visibleWhenEquals={"key": "ONENOTE_ENABLED", "value": "true"}),
    ]},
    {"group": "Audio capture", "fields": [
        _f("RECORD_AUDIO", "Record audio", "bool"),
        _f("AUDIO_DIR", "Recordings folder", "text"),
        _f("AUDIO_SAMPLE_RATE", "Sample rate (Hz)", "number"),
        _f("AUDIO_NORMALIZE", "Peak-normalize the mix", "bool"),
        _f("AUDIO_TARGET_PEAK_DBFS", "Target peak (dBFS)", "number"),
        _f("AUDIO_MAX_GAIN", "Max gain (dB)", "number"),
        _f("AUDIO_SILENCE_PROMPT_SECONDS", "Silence prompt after (s, 0 = off)", "number"),
        _f("AUDIO_ACTIVITY_DBFS", "Activity threshold (dBFS)", "number"),
    ]},
    {"group": "Output & server", "fields": [
        _f("TRANSCRIPT_DIR", "Transcripts folder", "text"),
        _f("ATTENDEE_RETRIES", "Attendance retries", "number"),
        _f("ATTENDEE_RETRY_DELAY_SECONDS", "Attendance retry delay (s)", "number"),
        _f("HOST", "Server host", "text", restart=True),
        _f("PORT", "Server port", "number", restart=True),
    ]},
]

SECRET_KEYS = {f["key"] for g in SCHEMA for f in g["fields"] if f["type"] == "secret"}

# A managed installation deliberately exposes only preferences that can be honored
# without changing the organization identity, cloud routing, deployed models or operational
# protocol settings. The full schema remains available to standalone/BYO installs.
_MANAGED_GROUPS = [
    ("Microsoft sign-in (Graph)", []),
    ("Managed service", []),
    ("Meeting and transcription", [
        "MAX_RECORDING_MINUTES", "FETCH_ATTENDEES", "USE_TEAMS_TRANSCRIPT",
        "STT_LANGUAGE", "STT_DICTIONARY", "CLEAN_TRANSCRIPT",
    ]),
    ("Summary", ["SUMMARIZE", "SUMMARY_LANGUAGE"]),
    ("OneNote", [
        "ONENOTE_ENABLED", "ONENOTE_NOTEBOOK", "ONENOTE_SECTION",
        "ONENOTE_SITE_PATHS",
    ]),
    ("Local storage and recording", [
        "TRANSCRIPT_DIR", "AUDIO_DIR", "AUDIO_SILENCE_PROMPT_SECONDS",
    ]),
]


def _schema_for_installation() -> list[dict]:
    if not config.MANAGED_BUILD:
        return SCHEMA
    fields_by_key = {
        field["key"]: field
        for group in SCHEMA
        for field in group["fields"]
    }
    return [{
        "group": group,
        "fields": [fields_by_key[key] for key in keys],
    } for group, keys in _MANAGED_GROUPS]


def _current(field) -> object:
    """Current effective value for a field, formatted for the form."""
    val = getattr(config, field["key"], None)
    if field["type"] == "bool":
        return bool(val)
    if field["type"] == "list":
        return list(val) if isinstance(val, list) else ([] if not val else [val])
    return "" if val is None else str(val)


def public_state() -> dict:
    """Schema + current values for the client. Secrets are never sent — only whether
    one is set (so the field can show a placeholder)."""
    _ensure_env_marker()
    schema = _schema_for_installation()
    values, secrets_set = {}, {}
    for group in schema:
        for field in group["fields"]:
            if field["key"] in SECRET_KEYS:
                values[field["key"]] = ""
                secrets_set[field["key"]] = bool(getattr(config, field["key"], "") or "")
            else:
                values[field["key"]] = _current(field)
    restart_keys = sorted({
        f["key"] for g in schema for f in g["fields"] if f.get("restart")
    })
    return {"schema": schema, "values": values, "secretsSet": secrets_set,
            "restartKeys": restart_keys, "envInfo": _env_info()}


def _to_env(val) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, list):
        return ",".join(str(x).strip() for x in val if str(x).strip())
    return str(val)


def write_env(updates: dict) -> list[str]:
    """Merge the posted values into the per-user .env (preserving any unmanaged keys).
    Superseded legacy model keys are removed so they can't shadow the new *_MODELS
    lists. Returns the keys whose value changed. A blank secret is ignored (keeps the
    stored one)."""
    allowed_keys = {
        field["key"]
        for group in _schema_for_installation()
        for field in group["fields"]
    }
    unsupported = sorted(set(updates) - allowed_keys)
    if unsupported:
        raise ValueError(
            "These settings are controlled by the managed service: "
            + ", ".join(unsupported)
        )
    existing = {k: v for k, v in dotenv_values(_ENV_PATH).items() if v is not None} \
        if _ENV_PATH.exists() else {}
    for legacy in config.LEGACY_MODEL_KEYS:
        existing.pop(legacy, None)  # migrate away from the old per-resource keys
    changed = []
    for key, raw in updates.items():
        if key in SECRET_KEYS and (raw is None or raw == ""):
            continue  # "leave unchanged"
        if key == "STT_DICTIONARY":
            raw = config.normalize_stt_dictionary(raw, strict=True)
        val = _to_env(raw)
        if existing.get(key) != val:
            changed.append(key)
        existing[key] = val
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{k}={v}" for k, v in existing.items())
    _ENV_PATH.write_text(body + "\n", encoding="utf-8")
    return changed
