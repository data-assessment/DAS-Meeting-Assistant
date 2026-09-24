"""Central configuration, read from environment (.env supported)."""
import importlib
import os
import re
import sys
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

import paths

VERSION = "0.40.15"


def _writable_dir(env_name: str, subdir: str) -> str:
    """Resolve an output directory. Honors an env override that is absolute (always)
    or relative (dev only); when frozen a relative/empty value falls back to the
    per-user data dir, since the install dir is read-only."""
    val = os.getenv(env_name)
    if val and (Path(val).is_absolute() or not paths.is_frozen()):
        return val
    return str(paths.data_dir() / subdir)


def _csv_env(env_name: str, default: str = "") -> list[str]:
    """Read a comma-separated env var, treating blank the same as unset."""
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        raw = default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _text_env(env_name: str, default: str = "") -> str:
    """Read a text env var; treat template comments after '=' as blank."""
    raw = os.getenv(env_name)
    if raw is None:
        return default
    value = raw.strip()
    if value.startswith("#"):
        return ""
    return value


STT_DICTIONARY_MAX_TERMS = 100
STT_DICTIONARY_MAX_TERM_LENGTH = 80


def normalize_stt_dictionary(values, *, strict: bool = False) -> list[str]:
    """Normalize user-maintained STT terms while preserving their spelling.

    The environment representation is comma-separated; the Settings UI supplies a
    list. Case-insensitive duplicates are removed in first-seen order. ``strict``
    is used for Settings writes so an entry that cannot round-trip through .env is
    reported instead of being silently changed.
    """
    if values is None:
        candidates = []
    elif isinstance(values, str):
        candidates = re.split(r"[,\r\n]+", values)
    else:
        candidates = list(values)

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        term = " ".join(str(raw or "").split())
        if not term:
            continue
        invalid = (
            len(term) > STT_DICTIONARY_MAX_TERM_LENGTH
            or any(char in term for char in (",", "=", "<", ">"))
        )
        if invalid:
            if strict:
                raise ValueError(
                    "Dictionary terms must be at most "
                    f"{STT_DICTIONARY_MAX_TERM_LENGTH} characters and cannot "
                    "contain commas, equals signs, or angle brackets."
                )
            continue
        identity = term.casefold()
        if identity in seen:
            continue
        if len(normalized) >= STT_DICTIONARY_MAX_TERMS:
            if strict:
                raise ValueError(
                    f"The business dictionary supports at most {STT_DICTIONARY_MAX_TERMS} terms."
                )
            break
        seen.add(identity)
        normalized.append(term)
    return normalized


# Installers contain only a validated public JSON profile. A source checkout
# without a profile retains .env support for development and existing scripts.
_DEPLOYMENT_PROFILE = paths.current_profile()
_EXPLICIT_PROFILE = paths.has_deployment_profile()
_BUNDLED_ENV_PATH = paths.resource_path(".env")
_BUNDLED_ENV_VALUES = (
    _DEPLOYMENT_PROFILE.environment() if _EXPLICIT_PROFILE
    else dotenv_values(_BUNDLED_ENV_PATH)
)
MANAGED_BUILD = _BUNDLED_ENV_VALUES.get("AI_MODE", "").strip().lower() in {"gateway", "entra"}
if not paths.is_frozen() and not _EXPLICIT_PROFILE:
    load_dotenv(_BUNDLED_ENV_PATH)
else:
    for _key, _value in _BUNDLED_ENV_VALUES.items():
        os.environ.setdefault(_key, _value)
load_dotenv(paths.data_dir() / ".env", override=True)
if _EXPLICIT_PROFILE and not MANAGED_BUILD:
    os.environ["AI_MODE"] = "local"
    for _key in ("AI_GATEWAY_ENDPOINT", "AI_GATEWAY_SCOPE", "AI_CLIENT_ID",
                 "AI_TENANT_ID", "USAGE_SERVICE_ENDPOINT"):
        os.environ.pop(_key, None)

_GATEWAY_LOCKED_ENV_KEYS = [
    "SPEECH_ENDPOINT", "AI_ACCESS_MODE",
    # The public client and authority must match the APIM token policy.
    "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_AUTH_MODE",
    "AI_MODE", "AI_GATEWAY_ENDPOINT", "AI_GATEWAY_SCOPE",
    "AI_CLIENT_ID", "AI_TENANT_ID", "USAGE_SERVICE_ENDPOINT",
    # Local server, capture and retry plumbing are support-controlled. If a key is
    # omitted from the bundle, removing a stale user override restores the safe
    # built-in default below.
    "HOST", "PORT", "POLL_INTERVAL_SECONDS",
    "ATTENDEE_RETRIES", "ATTENDEE_RETRY_DELAY_SECONDS",
    "RECORD_AUDIO", "AUDIO_SAMPLE_RATE", "AUDIO_NORMALIZE",
    "AUDIO_TARGET_PEAK_DBFS", "AUDIO_MAX_GAIN", "AUDIO_ACTIVITY_DBFS",
    "STT_REQUEST_TIMEOUT_SECONDS", "STT_MAX_RETRIES",
    "STT_RETRY_MAX_DELAY_SECONDS", "BACKGROUND_TRANSCRIBE",
    "BACKGROUND_TRANSCRIBE_CHUNK_SECONDS", "BACKGROUND_TRANSCRIBE_POLL_SECONDS",
    # A gateway build may expose only the models/APIs approved in its package.
    "STT_BACKEND", "STT_MODE", "TRANSCRIBE_MODELS", "LIVE_MODELS",
    "SUMMARY_MODELS", "AOAI_API_VERSION", "STT_REALTIME_API_VERSION",
    "STT_REALTIME_DEPLOYMENT", "STT_REALTIME_SAMPLE_RATE", "STT_REALTIME_TRANSLATE_TO",
    "STT_REALTIME_WHISPER_DELAY",
]
_GATEWAY_LOCAL_ONLY_ENV_KEYS = [
    "AOAI_ENDPOINT", "AOAI_API_KEY", "AOAI_ENDPOINT_2", "AOAI_API_KEY_2",
    "AOAI_API_VERSION_2",
]

if MANAGED_BUILD:
    for _key in _GATEWAY_LOCKED_ENV_KEYS:
        _value = _BUNDLED_ENV_VALUES.get(_key)
        if _value is None:
            os.environ.pop(_key, None)
        else:
            os.environ[_key] = _value
    # Never let a key/endpoint left by a previous local installation become an
    # active fallback or appear as a usable connection in a managed bundle.
    for _key in _GATEWAY_LOCAL_ONLY_ENV_KEYS:
        os.environ.pop(_key, None)
    if _BUNDLED_ENV_VALUES.get("AI_MODE") == "entra":
        os.environ["AOAI_ENDPOINT"] = _BUNDLED_ENV_VALUES["AOAI_ENDPOINT"]

# Detection. Lower = the meeting start is caught sooner (less of the opening
# greetings is missed), at the cost of more frequent presence polls.
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "2"))

# Safety net. Teams /me/presence is coarse and laggy, so it can stay set after
# a call truly ends and may never drop to idle between two back-to-back meetings,
# leaving the tool recording forever and never producing a transcript. If a
# recording exceeds this many minutes, force-finalize it so the transcript is
# never lost. 0 disables. Manual Stop in the UI is the primary control; this is
# the backstop.
MAX_RECORDING_MINUTES = float(os.getenv("MAX_RECORDING_MINUTES", "240"))

# Local server (UI <-> engine boundary; later this host:port becomes the Azure URL)
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", str(_DEPLOYMENT_PROFILE.port)))

# Microsoft Graph
GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "common")
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "")
# Auth flow: "device_code" (default) or "interactive".
# "interactive" opens a real browser on this machine (auth-code + PKCE) and can
# satisfy a Conditional Access policy that blocks device-code flow. Requires an
# "http://localhost" redirect URI under the app registration's "Mobile and
# desktop applications" platform.
GRAPH_AUTH_MODE = os.getenv("GRAPH_AUTH_MODE", "device_code").lower()

# AI access mode. "local" preserves the existing direct Azure OpenAI connection
# with customer/company endpoint + keys. "gateway" sends the same OpenAI protocol
# directly to our APIM endpoint and authenticates the signed-in user with Entra;
# APIM replaces that user token with its managed identity for Azure OpenAI.
AI_MODE = os.getenv("AI_MODE", "local").lower()  # "local" | "gateway" | "entra"
SPEECH_ENDPOINT = os.getenv("SPEECH_ENDPOINT", "").rstrip("/")
AI_GATEWAY_ENDPOINT = os.getenv("AI_GATEWAY_ENDPOINT", "").rstrip("/")
AI_GATEWAY_SCOPE = os.getenv("AI_GATEWAY_SCOPE", "").strip()
# Usually the same multi-tenant public desktop app used for Graph. Kept separate
# so an existing customer-owned Graph registration can coexist during migration.
AI_CLIENT_ID = os.getenv("AI_CLIENT_ID", GRAPH_CLIENT_ID).strip()
AI_TENANT_ID = os.getenv("AI_TENANT_ID", GRAPH_TENANT_ID).strip()
# Authenticated metadata-only usage ledger. The endpoint is baked into managed
# packages; local/key-based installations leave it blank and never report usage.
USAGE_SERVICE_ENDPOINT = os.getenv("USAGE_SERVICE_ENDPOINT", "").rstrip("/")

# Attendees: after a meeting ends, look up the Teams attendance report and write
# the participant list into the transcript. Delegated-only, but the attendance
# report needs the OnlineMeetingArtifact.Read.All scope (admin consent) and only
# covers meetings the signed-in user *organized*.
FETCH_ATTENDEES = os.getenv("FETCH_ATTENDEES", "true").lower() == "true"

# Prefer the Teams meeting transcript (named speakers + correct language) when it
# exists — only for meetings you organized with transcription turned on; otherwise
# fall back to batch speech-to-text. Adds the OnlineMeetingTranscript.Read.All scope.
USE_TEAMS_TRANSCRIPT = os.getenv("USE_TEAMS_TRANSCRIPT", "true").lower() == "true"
# The report is generated at/after meeting end, with a short lag — retry a few times.
ATTENDEE_RETRIES = int(os.getenv("ATTENDEE_RETRIES", "3"))
ATTENDEE_RETRY_DELAY_SECONDS = float(os.getenv("ATTENDEE_RETRY_DELAY_SECONDS", "10"))

# Audio capture (Phase A: record meeting audio to a WAV; STT comes later).
# Captures the default mic + the default speaker's WASAPI loopback (remote
# participants), mixed to 16 kHz mono.
RECORD_AUDIO = os.getenv("RECORD_AUDIO", "true").lower() == "true"
AUDIO_DIR = _writable_dir("AUDIO_DIR", "recordings")
AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))  # Azure Speech wants 16 kHz
# Peak-normalize the mix so transcription gets healthy levels (quiet capture is a
# top cause of poor STT). Target peak in dBFS; gain is capped so near-silent
# recordings aren't blown up into noise.
AUDIO_NORMALIZE = os.getenv("AUDIO_NORMALIZE", "true").lower() == "true"
AUDIO_TARGET_PEAK_DBFS = float(os.getenv("AUDIO_TARGET_PEAK_DBFS", "-1"))
AUDIO_MAX_GAIN = float(os.getenv("AUDIO_MAX_GAIN", "30"))
# Prompt-only end detection. If both mic and loopback stay below this RMS level
# for the configured duration during a recording, ask the user whether the call
# ended. Never stops automatically.
AUDIO_SILENCE_PROMPT_SECONDS = float(os.getenv(
    "AUDIO_SILENCE_PROMPT_SECONDS", "120"
))
AUDIO_ACTIVITY_DBFS = float(os.getenv("AUDIO_ACTIVITY_DBFS", "-45"))

# Speech-to-text (Phase B): transcribe the recorded WAV via Azure OpenAI.
# Uses the gpt-4o-transcribe-diarize deployment on the existing OpenAI resource
# (no separate Speech service). Set AOAI_API_KEY in .env (same key as text gen).
STT_BACKEND = os.getenv("STT_BACKEND", "azure_openai")  # "azure_openai" | "none"
STT_REQUEST_TIMEOUT_SECONDS = float(os.getenv(
    "STT_REQUEST_TIMEOUT_SECONDS", "60"
))
STT_MAX_RETRIES = int(os.getenv("STT_MAX_RETRIES", "3"))
STT_RETRY_MAX_DELAY_SECONDS = float(os.getenv(
    "STT_RETRY_MAX_DELAY_SECONDS", "15"
))
# Pre-transcribe closed audio slices while the meeting is still running. The full
# WAV is still written at the end and used as a fallback if any rolling slice fails.
BACKGROUND_TRANSCRIBE = os.getenv("BACKGROUND_TRANSCRIBE", "true").lower() == "true"
BACKGROUND_TRANSCRIBE_CHUNK_SECONDS = float(os.getenv(
    "BACKGROUND_TRANSCRIBE_CHUNK_SECONDS", "600"
))
BACKGROUND_TRANSCRIBE_POLL_SECONDS = float(os.getenv(
    "BACKGROUND_TRANSCRIBE_POLL_SECONDS", "15"
))
AOAI_ENDPOINT = os.getenv("AOAI_ENDPOINT", "")
AOAI_API_KEY = os.getenv("AOAI_API_KEY", "")
# API version that routes /audio/transcriptions for this deployment. The GA
# 2024-10-21 works; the 2025-*-preview strings return DeploymentNotFound here.
AOAI_API_VERSION = os.getenv("AOAI_API_VERSION", "2024-10-21")
# gpt-4o-transcribe handles auto/mixed-language reliably; gpt-4o-transcribe-diarize
# adds speaker labels but mis-detects language on multilingual audio (and is being
# retired) — only choose it when speaker labels matter more than language accuracy.
_LEGACY_STT_DEPLOYMENT = os.getenv("STT_DEPLOYMENT", "")
STT_LANGUAGE = _text_env("STT_LANGUAGE")  # optional ISO hint; "" = auto
# Per-user vocabulary hints. This key deliberately remains user-controlled in a
# managed build; only provider identity, routing, and model selection are locked.
STT_DICTIONARY = normalize_stt_dictionary(os.getenv("STT_DICTIONARY", ""))

# Failover: an optional second Azure OpenAI connection tried automatically when the
# primary is unavailable. It is a pure backup CONNECTION — no separate model list.
# Any model in a capability list (below) is attempted on the primary connection first
# and, if that's unavailable (outage or the model isn't deployed there), on the
# failover. Endpoints may host different deployments; the model just resolves to
# whichever connection has it.
AOAI_ENDPOINT_2 = os.getenv("AOAI_ENDPOINT_2", "")
AOAI_API_KEY_2 = os.getenv("AOAI_API_KEY_2", "")
AOAI_API_VERSION_2 = _text_env("AOAI_API_VERSION_2")  # "" = reuse primary


# --------------------------------------------------------------------------- #
# Model lists — one ordered list per capability. The FIRST entry is the default/
# active model; the rest are fallbacks. Connection is chosen automatically (primary
# preferred, failover if the primary can't serve the model) — models list plain
# deployment names, never a connection. Live (realtime) uses the primary connection
# only (the realtime API has no failover).
# --------------------------------------------------------------------------- #
# Superseded per-resource keys, still read so existing .env files keep working.
LEGACY_MODEL_KEYS = [
    "STT_ENGINES", "STT_ENGINES_2", "STT_DEFAULT_ENGINE", "STT_DEPLOYMENT_2",
    "STT_REALTIME_ENGINES", "STT_REALTIME_DEFAULT_ENGINE",
    "SUMMARY_DEPLOYMENT", "SUMMARY_DEPLOYMENT_2",
]


def _models_env(new_key: str, *legacy_keys: str, default: str = "") -> list[str]:
    """Ordered model list for a capability. Prefers the new *_MODELS key; otherwise
    merges the legacy per-resource keys (order-preserving, de-duplicated) so old .env
    files still work. Falls back to `default` when nothing is configured."""
    raw = os.getenv(new_key)
    if raw and raw.strip():
        return [m.strip() for m in raw.split(",") if m.strip()]
    merged: list[str] = []
    for key in legacy_keys:
        for m in (os.getenv(key, "") or "").split(","):
            m = m.strip()
            if m and m not in merged:
                merged.append(m)
    return merged or ([default] if default else [])


TRANSCRIBE_MODELS = _models_env(
    "TRANSCRIBE_MODELS", "STT_DEFAULT_ENGINE", "STT_ENGINES", "STT_ENGINES_2",
    "STT_DEPLOYMENT", "STT_DEPLOYMENT_2", default="gpt-4o-transcribe")
LIVE_MODELS = _models_env(
    "LIVE_MODELS", "STT_REALTIME_DEFAULT_ENGINE", "STT_REALTIME_ENGINES",
    default="gpt-4o-transcribe")
SUMMARY_MODELS = _models_env(
    "SUMMARY_MODELS", "SUMMARY_DEPLOYMENT", "SUMMARY_DEPLOYMENT_2",
    default="gpt-4o")

# Transcription mode:
#   batch    = transcribe the recorded WAV after the meeting (diarized).
#   realtime = stream audio live to the realtime API for a live popup transcript
#              (no speaker labels), then still run the batch diarized pass at the
#              end for the final saved file (hybrid). Needs a realtime deployment
#              (gpt-4o-transcribe — NOT the diarize model).
STT_MODE = os.getenv("STT_MODE", "batch")  # "batch" | "realtime"
STT_REALTIME_DEPLOYMENT = os.getenv("STT_REALTIME_DEPLOYMENT", "gpt-4o-transcribe")  # legacy default
STT_REALTIME_API_VERSION = os.getenv("STT_REALTIME_API_VERSION", "2025-04-01-preview")
STT_REALTIME_SAMPLE_RATE = int(os.getenv("STT_REALTIME_SAMPLE_RATE", "24000"))
# Optional fallback target language for gpt-realtime-translate when the UI
# Translate dropdown is Off. Blank means there is no default translation target.
STT_REALTIME_TRANSLATE_TO = os.getenv("STT_REALTIME_TRANSLATE_TO", "").strip()
# gpt-realtime-whisper accepts a transcription delay trade-off (accuracy vs latency):
# minimal | low | medium | high | xhigh. Blank = model default. Ignored by other models.
STT_REALTIME_WHISPER_DELAY = os.getenv("STT_REALTIME_WHISPER_DELAY", "").strip()

# Summary (post-transcription): condense the transcript with a deployed chat model
# on the same resource. Optional. SUMMARY_LANGUAGE is the output language.
SUMMARIZE = os.getenv("SUMMARIZE", "true").lower() == "true"
SUMMARY_LANGUAGE = os.getenv("SUMMARY_LANGUAGE", "de")

# Clean up a plain (no-speaker) batch transcript with the chat model before saving:
# replace unintelligible/hallucinated passages with <unintelligible> and best-effort
# attribute lines to speakers from context. No effect on Teams/diarize transcripts.
CLEAN_TRANSCRIPT = os.getenv("CLEAN_TRANSCRIPT", "true").lower() == "true"

# Output
TRANSCRIPT_DIR = _writable_dir("TRANSCRIPT_DIR", "transcripts")

# OneNote: product-level feature gate plus opt-in "save to OneNote" action for
# finished transcripts. Uses the SAME Microsoft sign-in as Graph (OneNote is a
# Graph workload) and requests delegated Notes.ReadWrite when used.
ONENOTE_ENABLED = os.getenv("ONENOTE_ENABLED", "false").lower() == "true"
ONENOTE_NOTEBOOK = os.getenv("ONENOTE_NOTEBOOK", "Voice Transcriber")
ONENOTE_SECTION = os.getenv("ONENOTE_SECTION", "Transcripts")
ONENOTE_SITE_PATHS = _csv_env(
    "ONENOTE_SITE_PATHS",
    os.getenv("SHAREPOINT_SITE", ""),
)


# Presence activities that count as a live call/meeting trigger. Do not include
# "InAMeeting": Graph may set it from a current calendar event even when the
# user is not actually in a Teams call.
ACTIVE_ACTIVITIES = {"InACall", "InAConferenceCall", "Presenting"}

# Our own mail domains, so colleagues are never mistaken for the customer when
# guessing which record a meeting belongs to. Empty means "use the signed-in
# user's own domain", which is right for every single-tenant install; set this
# only when the company sends mail from more than one domain.
OWN_EMAIL_DOMAINS = _csv_env("OWN_EMAIL_DOMAINS")


# --------------------------------------------------------------------------- #
# Live reload (in-app Settings window writes the per-user .env, then calls this)
# --------------------------------------------------------------------------- #
# Env keys the Settings window manages. Cleared from the environment before a reload
# so a value the user removed from the .env reverts to its built-in default rather
# than lingering. HOST/PORT are managed too but only take effect on restart (the
# local web server is already bound).
MANAGED_ENV_KEYS = [
    "SPEECH_ENDPOINT", "AI_ACCESS_MODE",
    "POLL_INTERVAL_SECONDS", "MAX_RECORDING_MINUTES", "HOST", "PORT",
    "GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_AUTH_MODE",
    "AI_MODE", "AI_GATEWAY_ENDPOINT", "AI_GATEWAY_SCOPE",
    "AI_CLIENT_ID", "AI_TENANT_ID", "USAGE_SERVICE_ENDPOINT",
    "FETCH_ATTENDEES", "USE_TEAMS_TRANSCRIPT", "ATTENDEE_RETRIES",
    "ATTENDEE_RETRY_DELAY_SECONDS", "RECORD_AUDIO", "AUDIO_DIR", "AUDIO_SAMPLE_RATE",
    "AUDIO_NORMALIZE", "AUDIO_TARGET_PEAK_DBFS", "AUDIO_MAX_GAIN",
    "AUDIO_SILENCE_PROMPT_SECONDS", "AUDIO_ACTIVITY_DBFS", "STT_BACKEND",
    "STT_REQUEST_TIMEOUT_SECONDS", "STT_MAX_RETRIES",
    "STT_RETRY_MAX_DELAY_SECONDS",
    "AOAI_ENDPOINT", "AOAI_API_KEY", "AOAI_API_VERSION", "STT_LANGUAGE",
    "STT_DICTIONARY",
    "AOAI_ENDPOINT_2", "AOAI_API_KEY_2", "AOAI_API_VERSION_2",
    "TRANSCRIBE_MODELS", "LIVE_MODELS", "SUMMARY_MODELS", "STT_MODE",
    "STT_REALTIME_DEPLOYMENT", "STT_REALTIME_API_VERSION", "STT_REALTIME_SAMPLE_RATE",
    "STT_REALTIME_TRANSLATE_TO", "STT_REALTIME_WHISPER_DELAY",
    "SUMMARIZE", "SUMMARY_LANGUAGE", "CLEAN_TRANSCRIPT", "TRANSCRIPT_DIR",
    "ONENOTE_ENABLED", "ONENOTE_NOTEBOOK", "ONENOTE_SECTION",
    "ONENOTE_SITE_PATHS",
]

# Keys that only take effect after a restart (server already bound to them).
RESTART_ONLY_KEYS = {"HOST", "PORT"}


def reload() -> None:
    """Re-read all settings after the per-user .env was rewritten by the Settings
    window, so changes apply without restarting. Safe because every consumer reads
    config.<NAME> live (no value is captured at import elsewhere). Legacy model keys
    are cleared too so a lingering old value can't shadow a new *_MODELS list."""
    for key in MANAGED_ENV_KEYS + LEGACY_MODEL_KEYS:
        os.environ.pop(key, None)
    importlib.reload(sys.modules[__name__])


# --------------------------------------------------------------------------- #
# Azure OpenAI providers (with failover to the second resource)
# Each provider is a dict: {endpoint, key, deployment, api_version}. Callers try
# them in order and move to the next on failure.
# --------------------------------------------------------------------------- #
def _connections() -> list[dict]:
    """Azure OpenAI connections in preference order: primary, then failover if set."""
    if AI_MODE == "entra":
        return [{"endpoint": AOAI_ENDPOINT, "key": "", "auth": "entra-direct",
                 "api_version": AOAI_API_VERSION}]
    if AI_MODE == "gateway":
        return [{
            "endpoint": AI_GATEWAY_ENDPOINT,
            "key": "",
            "auth": "entra",
            "api_version": AOAI_API_VERSION,
        }]
    conns = [{"endpoint": AOAI_ENDPOINT, "key": AOAI_API_KEY, "api_version": AOAI_API_VERSION}]
    if AOAI_ENDPOINT_2 and AOAI_API_KEY_2:
        conns.append({"endpoint": AOAI_ENDPOINT_2, "key": AOAI_API_KEY_2,
                      "api_version": AOAI_API_VERSION_2 or AOAI_API_VERSION})
    return conns


def _providers_for(models: list[str], active: str = "") -> list[dict]:
    """Expand an ordered model list into providers. For each model, try the primary
    connection first, then the failover — so a model deployed only on the failover, or
    a primary outage, resolves automatically. `active` (the chosen default) is moved to
    the front; since this is rebuilt per call, every fresh recording starts primary-first
    again and a prior failover never sticks."""
    ordered = list(models)
    if active and active in ordered:
        ordered = [active] + [m for m in ordered if m != active]
    conns = _connections()
    providers = [{"endpoint": c["endpoint"], "key": c["key"],
                  "auth": c.get("auth", "key"), "deployment": m,
                  "api_version": c["api_version"]} for m in ordered for c in conns]
    fallback = conns[0] if conns else {
        "endpoint": AOAI_ENDPOINT, "key": AOAI_API_KEY,
        "auth": "key", "api_version": AOAI_API_VERSION,
    }
    return providers or [{**fallback,
                          "deployment": active or "gpt-4o-transcribe"}]


# ---- batch speech-to-text ----
def default_stt_engine() -> str:
    return TRANSCRIBE_MODELS[0] if TRANSCRIBE_MODELS else "gpt-4o-transcribe"


def stt_engine_options() -> list[dict]:
    """One entry per transcription model (primary connection) — for UI/CLI listing."""
    primary = _connections()[0]
    return [{"endpoint": primary["endpoint"], "key": primary["key"],
             "auth": primary.get("auth", "key"), "deployment": m,
             "api_version": primary["api_version"]} for m in TRANSCRIBE_MODELS]


def stt_providers_for(active_deployment: str) -> list[dict]:
    """Providers for a batch transcription: the active model first, all models tried
    primary-then-failover."""
    return _providers_for(TRANSCRIBE_MODELS, active_deployment)


def providers_for_model(deployment: str) -> list[dict]:
    """Pin one model while retaining connection failover in local mode."""
    return _providers_for([deployment], deployment)


def stt_providers() -> list[dict]:
    return stt_providers_for(default_stt_engine())


# ---- live (realtime) transcription — primary connection only ----
def realtime_engine_options() -> list[str]:
    """Selectable live models (order-preserving)."""
    return list(LIVE_MODELS)


def default_realtime_engine() -> str:
    return LIVE_MODELS[0] if LIVE_MODELS else "gpt-4o-transcribe"


def realtime_engine_kind(deployment: str) -> str:
    """'translate' for the speech-to-translated-text model (emits a target-language
    transcript), else 'transcribe' (source-language transcript)."""
    return "translate" if "translate" in (deployment or "").lower() else "transcribe"


def realtime_endpoint() -> str:
    """Endpoint for realtime traffic in the selected AI access mode."""
    return AI_GATEWAY_ENDPOINT if AI_MODE == "gateway" else AOAI_ENDPOINT


# ---- chat model (summary / cleanup / translate) ----
def chat_providers() -> list[dict]:
    """Providers for the chat model, primary-then-failover per model."""
    return _providers_for(SUMMARY_MODELS)
