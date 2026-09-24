# Isolate state before app/config imports, including token and log paths.
import os
import tempfile
os.environ.pop("VOICE_TRANSCRIBER_DATA_ROOT", None)
_TEST_STATE = tempfile.TemporaryDirectory(prefix="das-client-tests-")
os.environ["LOCALAPPDATA"] = _TEST_STATE.name

import pytest


@pytest.fixture(autouse=True)
def _default_local_ai(monkeypatch):
    # Profile-reload tests mutate the shared config module. Ordinary notes tests
    # use synthetic local credentials; managed tests opt in explicitly.
    import config
    monkeypatch.setattr(config, "AI_MODE", "local")


@pytest.fixture(autouse=True)
def _clear_onenote_caches():
    """Integration data is cached in module-level singletons for the app's
    whole lifetime, so without this one test's cached records answer the next
    test's request and the loader under test is never called."""
    import app

    app.STATE.onenote_notebooks_cache = None
    app.STATE.onenote_sections_cache.clear()
    yield
    app.STATE.onenote_notebooks_cache = None
    app.STATE.onenote_sections_cache.clear()


@pytest.fixture(autouse=True)
def _isolate_choice_memory(tmp_path, monkeypatch):
    """Learned choices persist to the real user data directory.

    Without redirecting it, tests would read the developer's own remembered
    choices and write test data into them.
    """
    from engine.choice_memory import ChoiceMemory
    import app

    monkeypatch.setattr(
        app, "_CHOICE_MEMORY",
        ChoiceMemory(tmp_path / "choice-memory.json"),
        raising=False,
    )
    # Suggestions must not depend on whoever happens to be signed in.
    monkeypatch.setattr(app, "_own_domains",
                        lambda: frozenset({"company.example"}))
