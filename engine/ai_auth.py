"""Authentication and client construction for local and managed AI access."""
from __future__ import annotations

import config
import threading

AZURE_SCOPE = "https://cognitiveservices.azure.com/.default"
_azure_lock = threading.Lock()


def azure_access_token(interactive=False):
    if config.AI_MODE != "entra":
        raise RuntimeError("Direct Azure sign-in is not configured")
    from engine.graph_auth import get_access_token_for_scopes
    with _azure_lock:
        try:
            return get_access_token_for_scopes([AZURE_SCOPE], interactive=interactive)
        except Exception:
            raise RuntimeError("DAS-Azure-Anmeldung fehlt oder ist abgelaufen. Bitte in den Einstellungen mit Ihrem Firmenkonto anmelden.") from None


def azure_token(interactive=False):
    return azure_access_token(interactive).token


def cloud_token(interactive=False):
    return (azure_token if config.AI_MODE == "entra" else gateway_token)(interactive=interactive)


class UserTokenCredential:
    """Speech SDK refreshes tokens silently; only the explicit sign-in opens a browser."""
    def get_token(self, *scopes, **kwargs):
        if scopes != (AZURE_SCOPE,):
            raise ValueError("Unexpected Azure token scope")
        return azure_access_token(interactive=False)


def available() -> tuple[bool, str | None]:
    if config.AI_MODE == "entra":
        for name in ("AOAI_ENDPOINT", "SPEECH_ENDPOINT", "AI_CLIENT_ID"):
            if not getattr(config, name):
                return False, f"{name} not set"
        return True, None
    if config.AI_MODE == "local":
        if not config.AOAI_ENDPOINT:
            return False, "AOAI_ENDPOINT not set"
        if not config.AOAI_API_KEY:
            return False, "AOAI_API_KEY not set"
        return True, None
    if config.AI_MODE != "gateway":
        return False, f"unsupported AI_MODE={config.AI_MODE}"
    if not config.AI_GATEWAY_ENDPOINT:
        return False, "AI_GATEWAY_ENDPOINT not set"
    if not config.AI_GATEWAY_SCOPE:
        return False, "AI_GATEWAY_SCOPE not set"
    if not config.AI_CLIENT_ID:
        return False, "AI_CLIENT_ID not set"
    return True, None


def gateway_token(interactive: bool = False) -> str:
    """Return the signed-in user's delegated token for the AI gateway."""
    if config.AI_MODE != "gateway":
        raise RuntimeError("AI gateway token requested while AI_MODE is not gateway")
    from engine.graph_auth import get_token_for_scopes

    token = get_token_for_scopes(
        [config.AI_GATEWAY_SCOPE],
        interactive=interactive,
        label="DAS Meeting Assistant cloud",
        client_id=config.AI_CLIENT_ID,
        tenant_id=config.AI_TENANT_ID,
    )
    if not token:
        raise RuntimeError(
            "DAS Meeting Assistant cloud sign-in is required. Open Settings and sign in."
        )
    return token


def openai_auth_kwargs(provider: dict) -> dict:
    """Authentication kwargs accepted by openai.AzureOpenAI."""
    if provider.get("auth") == "entra-direct":
        return {"azure_ad_token_provider": azure_token}
    if provider.get("auth") == "entra":
        return {"azure_ad_token_provider": gateway_token}
    return {"api_key": provider.get("key", "")}


def websocket_headers() -> dict[str, str]:
    if config.AI_MODE == "entra":
        return {"Authorization": f"Bearer {azure_token(interactive=False)}"}
    if config.AI_MODE == "gateway":
        return {"Authorization": f"Bearer {gateway_token(interactive=False)}"}
    return {"api-key": config.AOAI_API_KEY}
