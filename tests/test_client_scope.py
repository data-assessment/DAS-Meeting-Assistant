"""Retired exports cannot be re-enabled by old settings or direct API calls."""
import pytest
from fastapi.testclient import TestClient

import app
from deployment_profile import ProfileError, validate_profile
from engine import settings_schema
from test_deployment_profile import direct


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/crm/status"), ("POST", "/api/crm/connect"),
    ("GET", "/api/crm/context"), ("POST", "/api/crm/choice"),
    ("POST", "/api/crm/create"), ("GET", "/api/jira/status"),
    ("POST", "/api/jira/connect"), ("POST", "/api/jira/disconnect"),
    ("GET", "/api/jira/context"), ("POST", "/api/jira/choice"),
    ("POST", "/api/jira/topics"), ("POST", "/api/jira/drafts"),
    ("POST", "/api/jira/create"), ("GET", "/jira/callback"),
])
def test_retired_exports_have_no_http_routes(method, path):
    assert path not in {route.path for route in app.api.routes}
    # The static UI mount responds 405 to unsupported POSTs and 404 to GETs.
    assert TestClient(app.api).request(method, path).status_code in (404, 405)


@pytest.mark.parametrize("managed", [True, False])
def test_old_switches_cannot_expose_retired_exports(monkeypatch, managed):
    monkeypatch.setenv("JIRA_ENABLED", "true")
    monkeypatch.setenv("CRM_ENABLED", "true")
    monkeypatch.setattr(settings_schema.config, "MANAGED_BUILD", managed)
    state = settings_schema.public_state()
    fields = {f["key"] for group in state["schema"] for f in group["fields"]}
    assert not any(k.startswith(("JIRA_", "CRM_")) for k in fields | state["values"].keys())
    payload = app.state_payload()
    assert not any(k.startswith(("jira", "crm")) for k in payload)


def test_existing_disabled_profile_flags_are_discarded():
    profile = validate_profile(direct(JIRA_ENABLED="false", CRM_ENABLED="false"))
    assert not any(k.startswith(("JIRA_", "CRM_")) for k in profile.environment())
    assert profile.environment()["AI_MODE"] == "entra"
    assert profile.settings["SPEECH_ENDPOINT"] == direct()["settings"]["SPEECH_ENDPOINT"]


@pytest.mark.parametrize("setting", ["JIRA_ENABLED", "CRM_ENABLED"])
@pytest.mark.parametrize("value", ["true", "", False])
def test_profile_cannot_enable_or_misconfigure_retired_exports(setting, value):
    with pytest.raises(ProfileError, match="Unsupported profile setting"):
        validate_profile(direct(**{setting: value}))


def test_saved_onenote_choice_survives_legacy_export_settings(monkeypatch):
    monkeypatch.setattr(app.NOTES, "load_credentials", lambda: None)
    monkeypatch.setattr(app.NOTES, "setup_complete", False)
    monkeypatch.setattr(app.NOTES, "onboarding_complete", False)
    monkeypatch.setattr(app.STATE, "onenote_choices", {})
    choice = {"lastNotebookId": "book-1", "sections": {"book-1": {"sectionName": "Meetings"}}}
    app._apply_settings({"onenote_choices": choice, "jira_choices": {}, "crm_choices": {}})
    assert app.STATE.onenote_choices == choice
