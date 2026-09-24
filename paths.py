"""Filesystem locations that work both in dev and as a bundled (PyInstaller) app.

- resource_path(): read-only bundled assets (frontend/dist, favicon.ico, the public
  deployment profile). In a PyInstaller build these live under sys._MEIPASS; in dev they sit next
  to the source, so the same relative layout resolves in both.
- data_dir(): a per-user *writable* directory (%LOCALAPPDATA%\\MeetingTranscriber)
  for the token cache, recordings, transcripts and logs — the install directory
  (e.g. Program Files) is read-only, so runtime state must not live next to the exe.
"""
import os
import sys
from pathlib import Path

from deployment_profile import DeploymentProfile, PROFILE_FILENAME, ProfileError, load_profile

APP_NAME = "MeetingTranscriber"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def resource_path(*parts: str) -> Path:
    """Path to a bundled, read-only resource (works in dev and when frozen)."""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def has_deployment_profile() -> bool:
    return resource_path(PROFILE_FILENAME).is_file()


def current_profile() -> DeploymentProfile:
    profile_path = resource_path(PROFILE_FILENAME)
    if profile_path.is_file():
        return load_profile(profile_path)
    if is_frozen():
        raise ProfileError("Packaged application requires a deployment profile")
    return DeploymentProfile("community", {})


def data_dir() -> Path:
    """Per-user writable directory; created on first access."""
    # Isolate only our state: MSAL's external browser inherits LOCALAPPDATA.
    override = os.getenv("VOICE_TRANSCRIBER_DATA_ROOT")
    if override and not Path(override).is_absolute():
        raise ValueError("VOICE_TRANSCRIBER_DATA_ROOT must be an absolute path")
    root = override or os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    d = Path(root) / APP_NAME / current_profile().data_namespace
    d.mkdir(parents=True, exist_ok=True)
    return d
