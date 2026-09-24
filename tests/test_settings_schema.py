from engine import settings_schema


def test_managed_settings_show_preferences_but_hide_service_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_schema, "_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(settings_schema.config, "MANAGED_BUILD", True)
    monkeypatch.setattr(settings_schema.config, "ONENOTE_ENABLED", False)

    state = settings_schema.public_state()
    groups = {group["group"]: group for group in state["schema"]}

    assert "OneNote" in groups
    assert "Managed service" in groups
    assert "AI access" not in groups
    assert "Azure OpenAI — primary connection" not in groups
    visible_keys = {
        field["key"]
        for group in state["schema"]
        for field in group["fields"]
    }
    assert "STT_LANGUAGE" in visible_keys
    assert "STT_DICTIONARY" in visible_keys
    assert "SUMMARY_LANGUAGE" in visible_keys
    assert "AI_GATEWAY_ENDPOINT" not in visible_keys
    assert "TRANSCRIBE_MODELS" not in visible_keys
    assert "HOST" not in visible_keys
    assert state["values"]["ONENOTE_ENABLED"] is False


def test_managed_settings_reject_service_control_updates(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_schema, "_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(settings_schema.config, "MANAGED_BUILD", True)

    try:
        settings_schema.write_env({"AI_GATEWAY_ENDPOINT": "https://other.example"})
    except ValueError as exc:
        assert "controlled by the managed service" in str(exc)
    else:
        raise AssertionError("managed service setting was accepted")


def test_standalone_settings_keep_infrastructure_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_schema, "_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(settings_schema.config, "MANAGED_BUILD", False)

    state = settings_schema.public_state()
    groups = {group["group"] for group in state["schema"]}

    assert "AI access" in groups
    assert "Azure OpenAI — primary connection" in groups
    assert "Managed service" not in groups


def test_dictionary_settings_are_normalized_and_persisted(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(settings_schema, "_ENV_PATH", env_path)
    monkeypatch.setattr(settings_schema.config, "MANAGED_BUILD", True)

    changed = settings_schema.write_env({
        "STT_DICTIONARY": [" CaféPlan ", "Workboard", "CAFÉPLAN", ""],
    })

    assert changed == ["STT_DICTIONARY"]
    assert env_path.read_text(encoding="utf-8") == (
        "STT_DICTIONARY=CaféPlan,Workboard\n"
    )
