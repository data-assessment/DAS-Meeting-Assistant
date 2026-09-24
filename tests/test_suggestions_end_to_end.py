"""Pre-selection through the real dialog endpoints.

Covers what the user actually experiences: opening a dialog after a meeting and
finding the right target already chosen, with a reason attached, and having a
correction stick for the next occurrence of that meeting.
"""
import datetime

from fastapi.testclient import TestClient

import app
ATTENDEES_SECTION = """## Attendees

*Source: Teams attendance report.*

- **Ben Beispiel** <ben@company.example> — Organizer
- **Anna Beck** <anna@contoso.com> — Attendee

"""


def _transcript(monkeypatch, *, series="series-42", title="Contoso Weekly",
                attendees=ATTENDEES_SECTION, body=""):
    """A saved transcript, parsed exactly as the app parses one from disk."""
    text = f"# {title}\n\n"
    if series:
        text += f'<!-- vt-meta {{"seriesId": "{series}"}} -->\n\n'
    text += attendees
    text += "## Summary\n\nWe discussed the rollout.\n\n"
    text += body
    started = datetime.datetime(2026, 8, 14, 10, 0)
    parsed = _parse_text(text, title, started)
    monkeypatch.setattr(app, "_select_transcript", lambda *_: parsed)
    monkeypatch.setattr(app, "_list_transcripts", lambda *_a, **_k: [parsed])
    return parsed


def _parse_text(text, title, started):
    """Run the real parser over in-memory content."""
    attendees, _note, _source = app._parse_transcript_attendees(text)
    attendees = attendees or []
    return {
        "id": "t1",
        "title": title,
        "path": "kickoff.md",
        "summary": "We discussed the rollout.",
        "startedAt": started,
        "endedAt": started + datetime.timedelta(minutes=30),
        "attendees": attendees,
        "emails": sorted(
            {a["email"] for a in attendees if a.get("email")}
        ),
        "meta": app._parse_transcript_meta(text),
    }


# --- OneNote ------------------------------------------------------------- #
def _wire_onenote(monkeypatch, notebooks, sections):
    monkeypatch.setattr(app.config, "ONENOTE_ENABLED", True, raising=False)
    monkeypatch.setattr(app.config, "ONENOTE_NOTEBOOK", "Voice Transcriber",
                        raising=False)
    monkeypatch.setattr(app.config, "ONENOTE_SECTION", "Transcripts",
                        raising=False)
    monkeypatch.setattr(app.STATE, "onenote_choices", {"sections": {}},
                        raising=False)

    async def notebooks_loader(refresh=False):
        return notebooks, None

    async def sections_loader(notebook_id="", sections_url="", refresh=False):
        return sections, None

    monkeypatch.setattr(app, "_load_onenote_notebooks", notebooks_loader)
    monkeypatch.setattr(app, "_load_onenote_sections", sections_loader)
    monkeypatch.setattr(app, "_onenote_page_title_for_transcript",
                        lambda *_: "Contoso Weekly")


def test_onenote_notebook_matches_the_customer_domain(monkeypatch):
    _transcript(monkeypatch)
    _wire_onenote(
        monkeypatch,
        [{"id": "nb1", "name": "Internal"},
         {"id": "nb2", "name": "Contoso"}],
        [{"id": "s1", "name": "Transcripts"}],
    )

    body = TestClient(app.api).get("/api/onenote/targets").json()

    assert body["selectedNotebookId"] == "nb2"
    assert "contoso.com" in body["notebookReason"]


def test_onenote_falls_back_to_the_configured_notebook(monkeypatch):
    _transcript(monkeypatch, attendees=(
        "## Attendees\n\n*Source: Teams attendance report.*\n\n"
        "- **Ben Beispiel** <ben@company.example> — Organizer\n\n"
    ))
    _wire_onenote(
        monkeypatch,
        [{"id": "nb1", "name": "Internal"},
         {"id": "nb2", "name": "Voice Transcriber"}],
        [{"id": "s1", "name": "Transcripts"}],
    )

    body = TestClient(app.api).get("/api/onenote/targets").json()

    assert body["selectedNotebookId"] == "nb2"
    assert "ONENOTE_NOTEBOOK" in body["notebookReason"]


def test_onenote_remembers_notebook_and_section_per_meeting(monkeypatch):
    _transcript(monkeypatch)
    _wire_onenote(
        monkeypatch,
        [{"id": "nb1", "name": "Internal"},
         {"id": "nb2", "name": "Contoso"}],
        [{"id": "s1", "name": "Transcripts"},
         {"id": "s2", "name": "Customer calls"}],
    )
    client = TestClient(app.api)

    client.post("/api/onenote/choice", json={
        "notebookId": "nb1", "sectionName": "Customer calls",
        "transcriptId": "t1",
    })

    body = client.get("/api/onenote/targets").json()
    assert body["selectedNotebookId"] == "nb1"
    assert "recurring meeting" in body["notebookReason"]
    assert body["sectionSuggestion"]["name"] == "Customer calls"
    assert "recurring meeting" in body["sectionSuggestion"]["reason"]
