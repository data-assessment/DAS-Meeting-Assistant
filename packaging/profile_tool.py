"""Validate build inputs and audit the unpacked application before distribution."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployment_profile import load_profile, PROFILE_FILENAME, ProfileError

def audit_artifact(directory: Path, profile):
    bundled = directory / "_internal" / PROFILE_FILENAME
    if load_profile(bundled).document() != profile.document():
        raise ProfileError("Bundled profile does not match the selected build profile")
    forbidden = {".env", "bundle.env", "signing.env", "token_cache.bin",
                 "server.py", "usage_store.py"}
    for path in directory.rglob("*"):
        if path.is_file() and (path.name.lower() in forbidden
                              or path.suffix.lower() in {".env", ".pfx", ".p12", ".key", ".wav", ".mp3"}):
            raise ProfileError("Forbidden configuration, credential or raw-data file in build artifact")
        if path.is_file() and path.suffix.lower() == ".pem":
            if b"PRIVATE KEY-----" in path.read_bytes():
                raise ProfileError("Private key material in build artifact")
    if not (directory / (profile.executable_name + ".exe")).is_file():
        raise ProfileError("Expected executable missing from build artifact")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    try:
        profile = load_profile(args.profile)
        if args.stage:
            args.stage.parent.mkdir(parents=True, exist_ok=True)
            args.stage.write_text(json.dumps(profile.document(), indent=2) + "\n", encoding="utf-8")
        if args.audit:
            audit_artifact(args.audit, profile)
            commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
            dirty = bool(subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True))
            exe = args.audit / (profile.executable_name + ".exe")
            manifest = {"source_commit": commit, "source_dirty": dirty,
                        "features": ["isolated-app-data-root", "owned-local-ui", "ui-http-smoke-test"],
                        "profile": profile.mode,
                        "profile_sha256": hashlib.sha256((args.audit / "_internal" / PROFILE_FILENAME).read_bytes()).hexdigest(),
                        "executable_sha256": hashlib.sha256(exe.read_bytes()).hexdigest()}
            (args.audit / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(profile.mode)
        return 0
    except ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
