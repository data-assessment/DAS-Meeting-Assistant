"""Snapshot dependency notices; verify and include them in each offline build."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
THIRD = ROOT / "third_party"
DOCUMENT = re.compile(r"licen[sc]e|copying|copyright|notice|^redist", re.I)
FORBIDDEN = {"av", "aiortc", "aioice", "pylibsrtp", "PyInstaller",
             "_pyinstaller_hooks_contrib", "pytest", "_pytest", "setuptools"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_packages():
    return dict((canonical(name), version) for name, version in re.findall(
        r"^([\w.-]+)(?:\[[^]]+\])?==([^\s]+)",
        (ROOT / "requirements.txt").read_text(), re.M))


def runtime_npm():
    lock = json.loads((ROOT / "frontend/package-lock.json").read_text())
    return {path: item for path, item in lock["packages"].items()
            if path and not item.get("dev")}


def snapshot():
    """No downloads. Sources and supplemental upstream notices are reviewed inputs."""
    packages = []
    for name, version in sorted(locked_packages().items()):
        dist = metadata.distribution(name)
        if dist.version != version:
            raise ValueError(f"Install the pinned dependencies first: {name}")
        files = []
        for item in dist.files or []:
            if DOCUMENT.search(item.name) and not item.name.endswith((".py", ".pyc", ".pyd")):
                source = Path(dist.locate_file(item))
                relative = Path("licenses/python") / name / str(item).replace("\\", "/")
                if not source.is_file():
                    continue
                target = THIRD / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                files.append(relative.as_posix())
        if name == "proxy-tools":
            files.append("upstream/proxy-tools/LICENSE.txt")
        if not files:
            raise ValueError(f"Missing original license document: {name}")
        license_name = dist.metadata.get("License-Expression") or dist.metadata.get("License", "See original text")
        if len(license_name) > 150:
            license_name = "See original text"
        if name == "proxy-tools":
            license_name = "BSD-3-Clause (upstream text; metadata says MIT)"
        packages.append({"ecosystem": "python", "name": name, "version": version,
                         "license_metadata": license_name, "documents": sorted(files)})
    for path, info in sorted(runtime_npm().items()):
        package_dir = ROOT / "frontend" / path
        package = json.loads((package_dir / "package.json").read_text())
        if package["version"] != info["version"]:
            raise ValueError(f"Run npm ci first: {path}")
        files = []
        for source in package_dir.iterdir():
            if source.is_file() and DOCUMENT.search(source.name):
                relative = Path("licenses/npm") / package["name"] / source.name
                target = THIRD / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                files.append(relative.as_posix())
        if not files:
            raise ValueError(f"Missing npm license document: {path}")
        packages.append({"ecosystem": "npm", "name": package["name"], "version": info["version"],
                         "license_metadata": info.get("license", "See original text"), "documents": files})
    for name, source in [
        ("python/LICENSE.txt", Path(sys.base_prefix) / "LICENSE.txt"),
        ("pyinstaller/COPYING.txt", next(Path(metadata.distribution("pyinstaller").locate_file(f))
                                        for f in metadata.files("pyinstaller") if f.name == "COPYING.txt")),
    ]:
        target = THIRD / "licenses" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    lines = ["# Runtime dependency documents", "",
             "Versions are pinned. License labels below are upstream metadata, not a legal",
             "classification of every embedded component; the linked original texts control.", "",
             "| Package | Version | Upstream license label | Original documents |",
             "|---|---|---|---|"]
    for package in packages:
        label = package["license_metadata"].replace("|", "\\|").replace("\n", " ")
        folder = Path(package["documents"][0]).parent.as_posix()
        lines.append(f"| {package['ecosystem']}: {package['name']} | {package['version']} | {label} | [Documents]({folder}) |")
    lines += ["", "Supplemental native and Rust notices are indexed in `upstream-provenance.json`",
              "and `rust-manifest.json`. Python, PyInstaller and Inno Setup notices are in `licenses/`.", ""]
    (THIRD / "COMPONENTS.md").write_text("\n".join(lines), encoding="utf-8")
    manifest = {"python_version": sys.version.split()[0],
                "pyinstaller_version": metadata.version("pyinstaller"),
                "packages": packages,
                "files": {p.relative_to(THIRD).as_posix(): digest(p)
                          for p in sorted(THIRD.rglob("*")) if p.is_file() and p.name != "manifest.json"}}
    (THIRD / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Collected {len(packages)} package notices and {len(manifest['files'])} documents/source archives.")


def verify():
    manifest = json.loads((THIRD / "manifest.json").read_text())
    if manifest["python_version"] != sys.version.split()[0]:
        raise ValueError(
            f"Python runtime mismatch: expected {manifest['python_version']}, "
            f"found {sys.version.split()[0]}. Create a fresh venv with the public "
            "Windows x64 Python in packaging/README.md and pass its python.exe "
            "with -PythonExe. Refresh notices only for an intentional runtime update."
        )
    if manifest["pyinstaller_version"] != metadata.version("pyinstaller"):
        raise ValueError("PyInstaller changed: refresh the bootloader notices.")
    packages = {p["name"]: p["version"] for p in manifest["packages"] if p["ecosystem"] == "python"}
    if packages != locked_packages():
        raise ValueError("Python dependencies changed: refresh third_party/manifest.json.")
    for name, version in packages.items():
        if metadata.version(name) != version:
            raise ValueError(f"Installed package differs from the documented version: {name}")
    npm_versions = {p["name"]: p["version"] for p in manifest["packages"] if p["ecosystem"] == "npm"}
    if npm_versions != {p.removeprefix("node_modules/"): v["version"] for p, v in runtime_npm().items()}:
        raise ValueError("Frontend dependencies changed: refresh the notices.")
    for relative, expected in manifest["files"].items():
        path = THIRD / relative
        if not path.resolve().is_relative_to(THIRD.resolve()) or not path.is_file() or digest(path) != expected:
            raise ValueError(f"Missing or changed third-party document: {relative}")
    return manifest


def bundle(directory, analysis):
    manifest = verify()
    toc = ast.literal_eval(analysis.read_text(encoding="utf-8"))
    modules = [item[0] for item in toc[14]]
    bad = sorted(set(m.split(".")[0] for m in modules) & FORBIDDEN)
    if bad:
        raise ValueError(f"Removed or build-only modules in the application: {bad}")
    native = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".dll", ".pyd"}:
            relative = path.relative_to(directory).as_posix()
            if re.search(r"(?:avcodec|avformat|avutil|swresample|swscale|x264|x265|dbghelp|dbgcore|libglib|libgobject)", path.name, re.I):
                raise ValueError(f"Unexpected native dependency: {relative}")
            native.append({"path": relative, "sha256": digest(path)})
    if not (directory / "_internal/pystray/__init__.py").is_file():
        raise ValueError("The LGPL pystray source must remain externally replaceable.")
    for name in ("LICENSE", "NOTICE", "SOURCE.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(ROOT / name, directory / name)
    (directory / "docs").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "docs/open-source-release.md", directory / "docs/open-source-release.md")
    shutil.copytree(THIRD, directory / "third_party", dirs_exist_ok=True)
    inventory = {"python": manifest["python_version"], "native_files": native,
                 "python_modules": sorted(modules),
                 "notice_manifest_sha256": digest(THIRD / "manifest.json")}
    (directory / "component-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    print(f"Verified notices; {len(native)} native files; no removed/build-only modules.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--analysis", type=Path)
    args = parser.parse_args()
    if args.refresh:
        snapshot()
    elif args.bundle:
        if not args.analysis:
            parser.error("--bundle requires --analysis")
        bundle(args.bundle, args.analysis)
    else:
        verify()
        print("Dependency documents verified.")
