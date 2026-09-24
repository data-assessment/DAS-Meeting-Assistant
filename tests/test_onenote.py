from engine import onenote


class _Response:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_find_or_create_uses_display_name_filter(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(
            200,
            {"value": [{"id": "section-id", "displayName": "Transcripts"}]},
        )

    def fake_post(*_args, **_kwargs):
        raise AssertionError("existing section should not be created")

    monkeypatch.setattr(onenote.requests, "get", fake_get)
    monkeypatch.setattr(onenote.requests, "post", fake_post)

    result = onenote._find_or_create(
        {"Authorization": "Bearer token"},
        "https://graph.microsoft.com/v1.0/me/onenote/notebooks/"
        "notebook-id/sections",
        "https://graph.microsoft.com/v1.0/me/onenote/notebooks/"
        "notebook-id/sections",
        "Transcripts",
        "onenote.section",
    )

    assert result == "section-id"
    assert calls[0][1]["params"] == {
        "$select": "id,displayName",
        "$filter": "displayName eq 'Transcripts'",
    }


def test_find_or_create_escapes_display_name_filter(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(200, {"value": []})

    def fake_post(url, **kwargs):
        return _Response(201, {"id": "created-id"})

    monkeypatch.setattr(onenote.requests, "get", fake_get)
    monkeypatch.setattr(onenote.requests, "post", fake_post)

    result = onenote._find_or_create(
        {"Authorization": "Bearer token"},
        "https://graph.microsoft.com/v1.0/me/onenote/notebooks",
        "https://graph.microsoft.com/v1.0/me/onenote/notebooks",
        "Bob's Notes",
        "onenote.notebook",
    )

    assert result == "created-id"
    assert calls[0][1]["params"]["$filter"] == "displayName eq 'Bob''s Notes'"


def test_site_search_hostname_accepts_only_base_hosts():
    assert (
        onenote._site_search_hostname("https://tenant.sharepoint.com")
        == "tenant.sharepoint.com"
    )
    assert (
        onenote._site_search_hostname("tenant.sharepoint.com")
        == "tenant.sharepoint.com"
    )
    assert (
        onenote._site_search_hostname(
            "https://tenant.sharepoint.com/sites/Team"
        ) is None
    )
    assert (
        onenote._site_search_hostname("tenant.sharepoint.com:/sites/Team")
        is None
    )


def test_site_notebooks_searches_base_host(monkeypatch):
    monkeypatch.setattr(
        onenote.config,
        "ONENOTE_SITE_PATHS",
        ["https://tenant.sharepoint.com"],
    )
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url == f"{onenote.GRAPH}/sites/tenant.sharepoint.com":
            return _Response(200, {"id": "root-id", "displayName": "Root"})
        if url == f"{onenote.GRAPH}/sites":
            return _Response(200, {
                "value": [
                    {
                        "id": "root-id",
                        "displayName": "Root",
                        "webUrl": "https://tenant.sharepoint.com",
                        "siteCollection": {
                            "hostname": "tenant.sharepoint.com"
                        },
                    },
                    {
                        "id": "team-id",
                        "displayName": "V3development",
                        "webUrl": (
                            "https://tenant.sharepoint.com/sites/"
                            "V3development"
                        ),
                        "siteCollection": {
                            "hostname": "tenant.sharepoint.com"
                        },
                    },
                ]
            })
        if url == f"{onenote.GRAPH}/sites/root-id/onenote/notebooks":
            return _Response(200, {"value": []})
        if url == f"{onenote.GRAPH}/sites/team-id/onenote/notebooks":
            return _Response(200, {
                "value": [{
                    "id": "notebook-id",
                    "displayName": "Voice Transcriber",
                    "sectionsUrl": (
                        f"{onenote.GRAPH}/sites/team-id/onenote/notebooks/"
                        "notebook-id/sections"
                    ),
                    "links": {
                        "oneNoteWebUrl": {
                            "href": (
                                "https://tenant.sharepoint.com/sites/"
                                "V3development/notebook"
                            )
                        }
                    },
                }]
            })
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(onenote.requests, "get", fake_get)

    notebooks, warnings = onenote._site_notebooks(
        {"Authorization": "Bearer token"}
    )

    assert warnings == []
    assert notebooks == [{
        "id": "notebook-id",
        "name": "Voice Transcriber",
        "label": "Voice Transcriber (V3development)",
        "sectionsUrl": (
            f"{onenote.GRAPH}/sites/team-id/onenote/notebooks/"
            "notebook-id/sections"
        ),
        "webUrl": "https://tenant.sharepoint.com/sites/V3development/notebook",
        "source": "V3development",
    }]
    assert calls[0][0] == f"{onenote.GRAPH}/sites"
    assert calls[0][1]["params"] == {
        "search": "*",
        "$select": "id,displayName,name,webUrl,siteCollection",
    }


def test_site_notebooks_keeps_exact_site_lookup(monkeypatch):
    monkeypatch.setattr(
        onenote.config,
        "ONENOTE_SITE_PATHS",
        ["https://tenant.sharepoint.com/sites/V3development"],
    )
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url == (
            f"{onenote.GRAPH}/sites/tenant.sharepoint.com:/sites/"
            "V3development"
        ):
            return _Response(
                200,
                {"id": "team-id", "displayName": "V3development"},
            )
        if url == f"{onenote.GRAPH}/sites/team-id/onenote/notebooks":
            return _Response(200, {"value": []})
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(onenote.requests, "get", fake_get)

    notebooks, warnings = onenote._site_notebooks(
        {"Authorization": "Bearer token"}
    )

    assert notebooks == []
    assert warnings == ["No OneNote notebooks found on: V3development"]
    assert calls[0][0] == (
        f"{onenote.GRAPH}/sites/tenant.sharepoint.com:/sites/"
        "V3development"
    )


def test_host_discovery_skips_sites_that_forbid_onenote(monkeypatch):
    monkeypatch.setattr(
        onenote.requests,
        "get",
        lambda *_args, **_kwargs: _Response(403, {"error": "forbidden"}),
    )

    notebooks, warnings = onenote._list_site_notebooks(
        {"Authorization": "Bearer token"},
        "designer-id",
        "Designer",
        "https://tenant.sharepoint.com/sites/Designer",
        warn_when_empty=False,
    )

    assert notebooks == []
    assert warnings == []


def test_exact_site_still_reports_forbidden_onenote(monkeypatch):
    monkeypatch.setattr(
        onenote.requests,
        "get",
        lambda *_args, **_kwargs: _Response(403, {"error": "forbidden"}),
    )

    notebooks, warnings = onenote._list_site_notebooks(
        {"Authorization": "Bearer token"},
        "designer-id",
        "Designer",
        "https://tenant.sharepoint.com/sites/Designer",
    )

    assert notebooks == []
    assert warnings == ["Could not list OneNote notebooks for Designer (HTTP 403)."]


def test_site_notebooks_warns_when_base_host_has_no_sites(monkeypatch):
    monkeypatch.setattr(
        onenote.config,
        "ONENOTE_SITE_PATHS",
        ["https://tenant.sharepoint.com"],
    )

    def fake_get(url, **_kwargs):
        if url == f"{onenote.GRAPH}/sites":
            return _Response(200, {"value": []})
        if url == f"{onenote.GRAPH}/sites/tenant.sharepoint.com":
            return _Response(404, text="not found")
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(onenote.requests, "get", fake_get)

    notebooks, warnings = onenote._site_notebooks(
        {"Authorization": "Bearer token"}
    )

    assert notebooks == []
    assert warnings == [
        "No SharePoint sites found for host: tenant.sharepoint.com"
    ]


def test_site_notebooks_base_host_uses_root_site(monkeypatch):
    monkeypatch.setattr(
        onenote.config,
        "ONENOTE_SITE_PATHS",
        ["https://tenant.sharepoint.com"],
    )

    def fake_get(url, **_kwargs):
        if url == f"{onenote.GRAPH}/sites":
            return _Response(200, {"value": []})
        if url == f"{onenote.GRAPH}/sites/tenant.sharepoint.com":
            return _Response(200, {"id": "root-id", "displayName": "Root"})
        if url == f"{onenote.GRAPH}/sites/root-id/onenote/notebooks":
            return _Response(200, {
                "value": [{"id": "notebook-id", "displayName": "Root Notes"}]
            })
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(onenote.requests, "get", fake_get)

    notebooks, warnings = onenote._site_notebooks(
        {"Authorization": "Bearer token"}
    )

    assert warnings == []
    assert notebooks[0]["id"] == "notebook-id"
    assert notebooks[0]["label"] == "Root Notes (Root)"


def test_site_notebooks_base_host_matches_web_url_hostname(monkeypatch):
    monkeypatch.setattr(
        onenote.config,
        "ONENOTE_SITE_PATHS",
        ["https://tenant-my.sharepoint.com"],
    )

    def fake_get(url, **_kwargs):
        if url == f"{onenote.GRAPH}/sites":
            return _Response(200, {
                "value": [{
                    "id": "team-id",
                    "displayName": "V3development",
                    "webUrl": (
                        "https://tenant-my.sharepoint.com/sites/"
                        "V3development"
                    ),
                    "siteCollection": {"hostname": "tenant.sharepoint.com"},
                }]
            })
        if url == f"{onenote.GRAPH}/sites/tenant-my.sharepoint.com":
            return _Response(404, text="not found")
        if url == f"{onenote.GRAPH}/sites/team-id/onenote/notebooks":
            return _Response(200, {
                "value": [{"id": "notebook-id", "displayName": "V3 Notes"}]
            })
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(onenote.requests, "get", fake_get)

    notebooks, warnings = onenote._site_notebooks(
        {"Authorization": "Bearer token"}
    )

    assert warnings == []
    assert notebooks[0]["id"] == "notebook-id"
    assert notebooks[0]["label"] == "V3 Notes (V3development)"
