"""Publish a finished transcript to Microsoft OneNote as a page.

This is an *additional* destination — the local Markdown file is still written. It
reuses the SAME Microsoft sign-in as the rest of the tool: OneNote is a Microsoft
Graph workload, so the existing MSAL token (engine/graph_auth) works as-is once the
`Notes.ReadWrite` delegated scope is granted. No separate login.

Target = a notebook + section by display name (config.ONENOTE_NOTEBOOK /
ONENOTE_SECTION), found-or-created on first use. A page is then created from an HTML
rendering of the same content the Markdown file holds.

Runs from the user-triggered OneNote dialog. Loading targets uses cached auth first;
refreshing or saving can request the Notes scope if consent is missing.

Untested against a live tenant yet — Graph calls are logged via graph_log and all
failures degrade to a note, so a first live run can be validated from the logs.
"""
import datetime
import html
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import requests

import config
from engine import mdfmt
from engine.graph_auth import get_token
from engine.graph_log import log_graph_call
from engine.meeting import is_diarize_label, seg_label

GRAPH = "https://graph.microsoft.com/v1.0"
_TIMEOUT = 30
_MAX_SITE_SEARCH_RESULTS = 200
# Concurrent per-site notebook lookups during host-wide discovery. Enough to
# collapse a long tenant walk, low enough to stay friendly to Graph throttling.
_SITE_SCAN_WORKERS = 8


def available() -> tuple[bool, str | None]:
    if not config.ONENOTE_ENABLED:
        return False, "OneNote disabled (ONENOTE_ENABLED=false)"
    if not config.GRAPH_CLIENT_ID:
        return False, "GRAPH_CLIENT_ID not set"
    return True, None


# --------------------------------------------------------------------------- #
# HTML rendering (mirrors engine/meeting.py's Markdown, as OneNote page HTML)
# --------------------------------------------------------------------------- #
def _esc(text) -> str:
    return html.escape(str(text or ""))


def _fmt_ts(seconds: float) -> str:
    h, rem = divmod(int(seconds or 0), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _attendees_html(attendees, note, source) -> str:
    out = [f"<h2>Attendees</h2><p><i>Source: {_esc(source)}.</i></p>"]
    if attendees:
        items = []
        for a in attendees:
            email = f" &lt;{_esc(a['email'])}&gt;" if a.get("email") else ""
            extra = ", ".join(p for p in (a.get("role"),) if p)
            suffix = f" — {_esc(extra)}" if extra else ""
            items.append(f"<li><b>{_esc(a.get('name', '(unknown)'))}</b>{email}{suffix}</li>")
        out.append("<ul>" + "".join(items) + "</ul>")
    else:
        out.append(f"<p><i>None — {_esc(note or 'no attendees available')}.</i></p>")
    return "".join(out)


def _transcript_html(text, note, segments, source) -> str:
    out = []
    if source:
        out.append(f"<p><i>Source: {_esc(source)}.</i></p>")
    if segments:
        current_chunk = None
        multi = (any(is_diarize_label(s.get("speaker")) for s in segments)
                 and len({s.get("chunk", 0) for s in segments}) > 1)
        for seg in segments:
            chunk = seg.get("chunk", 0)
            if multi and chunk != current_chunk:
                out.append(f"<h3>Part {chunk + 1} (speaker labels reset)</h3>")
                current_chunk = chunk
            ts = _fmt_ts(seg.get("start", 0))
            label = seg_label(seg.get("speaker"))
            body = _esc((seg.get("text") or "").strip())
            prefix = f"[{ts}] {_esc(label)}:" if label else f"[{ts}]"
            out.append(f"<p><b>{prefix}</b> {body}</p>")
        if note:
            out.append(f"<p><i>Note: {_esc(note)}</i></p>")
    elif text:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        out.append("<ul>" + "".join(f"<li>{_esc(ln)}</li>" for ln in lines) + "</ul>")
    else:
        out.append(f"<p><i>Unavailable — {_esc(note or 'speech-to-text not run')}.</i></p>")
    return "".join(out)


def _page_html(title, started_at, ended_at, attendees, attendees_note,
               attendees_source, summary, summary_note,
               transcript_text, transcript_note, transcript_segments,
               transcript_source, page_title: str | None = None) -> str:
    duration = ended_at - started_at
    meta = (
        "<table><tr><td><b>Start</b></td><td>{s}</td></tr>"
        "<tr><td><b>End</b></td><td>{e}</td></tr>"
        "<tr><td><b>Duration</b></td><td>{d}</td></tr></table>"
    ).format(s=started_at.strftime("%Y-%m-%d %H:%M:%S"),
             e=ended_at.strftime("%Y-%m-%d %H:%M:%S"), d=duration)

    summary_html = ""
    if summary:
        # The summary is Markdown; OneNote wants HTML, so render it (otherwise '###'
        # and '**' show through as literal text).
        summary_html = "<h2>Summary</h2>" + mdfmt.to_html(summary)
    elif summary_note:
        summary_html = f"<h2>Summary</h2><p><i>Unavailable — {_esc(summary_note)}.</i></p>"

    body = (
        meta
        + _attendees_html(attendees, attendees_note, attendees_source)
        + summary_html
        + "<h2>Transcript</h2>"
        + _transcript_html(transcript_text, transcript_note, transcript_segments, transcript_source)
    )
    created = started_at.astimezone().isoformat(timespec="seconds")
    # Auto-detected meetings all carry the generic title "Teams-Meeting", so prefix
    # the page name with the start date/time — otherwise every page in the section is
    # identically named (the local .md avoids this via a timestamped filename).
    page_title = page_title or f"{started_at:%Y-%m-%d %H:%M} · {title}"
    return (
        "<!DOCTYPE html><html><head>"
        f"<title>{_esc(page_title)}</title>"
        f'<meta name="created" content="{created}"/>'
        "</head><body>" + body + "</body></html>"
    )


# --------------------------------------------------------------------------- #
# Graph: find-or-create the notebook + section, then create the page
# --------------------------------------------------------------------------- #
def _find_or_create(headers: dict, list_url: str, create_url: str, name: str,
                    purpose: str) -> str | None:
    """Return the id of the OneNote notebook/section named `name` under list_url,
    creating it under create_url if absent. Returns None on failure."""
    filter_value = name.replace("'", "''")
    resp = requests.get(
        list_url,
        headers=headers,
        params={
            "$select": "id,displayName",
            "$filter": f"displayName eq '{filter_value}'",
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code == 200:
        for item in resp.json().get("value", []):
            if (item.get("displayName") or "").casefold() == name.casefold():
                log_graph_call(f"{purpose}: found", "GET", list_url, status=200,
                               result={"name": name})
                return item["id"]
    else:
        log_graph_call(f"{purpose}: list", "GET", list_url, status=resp.status_code,
                       error=resp.text[:300])
        return None
    # Not found — create it.
    created = requests.post(create_url, headers={**headers, "Content-Type": "application/json"},
                            json={"displayName": name}, timeout=_TIMEOUT)
    if created.status_code in (200, 201):
        log_graph_call(f"{purpose}: created", "POST", create_url,
                       status=created.status_code, result={"name": name})
        return created.json().get("id")
    log_graph_call(f"{purpose}: create", "POST", create_url,
                   status=created.status_code, error=created.text[:300])
    return None


def _notebook_payload(notebook: dict) -> dict:
    links = notebook.get("links") or {}
    web_link = links.get("oneNoteWebUrl") or {}
    return {
        "id": notebook.get("id", ""),
        "name": notebook.get("displayName") or "(untitled notebook)",
        "label": notebook.get("displayName") or "(untitled notebook)",
        "sectionsUrl": notebook.get("sectionsUrl"),
        "webUrl": web_link.get("href"),
    }


def _site_graph_path(site_ref: str) -> tuple[str | None, str | None]:
    site_ref = (site_ref or "").strip()
    if not site_ref:
        return None, None
    if "," in site_ref and "://" not in site_ref:
        return site_ref, site_ref
    parsed = urllib.parse.urlparse(site_ref)
    if parsed.scheme and parsed.netloc:
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) >= 2 and parts[0].lower() in ("sites", "teams"):
            parts = parts[:2]
        path = "/".join(parts)
        label = parts[-1] if parts else parsed.netloc
        return (f"{parsed.netloc}:/{path}" if path else parsed.netloc), label
    if ":/" in site_ref:
        label = site_ref.rstrip("/").split("/")[-1]
        return site_ref, label
    if "/" not in site_ref and "." in site_ref:
        return site_ref, site_ref
    return None, site_ref


def _site_search_hostname(site_ref: str) -> str | None:
    site_ref = (site_ref or "").strip().rstrip("/")
    if (
        not site_ref
        or "," in site_ref
        or ":/" in site_ref and "://" not in site_ref
    ):
        return None
    parsed = urllib.parse.urlparse(site_ref)
    if parsed.scheme and parsed.netloc:
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        return parsed.netloc if not parts else None
    if "://" not in site_ref and "/" not in site_ref and "." in site_ref:
        return site_ref
    return None


def _site_id(headers: dict, site_ref: str) -> tuple[str | None, str | None]:
    graph_path, fallback_label = _site_graph_path(site_ref)
    if not graph_path:
        return None, fallback_label
    if "," in graph_path:
        return graph_path, fallback_label
    url = f"{GRAPH}/sites/{graph_path}"
    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        print(f"[onenote] site lookup failed for {site_ref}:", exc)
        return None, fallback_label
    if resp.status_code != 200:
        log_graph_call("onenote.site: lookup", "GET", url,
                       status=resp.status_code, error=resp.text[:300])
        return None, fallback_label
    data = resp.json()
    return data.get("id"), data.get("displayName") or fallback_label


def _site_ref_note(site_ref: str) -> str | None:
    return None


def _site_label(site: dict) -> str:
    if site.get("displayName"):
        return site["displayName"]
    web_url = site.get("webUrl") or ""
    parsed = urllib.parse.urlparse(web_url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    return (
        parts[-1]
        if parts else site.get("name") or site.get("id") or "SharePoint site"
    )


def _site_hostname(site: dict) -> str:
    site_collection = site.get("siteCollection") or {}
    if site_collection.get("hostname"):
        return site_collection["hostname"]
    web_url = site.get("webUrl") or ""
    return urllib.parse.urlparse(web_url).netloc


def _site_matches_hostname(site: dict, hostname: str) -> bool:
    site_collection = site.get("siteCollection") or {}
    hosts = [site_collection.get("hostname") or ""]
    web_url = site.get("webUrl") or ""
    hosts.append(urllib.parse.urlparse(web_url).netloc)
    return any(
        host.casefold() == hostname.casefold()
        for host in hosts
        if host
    )


def _search_sites(
    headers: dict,
    hostname: str,
) -> tuple[list[dict], list[str]]:
    sites = []
    warnings = []
    url = f"{GRAPH}/sites"
    params = {
        "search": "*",
        "$select": "id,displayName,name,webUrl,siteCollection",
    }
    while url and len(sites) < _MAX_SITE_SEARCH_RESULTS:
        try:
            resp = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            print(
                f"[onenote] SharePoint site search failed for {hostname}:",
                exc,
            )
            warnings.append(
                f"Could not search SharePoint sites for: {hostname}"
            )
            break
        params = None
        if resp.status_code != 200:
            log_graph_call("onenote.sites: search", "GET", url,
                           status=resp.status_code, error=resp.text[:300])
            warnings.append(
                f"Could not search SharePoint sites for {hostname} "
                f"(HTTP {resp.status_code})."
            )
            break
        data = resp.json()
        for site in data.get("value", []):
            if _site_matches_hostname(site, hostname):
                sites.append(site)
                if len(sites) >= _MAX_SITE_SEARCH_RESULTS:
                    break
        url = (
            data.get("@odata.nextLink")
            if len(sites) < _MAX_SITE_SEARCH_RESULTS else None
        )
    if len(sites) >= _MAX_SITE_SEARCH_RESULTS:
        warnings.append(
            f"SharePoint site search for {hostname} stopped after "
            f"{_MAX_SITE_SEARCH_RESULTS} sites. Add exact site URLs for "
            "missing notebooks."
        )
    return sites, warnings


def _list_site_notebooks(
    headers: dict,
    site_id: str,
    site_name: str,
    site_ref: str,
    warn_when_empty: bool = True,
) -> tuple[list[dict], list[str]]:
    url = f"{GRAPH}/sites/{site_id}/onenote/notebooks"
    try:
        resp = requests.get(
            url,
            headers=headers,
            params={"$select": "id,displayName,links,sectionsUrl"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        print(
            f"[onenote] site notebook lookup failed for {site_ref}:",
            exc,
        )
        return [], [f"Could not list OneNote notebooks for: {site_ref}"]
    if resp.status_code != 200:
        log_graph_call("onenote.site.notebooks: list", "GET", url,
                       status=resp.status_code, error=resp.text[:300])
        # A host-wide discovery walks every visible SharePoint site. Some sites
        # expose their metadata but do not permit the OneNote API; skip those
        # expected partial-access failures without alarming the user. An exact
        # configured site still reports the same 403 as an actionable warning.
        if resp.status_code == 403 and not warn_when_empty:
            return [], []
        return [], [
            f"Could not list OneNote notebooks for "
            f"{site_name or site_ref} "
            f"(HTTP {resp.status_code})."
        ]
    notebooks = []
    for item in resp.json().get("value", []):
        notebook = _notebook_payload(item)
        notebook["source"] = site_name or site_ref
        notebook["label"] = f"{notebook['name']} ({notebook['source']})"
        notebooks.append(notebook)
    if not notebooks and warn_when_empty:
        return [], [f"No OneNote notebooks found on: {site_name or site_ref}"]
    return notebooks, []


def _host_site_notebooks(
    headers: dict,
    hostname: str,
) -> tuple[list[dict], list[str]]:
    sites, warnings = _search_sites(headers, hostname)
    root_site_id, root_site_name = _site_id(headers, hostname)
    already_found = any(site.get("id") == root_site_id for site in sites)
    if root_site_id and not already_found:
        sites.insert(0, {
            "id": root_site_id,
            "displayName": root_site_name or hostname,
            "webUrl": f"https://{hostname}",
            "siteCollection": {"hostname": hostname},
        })
    # A bare tenant host expands to every visible site, and each lookup takes
    # seconds, so doing them one after another made the dialog wait ~35s. They
    # are independent reads, so run a bounded batch at a time and keep the
    # site order stable by collecting the results in submission order.
    scanned = [site for site in sites if site.get("id")]
    notebooks = []
    if scanned:
        with ThreadPoolExecutor(
            max_workers=min(_SITE_SCAN_WORKERS, len(scanned))
        ) as pool:
            pending = [
                pool.submit(
                    _list_site_notebooks,
                    headers,
                    site.get("id", ""),
                    _site_label(site),
                    site.get("webUrl") or hostname,
                    False,
                )
                for site in scanned
            ]
            for future in pending:
                found, site_warnings = future.result()
                notebooks.extend(found)
                warnings.extend(site_warnings)
    if not sites and not warnings:
        warnings.append(f"No SharePoint sites found for host: {hostname}")
    elif sites and not notebooks:
        warnings.append(
            f"No OneNote notebooks found under SharePoint host: {hostname}"
        )
    return notebooks, warnings


def _site_notebooks(headers: dict) -> tuple[list[dict], list[str]]:
    notebooks = []
    warnings = []
    for site_ref in config.ONENOTE_SITE_PATHS:
        ref_note = _site_ref_note(site_ref)
        if ref_note:
            warnings.append(ref_note)
        search_hostname = _site_search_hostname(site_ref)
        if search_hostname:
            found, host_warnings = _host_site_notebooks(
                headers,
                search_hostname,
            )
            notebooks.extend(found)
            warnings.extend(host_warnings)
            continue
        site_id, site_name = _site_id(headers, site_ref)
        if not site_id:
            warnings.append(f"Could not resolve SharePoint site: {site_ref}")
            continue
        found, site_warnings = _list_site_notebooks(
            headers,
            site_id,
            site_name or site_ref,
            site_ref,
        )
        notebooks.extend(found)
        warnings.extend(site_warnings)
    return notebooks, warnings


def list_notebooks(interactive: bool = False) -> tuple[list[dict], str | None]:
    """Accessible OneNote notebooks for the signed-in user.

    Graph can include shared notebooks here; if a team/shared notebook is
    writable, the selected section can receive the transcript page.
    """
    ok, why = available()
    if not ok:
        return [], why
    token = get_token(interactive=interactive, include_onenote=True)
    if not token:
        return [], "Microsoft sign-in required for OneNote"
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{GRAPH}/me/onenote/notebooks"
    try:
        resp = requests.get(
            url,
            headers=headers,
            params={
                "includeSharedNotebooks": "true",
                "$select": "id,displayName,links,sectionsUrl",
            },
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        return [], f"OneNote notebook lookup failed: {exc}"
    if resp.status_code != 200:
        log_graph_call("onenote.notebooks: list", "GET", url,
                       status=resp.status_code, error=resp.text[:300])
        return [], f"OneNote notebook lookup failed (HTTP {resp.status_code})"
    notebooks = [
        _notebook_payload(item)
        for item in resp.json().get("value", [])
    ]
    warning = None
    if config.ONENOTE_SITE_PATHS:
        site_token = get_token(
            interactive=interactive,
            include_onenote=True,
            include_sharepoint_sites=True,
        )
        if site_token:
            site_headers = {"Authorization": f"Bearer {site_token}"}
            site_notebooks, site_warnings = _site_notebooks(site_headers)
            notebooks.extend(site_notebooks)
            if site_warnings:
                warning = " ".join(site_warnings)
        else:
            warning = (
                "SharePoint site notebooks require admin-approved "
                "Microsoft Graph site access. Use Settings > "
                "Microsoft sign-in (Graph) > Grant SharePoint sites."
            )
    deduped = {}
    for notebook in notebooks:
        deduped[notebook["id"]] = notebook
    notebooks = list(deduped.values())
    notebooks.sort(key=lambda item: item.get("label", item["name"]).casefold())
    return notebooks, warning


def list_sections(
    notebook_id: str,
    sections_url: str | None = None,
    interactive: bool = False,
) -> tuple[list[dict], str | None]:
    ok, why = available()
    if not ok:
        return [], why
    token = get_token(
        interactive=interactive,
        include_onenote=True,
        include_sharepoint_sites=bool(sections_url),
    )
    if not token:
        if sections_url:
            return [], (
                "SharePoint site sections require admin-approved "
                "Microsoft Graph site access. Use Settings > "
                "Microsoft sign-in (Graph) > Grant SharePoint sites."
            )
        return [], "Microsoft sign-in required for OneNote"
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        sections_url
        or f"{GRAPH}/me/onenote/notebooks/{notebook_id}/sections"
    )
    if not url.startswith(GRAPH):
        return [], "Invalid OneNote sections URL"
    try:
        resp = requests.get(
            url,
            headers=headers,
            params={"$select": "id,displayName"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        return [], f"OneNote section lookup failed: {exc}"
    if resp.status_code != 200:
        log_graph_call("onenote.sections: list", "GET", url,
                       status=resp.status_code, error=resp.text[:300])
        return [], f"OneNote section lookup failed (HTTP {resp.status_code})"
    sections = [
        {
            "id": item.get("id", ""),
            "name": item.get("displayName") or "(untitled section)",
        }
        for item in resp.json().get("value", [])
    ]
    sections.sort(key=lambda item: item["name"].casefold())
    return sections, None


def notebook_web_url() -> str | None:
    """Best-effort web URL of the configured notebook, for the 'Open OneNote' nav
    action. Silent token only; returns None if unavailable."""
    token = get_token(interactive=False, include_onenote=True)
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(f"{GRAPH}/me/onenote/notebooks?$select=displayName,links",
                            headers=headers, timeout=_TIMEOUT)
        if resp.status_code == 200:
            for nb in resp.json().get("value", []):
                if (nb.get("displayName") or "").casefold() == config.ONENOTE_NOTEBOOK.casefold():
                    return ((nb.get("links") or {}).get("oneNoteWebUrl") or {}).get("href")
    except requests.RequestException as exc:
        print("[onenote] notebook url lookup failed:", exc)
    return None


def _target_notebook_id(headers: dict,
                        target: dict | None) -> tuple[str | None, str]:
    if target and target.get("notebookId"):
        name = target.get("notebookName") or config.ONENOTE_NOTEBOOK
        return target["notebookId"], name
    name = (target or {}).get("notebookName") or config.ONENOTE_NOTEBOOK
    notebook_id = _find_or_create(
        headers,
        f"{GRAPH}/me/onenote/notebooks",
        f"{GRAPH}/me/onenote/notebooks",
        name, "onenote.notebook")
    return notebook_id, name


def _target_section_id(headers: dict, notebook_id: str,
                       target: dict | None) -> tuple[str | None, str]:
    if target and target.get("sectionId"):
        name = target.get("sectionName") or config.ONENOTE_SECTION
        return target["sectionId"], name
    name = (target or {}).get("sectionName") or config.ONENOTE_SECTION
    sections_url = (target or {}).get("sectionsUrl")
    list_url = (
        sections_url
        or f"{GRAPH}/me/onenote/notebooks/{notebook_id}/sections"
    )
    section_id = _find_or_create(
        headers, list_url,
        list_url,
        name, "onenote.section")
    return section_id, name


def save_to_onenote(title, started_at: datetime.datetime, ended_at: datetime.datetime,
                    attendees=None, attendees_note=None,
                    attendees_source="Teams attendance report",
                    summary=None, summary_note=None,
                    transcript_text=None, transcript_note=None,
                    transcript_segments=None, transcript_source=None,
                    target: dict | None = None,
                    page_title: str | None = None,
                    ) -> tuple[str | None, str | None]:
    """Create a OneNote page for this meeting. Returns (page_web_url, note); on any
    problem returns (None, note) and never raises, so the local transcript is safe."""
    ok, why = available()
    if not ok:
        return None, why

    token = get_token(
        interactive=True,
        include_onenote=True,
        include_sharepoint_sites=bool((target or {}).get("sectionsUrl")),
    )
    if not token:
        if (target or {}).get("sectionsUrl"):
            return None, (
                "OneNote skipped: SharePoint site notebooks require "
                "admin-approved Microsoft Graph site access."
            )
        return None, "OneNote skipped: Microsoft sign-in required (grant the Notes permission)."
    headers = {"Authorization": f"Bearer {token}"}

    notebook_id, notebook_name = _target_notebook_id(headers, target)
    if not notebook_id:
        return None, f"OneNote: could not open/create notebook '{notebook_name}'"

    section_id, section_name = _target_section_id(headers, notebook_id, target)
    if not section_id:
        return None, f"OneNote: could not open/create section '{section_name}'"

    page = _page_html(title, started_at, ended_at, attendees, attendees_note,
                      attendees_source, summary, summary_note,
                      transcript_text, transcript_note, transcript_segments,
                      transcript_source, page_title)
    pages_url = f"{GRAPH}/me/onenote/sections/{section_id}/pages"
    resp = requests.post(pages_url, headers={**headers, "Content-Type": "text/html"},
                         data=page.encode("utf-8"), timeout=_TIMEOUT)
    if resp.status_code in (200, 201):
        web_url = (resp.json().get("links", {}).get("oneNoteWebUrl", {}) or {}).get("href")
        log_graph_call("onenote.page: created", "POST", pages_url, status=resp.status_code,
                       result={"notebook": notebook_name, "section": section_name})
        return web_url, None
    log_graph_call("onenote.page: create", "POST", pages_url, status=resp.status_code,
                   error=resp.text[:300])
    return None, f"OneNote page creation failed (HTTP {resp.status_code})"
