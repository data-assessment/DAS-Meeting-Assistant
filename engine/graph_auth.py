"""MSAL auth for Microsoft Graph, with on-disk token cache.

Two flows, selected by config.GRAPH_AUTH_MODE:

- "device_code" (default): the first call prints a device-login URL + code to
  the console.
- "interactive": the first call opens a real browser on this machine
  (auth-code + PKCE). This can satisfy a Conditional Access policy that blocks
  device-code flow. Requires an "http://localhost" redirect URI under the app
  registration's "Mobile and desktop applications" platform.

Subsequent calls use the cached/refreshed token in either mode.
"""
import datetime
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import msal

import config
import paths

def _scopes(
    include_onenote: bool = False,
    include_sharepoint_sites: bool = False,
) -> list[str]:
    """Build the scope list from the current config and explicit caller needs.
    Presence is always needed; the rest are added only when configured or requested.
    All are user-consentable except OnlineMeetingTranscript.Read.All (admin consent)."""
    scopes = ["Presence.Read"]
    # Attendees + Teams transcripts resolve the meeting from the user's calendar.
    if config.FETCH_ATTENDEES or config.USE_TEAMS_TRANSCRIPT:
        scopes += ["Calendars.Read", "OnlineMeetings.Read"]
    if config.FETCH_ATTENDEES:
        scopes += ["OnlineMeetingArtifact.Read.All"]
    if config.USE_TEAMS_TRANSCRIPT:
        scopes += ["OnlineMeetingTranscript.Read.All"]
    # OneNote uses the same sign-in (a Graph workload), but only callers that
    # are opening or publishing OneNote pages request the page-write scope.
    if include_onenote and config.ONENOTE_ENABLED:
        scopes += ["Notes.ReadWrite"]
        if include_sharepoint_sites and config.ONENOTE_SITE_PATHS:
            scopes += ["Sites.Read.All"]
    return scopes


_CACHE_PATH = str(paths.data_dir() / "token_cache.bin")

# Upper bound on how long an interactive sign-in may block its caller. Generous
# enough for MFA, short enough that an abandoned browser flow fails with a
# message instead of wedging the dialog that is waiting on it.
_INTERACTIVE_TIMEOUT = 300

# MSAL builds its own requests session with no timeout at all, so a stalled
# connection to login.microsoftonline.com (metadata discovery or a silent
# token refresh) blocks the caller indefinitely. Every dialog that loads data
# sits behind one of these calls, so they must be bounded.
_HTTP_TIMEOUT = 30


@contextmanager
def _cache_write_lock(target: Path):
    """Serialize the final cache replacement across processes."""
    lock_path = target.with_name(f".{target.name}.lock")
    with open(lock_path, "a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + 10
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"timed out locking Microsoft token cache {target.name}"
                        )
                    time.sleep(0.02)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - Windows is the packaged desktop target
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _preserve_invalid_cache(cache_path: str, serialized: str, exc: Exception) -> None:
    source = Path(cache_path)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S%f")
    backup = source.with_name(
        f"{source.name}.corrupt-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )
    try:
        with _cache_write_lock(source):
            try:
                current = source.read_text(encoding="utf-8")
            except FileNotFoundError:
                detail = "; another process already removed it"
            else:
                if current != serialized:
                    detail = "; another process already replaced it"
                else:
                    os.replace(source, backup)
                    detail = f"; moved to {backup.name}"
    except OSError as move_exc:
        detail = f"; could not preserve it: {move_exc}"
    print(f"Microsoft token cache was invalid and will be rebuilt ({exc}){detail}")


def _load_cache(cache_path: str = _CACHE_PATH) -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as stream:
            serialized = stream.read()
        try:
            cache.deserialize(serialized)
        except Exception as exc:
            # A killed or duplicated older app process could leave two partial JSON
            # writes in this file. Preserve it for diagnosis, but do not make a bad
            # cache prevent the user from signing in again.
            _preserve_invalid_cache(cache_path, serialized, exc)
    return cache


def _save_cache(cache: msal.SerializableTokenCache,
                cache_path: str = _CACHE_PATH) -> None:
    if cache.has_state_changed:
        target = Path(cache_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        stream = None
        try:
            stream = os.fdopen(descriptor, "w", encoding="utf-8")
            descriptor = -1
            with stream:
                stream.write(cache.serialize())
                stream.flush()
                os.fsync(stream.fileno())
            stream = None
            # Same-directory replacement is atomic: readers see either the complete
            # old cache or the complete new cache, never interleaved JSON.
            with _cache_write_lock(target):
                os.replace(temporary, target)
        finally:
            if stream is not None:
                stream.close()
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _acquire_token(
    scopes: list[str],
    interactive: bool,
    label: str,
    client_id: str,
    tenant_id: str,
    cache_path: str = _CACHE_PATH,
    *, full_result: bool = False,
) -> str | dict | None:
    if not client_id:
        raise RuntimeError(f"Client ID is not set (required for {label}).")

    cache = _load_cache(cache_path)
    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
        timeout=_HTTP_TIMEOUT,
    )

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])

    if not result:
        if not interactive:
            return None
        if config.GRAPH_AUTH_MODE == "interactive":
            print(f"\n=== {label} login ===")
            print("Opening a browser to sign in…")
            # Without a timeout MSAL waits forever for a redirect that never
            # arrives when the user closes or never notices the browser, and
            # the caller (a dialog waiting on this request) hangs with it.
            result = app.acquire_token_interactive(
                scopes, timeout=_INTERACTIVE_TIMEOUT
            )
        else:
            flow = app.initiate_device_flow(scopes=scopes)
            if "user_code" not in flow:
                raise RuntimeError(f"Failed to start device flow: {flow}")
            print(f"\n=== {label} login ===")
            print(flow["message"])
            result = app.acquire_token_by_device_flow(flow)

    _save_cache(cache, cache_path)
    if "access_token" not in result:
        raise RuntimeError(f"Auth failed: {result.get('error_description', result)}")
    return result if full_result else result["access_token"]


def signed_in_username() -> str:
    """The signed-in account's address, read from the local token cache.

    Offline and side-effect free: used to tell our own people from the
    customer's when guessing an export target.
    """
    try:
        cache = _load_cache(_CACHE_PATH)
        app = msal.PublicClientApplication(
            config.GRAPH_CLIENT_ID or "unset",
            authority=(
                "https://login.microsoftonline.com/"
                f"{config.GRAPH_TENANT_ID}"
            ),
            token_cache=cache,
            timeout=_HTTP_TIMEOUT,
            instance_discovery=False,
        )
        for account in app.get_accounts():
            username = str(account.get("username") or "").strip()
            if "@" in username:
                return username
    except Exception as exc:
        print("could not read the signed-in account:", exc)
    return ""


def get_token(
    interactive: bool = True,
    include_onenote: bool = False,
    include_sharepoint_sites: bool = False,
) -> str | None:
    """Return an access token. With interactive=False, only the silent path is
    tried (cached/refreshed) and None is returned if a user sign-in is needed —
    so presence polling never silently pops a browser. The explicit "Sign in"
    action calls this with interactive=True."""
    if not config.GRAPH_CLIENT_ID:
        raise RuntimeError("GRAPH_CLIENT_ID is not set (required for Graph presence).")

    return _acquire_token(
        _scopes(include_onenote, include_sharepoint_sites),
        interactive,
        "Microsoft Graph",
        config.GRAPH_CLIENT_ID,
        config.GRAPH_TENANT_ID,
    )


def get_token_for_scopes(
    scopes: list[str],
    interactive: bool = True,
    label: str = "Microsoft",
    client_id: str | None = None,
    tenant_id: str | None = None,
) -> str | None:
    """Acquire a delegated token for a non-Graph API with the same user flow."""
    selected_client = client_id or config.GRAPH_CLIENT_ID
    selected_tenant = tenant_id or config.GRAPH_TENANT_ID
    cache_path = _CACHE_PATH
    if selected_client != config.GRAPH_CLIENT_ID:
        cache_path = str(paths.data_dir() / "ai_token_cache.bin")
    return _acquire_token(
        scopes, interactive, label, selected_client, selected_tenant, cache_path
    )


def get_access_token_for_scopes(scopes, interactive=False):
    """Azure SDK token including its real remaining lifetime, using our user cache."""
    import time
    from azure.core.credentials import AccessToken

    client = config.AI_CLIENT_ID or config.GRAPH_CLIENT_ID
    tenant = config.AI_TENANT_ID or config.GRAPH_TENANT_ID
    cache_path = (_CACHE_PATH if client == config.GRAPH_CLIENT_ID
                  else str(paths.data_dir() / "ai_token_cache.bin"))
    # Measuring from before acquisition underestimates rather than extends expiry.
    started = int(time.time())
    result = _acquire_token(scopes, interactive, "DAS Azure", client, tenant,
                            cache_path, full_result=True)
    if not result:
        raise RuntimeError("Bitte in den Einstellungen mit Ihrem DAS-Firmenkonto anmelden.")
    expires = min(int(result.get("expires_on", started + int(result["expires_in"]))),
                  started + int(result["expires_in"]))
    if expires <= time.time() + 30:
        raise RuntimeError("DAS-Anmeldung abgelaufen. Bitte erneut anmelden.")
    return AccessToken(result["access_token"], expires)
