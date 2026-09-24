"""Export redacted OneNote/OneDrive diagnostics for support comparison.

Run this on two machines with the same app build and compare the JSON outputs:

    python onenote_diag.py --interactive --out onenote-diagnostics-me.json

The report intentionally excludes access tokens, client secrets, API keys and
full opaque Graph object ids. It does include signed-in account names and
notebook names, because those are needed to diagnose picker differences.
"""
import argparse
import base64
import datetime
import json
import sys
import urllib.parse

import requests

import config
from engine import onenote
from engine.graph_auth import get_token

GRAPH = onenote.GRAPH
TIMEOUT = 30


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _decode_claims(token: str | None) -> dict:
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}
    scopes = sorted(str(claims.get("scp") or "").split())
    return {
        "tenantId": claims.get("tid"),
        "account": claims.get("preferred_username") or claims.get("upn"),
        "name": claims.get("name"),
        "scopes": scopes,
        "hasNotesReadWrite": "Notes.ReadWrite" in scopes,
        "hasSitesReadAll": "Sites.Read.All" in scopes,
    }


def _host(url: str | None) -> str:
    return urllib.parse.urlparse(url or "").netloc


def _path(url: str | None) -> str:
    return urllib.parse.urlparse(url or "").path


def _graph_get(
    headers: dict,
    path_or_url: str,
    params: dict | None = None,
) -> dict:
    url = (
        path_or_url
        if path_or_url.startswith("https://") else f"{GRAPH}{path_or_url}"
    )
    try:
        resp = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}
    out = {"ok": resp.status_code == 200, "status": resp.status_code}
    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code == 200:
        out["data"] = data
    else:
        out["error"] = resp.text[:500]
    return out


def _notebook_payload(notebook: dict) -> dict:
    links = notebook.get("links") or {}
    web_url = ((links.get("oneNoteWebUrl") or {}).get("href")) or ""
    return {
        "name": notebook.get("displayName") or notebook.get("name"),
        "source": notebook.get("source"),
        "webHost": _host(web_url),
        "webPath": _path(web_url),
        "hasSectionsUrl": bool(notebook.get("sectionsUrl")),
    }


def _summarize_notebooks(response: dict) -> dict:
    if not response.get("ok"):
        return {
            "ok": False,
            "status": response.get("status"),
            "error": response.get("error"),
        }
    notebooks = [
        _notebook_payload(item)
        for item in (response.get("data") or {}).get("value", [])
    ]
    notebooks.sort(key=lambda item: (item.get("name") or "").casefold())
    return {"ok": True, "count": len(notebooks), "notebooks": notebooks}


def _configured_site_probe(headers: dict, site_ref: str) -> dict:
    host = onenote._site_search_hostname(site_ref)
    if host:
        return _host_probe(headers, host, site_ref)
    site_id, site_name, error = _resolve_site(headers, site_ref)
    out = {
        "input": site_ref,
        "mode": "exact-site",
        "siteResolved": bool(site_id),
        "siteName": site_name,
    }
    if not site_id:
        out["error"] = error or "Could not resolve site."
        return out
    out["notebooks"] = _site_notebooks(headers, site_id)
    return out


def _host_probe(headers: dict, hostname: str, site_ref: str) -> dict:
    out = {"input": site_ref, "mode": "base-host", "host": hostname}
    search = _graph_get(
        headers,
        "/sites",
        {
            "search": "*",
            "$select": "id,displayName,name,webUrl,siteCollection",
        },
    )
    sites = []
    if search.get("ok"):
        for site in (search.get("data") or {}).get("value", []):
            if onenote._site_matches_hostname(site, hostname):
                sites.append(site)
    else:
        out["searchError"] = {
            "status": search.get("status"),
            "error": search.get("error"),
        }

    root_id, root_name, root_error = _resolve_site(headers, hostname)
    if root_id and not any(site.get("id") == root_id for site in sites):
        sites.insert(0, {
            "id": root_id,
            "displayName": root_name or hostname,
            "webUrl": f"https://{hostname}",
            "siteCollection": {"hostname": hostname},
        })
    elif root_error:
        out["rootSiteError"] = root_error

    out["siteCount"] = len(sites)
    out["sites"] = []
    for site in sites:
        site_id = site.get("id") or ""
        notebooks = (
            _site_notebooks(headers, site_id)
            if site_id else {"ok": False}
        )
        out["sites"].append({
            "name": site.get("displayName") or site.get("name"),
            "webHost": _host(site.get("webUrl")),
            "webPath": _path(site.get("webUrl")),
            "notebooks": notebooks,
        })
    return out


def _resolve_site(
    headers: dict,
    site_ref: str,
) -> tuple[str | None, str | None, str | None]:
    graph_path, fallback_label = onenote._site_graph_path(site_ref)
    if not graph_path:
        return None, fallback_label, "Unsupported site reference format."
    result = _graph_get(headers, f"/sites/{graph_path}")
    if not result.get("ok"):
        return (
            None,
            fallback_label,
            f"HTTP {result.get('status')}: {result.get('error')}",
        )
    data = result.get("data") or {}
    return data.get("id"), data.get("displayName") or fallback_label, None


def _site_notebooks(headers: dict, site_id: str) -> dict:
    result = _graph_get(
        headers,
        f"/sites/{site_id}/onenote/notebooks",
        {"$select": "id,displayName,links,sectionsUrl"},
    )
    return _summarize_notebooks(result)


def _onedrive_sync_accounts() -> list[dict]:
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    base = r"Software\Microsoft\OneDrive\Accounts"
    allowed = {
        "configuredtenantid",
        "displayname",
        "serviceendpointuri",
        "tenantid",
        "useremail",
    }
    accounts = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)
    except OSError:
        return accounts
    with root:
        index = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            account = {"accountKey": subkey_name}
            try:
                subkey = winreg.OpenKey(root, subkey_name)
            except OSError:
                accounts.append(account)
                continue
            with subkey:
                value_index = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(
                            subkey,
                            value_index,
                        )
                    except OSError:
                        break
                    value_index += 1
                    if name.casefold() in allowed:
                        account[name] = value
            accounts.append(account)
    return accounts


def _acquire_token(
    interactive: bool,
    include_sites: bool,
) -> tuple[str | None, dict]:
    try:
        token = get_token(
            interactive=interactive,
            include_onenote=True,
            include_sharepoint_sites=include_sites,
        )
    except Exception as exc:
        return None, {"available": False, "error": str(exc)}
    claims = _decode_claims(token)
    return token, {"available": bool(token), **claims}


def build_report(interactive: bool) -> dict:
    notes_token, notes_claims = _acquire_token(
        interactive,
        include_sites=False,
    )
    sites_token, sites_claims = _acquire_token(interactive, include_sites=True)
    report = {
        "generatedAt": _now(),
        "config": {
            "oneNoteEnabled": config.ONENOTE_ENABLED,
            "defaultNotebook": config.ONENOTE_NOTEBOOK,
            "defaultSection": config.ONENOTE_SECTION,
            "sitePaths": config.ONENOTE_SITE_PATHS,
        },
        "tokens": {
            "notes": notes_claims,
            "sharePointSites": sites_claims,
        },
        "localOneDriveSyncAccounts": _onedrive_sync_accounts(),
        "graph": {},
    }
    if notes_token:
        notes_headers = {"Authorization": f"Bearer {notes_token}"}
        report["graph"]["me"] = _graph_get(
            notes_headers,
            "/me",
            {"$select": "displayName,userPrincipalName,mail"},
        )
        report["graph"]["meDrive"] = _graph_get(
            notes_headers,
            "/me/drive",
            {"$select": "name,driveType,webUrl,owner"},
        )
        report["graph"]["personalAndSharedNotebooks"] = _summarize_notebooks(
            _graph_get(
                notes_headers,
                "/me/onenote/notebooks",
                {
                    "includeSharedNotebooks": "true",
                    "$select": "id,displayName,links,sectionsUrl",
                },
            )
        )
    if sites_token:
        site_headers = {"Authorization": f"Bearer {sites_token}"}
        report["graph"]["configuredSharePointSites"] = [
            _configured_site_probe(site_headers, site_ref)
            for site_ref in config.ONENOTE_SITE_PATHS
        ]
    else:
        report["graph"]["configuredSharePointSites"] = {
            "ok": False,
            "error": (
                "No token with SharePoint site permissions was available."
            ),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export redacted OneNote/OneDrive diagnostics as JSON."
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="open Microsoft sign-in if cached Graph permissions are missing",
    )
    parser.add_argument(
        "--out",
        help="write JSON to this file instead of stdout",
    )
    args = parser.parse_args()

    report = build_report(interactive=args.interactive)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
