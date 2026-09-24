import importlib.util
from pathlib import Path
import shutil

import pytest


@pytest.fixture
def terms(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("microsoft_terms", root / "packaging/prepare_microsoft_terms.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Exercise real upstream texts and both user-facing covers.
    paths = [path for _, path, _ in module.TERMS] + [
        f"packaging/licenses/microsoft-components.{lang}.txt" for lang in ("de", "en")]
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    return module, tmp_path


def test_complete_source_terms_survive_rtf_encoding(terms):
    module, root = terms
    files = module.prepare(root, write=True)
    for lang in ("de", "en"):
        packet = files[f"third_party/microsoft-consent/microsoft-components.{lang}.rtf"].decode("ascii")
        for _, path, encoding in module.TERMS:
            for line in (root / path).read_text(encoding=encoding).splitlines():
                if line:
                    assert module.rtf_escape(line) in packet
    module.prepare(root)


@pytest.mark.parametrize("change", ["missing", "truncated", "source_changed", "cover_changed"])
def test_missing_or_changed_agreement_blocks_build(terms, change):
    module, root = terms
    module.prepare(root, write=True)
    packet = root / "third_party/microsoft-consent/microsoft-components.de.rtf"
    if change == "missing":
        packet.unlink()
    elif change == "truncated":
        packet.write_bytes(packet.read_bytes()[:100])
    else:
        target = root / (module.TERMS[1][1] if change == "source_changed"
                         else "packaging/licenses/microsoft-components.de.txt")
        target.write_text(target.read_text(encoding="utf-8") + "\nChanged terms", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing/stale Microsoft agreement"):
        module.prepare(root)


def test_rtf_escapes_unicode_and_control_syntax(terms):
    module, _ = terms
    assert module.rtf_escape("ä\\{}😀") == r"\u228?\\\{\}\u-10179?\u-8704?"
