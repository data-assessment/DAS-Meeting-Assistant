"""Host-wide SharePoint discovery has to stay fast without narrowing the config.

A bare tenant host in ONENOTE_SITE_PATHS expands to every visible site. Scanning
them one at a time made /api/onenote/targets take ~35s, and the startup warm-up
raced the dialog into doing the identical walk twice.
"""

import asyncio
import threading
import time

import app
from engine import onenote


def _sites(count):
    return [
        {
            "id": f"site-{i}",
            "displayName": f"Site {i}",
            "webUrl": f"https://tenant.sharepoint.com/sites/s{i}",
            "siteCollection": {"hostname": "tenant.sharepoint.com"},
        }
        for i in range(count)
    ]


def test_site_scan_runs_concurrently(monkeypatch):
    sites = _sites(8)
    monkeypatch.setattr(onenote, "_search_sites", lambda *_a: (sites, []))
    monkeypatch.setattr(onenote, "_site_id", lambda *_a: ("", ""))
    in_flight = []
    peak = [0]
    lock = threading.Lock()

    def slow_list(headers, site_id, name, ref, warn_when_empty=True):
        with lock:
            in_flight.append(site_id)
            peak[0] = max(peak[0], len(in_flight))
        time.sleep(0.2)
        with lock:
            in_flight.remove(site_id)
        return [{"id": f"nb-{site_id}", "name": site_id}], []

    monkeypatch.setattr(onenote, "_list_site_notebooks", slow_list)

    started = time.monotonic()
    notebooks, _ = onenote._host_site_notebooks({}, "tenant.sharepoint.com")
    took = time.monotonic() - started

    assert peak[0] > 1, "sites were still scanned one at a time"
    # Sequential would be 8 x 0.2s = 1.6s.
    assert took < 1.0, f"scan did not overlap (took {took:.2f}s)"
    assert len(notebooks) == 8


def test_site_scan_keeps_site_order(monkeypatch):
    """Concurrency must not shuffle the notebook list under the user."""
    sites = _sites(6)
    monkeypatch.setattr(onenote, "_search_sites", lambda *_a: (sites, []))
    monkeypatch.setattr(onenote, "_site_id", lambda *_a: ("", ""))

    def uneven(headers, site_id, name, ref, warn_when_empty=True):
        # Later sites finish first, so ordering cannot come from completion.
        time.sleep(0.05 * (6 - int(site_id.split("-")[1])))
        return [{"id": f"nb-{site_id}", "name": site_id}], []

    monkeypatch.setattr(onenote, "_list_site_notebooks", uneven)

    notebooks, _ = onenote._host_site_notebooks({}, "tenant.sharepoint.com")

    assert [n["id"] for n in notebooks] == [f"nb-site-{i}" for i in range(6)]


def test_site_scan_collects_every_warning(monkeypatch):
    sites = _sites(3)
    monkeypatch.setattr(onenote, "_search_sites", lambda *_a: (sites, []))
    monkeypatch.setattr(onenote, "_site_id", lambda *_a: ("", ""))
    monkeypatch.setattr(
        onenote, "_list_site_notebooks",
        lambda h, sid, n, r, warn_when_empty=True: ([], [f"bad {sid}"]),
    )

    notebooks, warnings = onenote._host_site_notebooks(
        {}, "tenant.sharepoint.com"
    )

    assert notebooks == []
    assert [w for w in warnings if w.startswith("bad ")] == [
        "bad site-0", "bad site-1", "bad site-2",
    ]


def test_concurrent_loads_scan_once(monkeypatch):
    """The warm-up and a dialog opening together must not both walk the tenant."""
    monkeypatch.setattr(app.STATE, "onenote_notebooks_cache", None,
                        raising=False)
    monkeypatch.setattr(app.config, "ONENOTE_SITE_PATHS", ["https://t"],
                        raising=False)
    scans = []

    def list_notebooks(interactive=False):
        scans.append(interactive)
        time.sleep(0.3)
        return [{"id": "nb", "name": "Notes"}], None

    monkeypatch.setattr(onenote, "list_notebooks", list_notebooks)

    async def exercise():
        warm = app._load_onenote_notebooks(refresh=True)
        on_demand = app._load_onenote_notebooks(refresh=False)
        return await asyncio.gather(warm, on_demand)

    results = asyncio.run(exercise())

    assert len(scans) == 1, f"tenant was walked {len(scans)} times"
    assert all(notebooks[0]["id"] == "nb" for notebooks, _ in results)
