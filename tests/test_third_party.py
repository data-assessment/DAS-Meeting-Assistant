import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def notices(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "release_notices", Path(__file__).resolve().parents[1] / "packaging/third_party.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "THIRD", tmp_path / "third_party")
    module.THIRD.mkdir()
    document = module.THIRD / "LICENSE"
    document.write_text("Original notice")
    manifest = {"python_version": module.sys.version.split()[0],
                "pyinstaller_version": "build-version", "packages": [],
                "files": {"LICENSE": module.digest(document)}}
    (module.THIRD / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(module.metadata, "version", lambda name: "build-version")
    monkeypatch.setattr(module, "locked_packages", lambda: {})
    monkeypatch.setattr(module, "runtime_npm", lambda: {})
    return module


@pytest.mark.parametrize("change", ["missing", "modified"])
def test_missing_or_changed_original_notice_blocks_distribution(notices, change):
    notices.verify()
    document = notices.THIRD / "LICENSE"
    if change == "missing":
        document.unlink()
    else:
        document.write_text("Truncated license")
    with pytest.raises(ValueError, match="Missing or changed third-party document"):
        notices.verify()


def test_dependency_update_requires_new_notices(notices, monkeypatch):
    monkeypatch.setattr(notices, "locked_packages", lambda: {"new-library": "1.0"})
    with pytest.raises(ValueError, match="Python dependencies changed"):
        notices.verify()


@pytest.mark.parametrize("component", ["PyInstaller.building.utils", "av.codec", "pytest"])
def test_removed_modules_block_distribution(notices, tmp_path, component):
    toc = [None] * 15
    toc[14] = [(component, "unused-path", "PYMODULE")]
    analysis = tmp_path / "Analysis-00.toc"
    analysis.write_text(repr(toc))
    with pytest.raises(ValueError, match="Removed or build-only modules"):
        notices.bundle(tmp_path, analysis)


def test_ffmpeg_native_binary_blocks_distribution(notices, tmp_path):
    toc = [None] * 15
    toc[14] = []
    analysis = tmp_path / "Analysis-00.toc"
    analysis.write_text(repr(toc))
    (tmp_path / "avcodec-62.dll").write_bytes(b"unwanted native library")
    with pytest.raises(ValueError, match="Unexpected native dependency"):
        notices.bundle(tmp_path, analysis)
