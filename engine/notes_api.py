"""Local UI routes for meeting notes; derived notes are saved automatically."""
import asyncio
import json
import os
from fastapi import Request
from fastapi.responses import JSONResponse
from engine.notes_i18n import tr


def install(app):
    api = app.api

    @api.middleware("http")
    async def notes_boundary(request, call_next):
        if not request.url.path.startswith("/api/notes"):
            return await call_next(request)
        if request.method == "POST":
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if origin and origin != expected:
                return JSONResponse({"ok": False, "error": tr("Fremder Ursprung nicht erlaubt.", "Foreign origin not allowed.")}, status_code=403)
            if not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"ok": False, "error": tr("JSON erforderlich.", "JSON required.")}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    async def body(request):
        raw = await request.body()
        if len(raw) > 1000000:
            raise ValueError(tr("Eingabe zu groß.", "Input too large."))
        return json.loads(raw)

    @api.get("/api/notes")
    async def state():
        notes = app.NOTES
        notes.sweep()
        current = notes.current
        return {"ok": True, "enabled": notes.enabled, "options": notes.public_options(),
                "active": app.STATE.active, "autoStart": app.STATE.auto_start, "error": notes.error,
                "health": app.STATE.health, "autoStartSuppressed": app.STATE.autostart_suppressed,
                "capture": current.session.snapshot() if current else None,
                "currentId": current.id if current else None,
                "uiView": app.STATE.notes_view, "uiRequest": app.STATE.notes_view_request,
                "storagePath": str(notes.folder),
                "reviews": [r.public() for r in reversed(list(notes.reviews.values()))]}

    @api.get("/api/notes/devices")
    async def devices():
        from engine.speech.capture import devices as enumerate_devices
        try:
            values = await asyncio.to_thread(enumerate_devices)
            return {"ok": True, "devices": [{"name": d["name"], "loopback": bool(d.get("isLoopbackDevice")),
                    "default": bool(d.get("isSystemDefault"))} for d in values]}
        except Exception:
            return {"ok": False, "error": tr("Audiogeräte konnten nicht geladen werden.", "Audio devices could not be loaded.")}

    @api.post("/api/notes/ui-language")
    async def ui_language(request: Request):
        # App language only: allowed during a meeting, unlike device and access settings.
        notes = app.NOTES
        try:
            data = await body(request)
            notes.set_ui_language(data.get("language") if isinstance(data, dict) else None)
        except Exception:
            return {"ok": False, "error": tr("Sprache konnte nicht geändert werden.", "The language could not be changed.")}
        app._save_settings()
        await app.broadcast()
        return {"ok": True}

    @api.post("/api/notes/configure")
    async def configure(request: Request):
        notes = app.NOTES
        try:
            if notes.managed and (not notes.setup_complete or notes.setup_busy):
                return {"ok": False, "error": tr("Bitte zuerst mit Microsoft anmelden.", "Please sign in with Microsoft first.")}
            if app.STATE.active or app.STATE.notes_starting:
                return {"ok": False, "error": tr("Einstellungen erst nach Stop ändern.", "Change settings only after stopping.")}
            notes.configure(await body(request))
            if notes.enabled:
                app.STATE.live_on = False
                app._hide_window()
            app._save_settings()
            notes.save_credentials()
            await app.broadcast()
            return {"ok": True}
        except Exception:
            if notes.credential_error:
                return {"ok": False, "error": notes.credential_error}
            return {"ok": False, "error": tr("Einstellungen ungültig. Sprache und Eingaben prüfen.", "Invalid settings. Check the language and inputs.") if notes.managed else tr("Einstellungen ungültig. Direkten Azure-Endpunkt und Eingaben prüfen.", "Invalid settings. Check the direct Azure endpoint and inputs.")}

    @api.post("/api/notes/finish-setup")
    async def finish_setup(request: Request):
        notes = app.NOTES
        try:
            data = await body(request)
            if not isinstance(data, dict) or type(data.get("autoStart")) is not bool:
                raise ValueError()
        except Exception:
            return {"ok": False, "error": tr("Einstellungen ungültig. Bitte Eingaben prüfen.", "Invalid settings. Please check your inputs.")}
        # Check after reading the body; sign-in or capture may start while it arrives.
        if not notes.managed or not notes.setup_complete or notes.setup_busy:
            return {"ok": False, "error": tr("Bitte zuerst mit Microsoft anmelden.", "Please sign in with Microsoft first.")}
        if app.STATE.active or app.STATE.notes_starting:
            return {"ok": False, "error": tr("Einstellungen erst nach dem Meeting ändern.", "Change settings only after the meeting.")}
        previous = (notes.options.copy(), notes.enabled, notes.onboarding_complete, app.STATE.auto_start)
        try:
            auto_start = data.pop("autoStart")
            notes.configure(data)
            notes.onboarding_complete = True
            app.STATE.auto_start = auto_start
            app._save_settings(strict=True)
        except Exception:
            notes.options, notes.enabled, notes.onboarding_complete, app.STATE.auto_start = previous
            return {"ok": False, "error": tr("Einstellungen konnten nicht gespeichert werden. Bitte Eingaben prüfen und erneut versuchen.", "Settings could not be saved. Please check your inputs and try again.")}
        notes.error = ""
        app.STATE.autostart_suppressed = False
        if notes.enabled:
            app.STATE.live_on = False
            app._hide_window()
        await app.broadcast()
        try:
            app._hide_notes_window()
        except Exception:
            return {"ok": False, "error": tr("Einrichtung gespeichert. Bitte das Fenster über das × schließen.", "Setup saved. Please close the window using the ×.")}
        return {"ok": True}

    @api.post("/api/notes/close")
    async def close():
        app._hide_notes_window()
        return {"ok": True}

    @api.post("/api/notes/connect")
    async def connect():
        if not app.NOTES.managed:
            return {"ok": False, "error": tr("DAS-Anmeldung ist nur in der DAS-Version verfügbar.", "DAS sign-in is only available in the DAS edition.")}
        if app.STATE.active or app.STATE.notes_starting:
            return {"ok": False, "error": tr("Bitte das laufende Meeting zuerst beenden.", "Please end the current meeting first.")}
        if app.NOTES.setup_busy:
            return {"ok": False, "error": tr("Die DAS-Anmeldung läuft bereits.", "DAS sign-in is already in progress.")}
        app.NOTES.setup_busy = True
        app.NOTES.setup_complete = False
        app.NOTES.onboarding_complete = False
        try:
            from engine.graph_auth import get_token
            from engine.ai_auth import cloud_token
            from engine.notes_cloud import check_access
            token = await asyncio.to_thread(get_token, interactive=True)
            if not token:
                raise RuntimeError(tr("Microsoft-Anmeldung wurde nicht abgeschlossen.", "Microsoft sign-in was not completed."))
            await asyncio.to_thread(cloud_token, interactive=True)
            await asyncio.to_thread(check_access)
            app.NOTES.setup_complete = True
            app.NOTES.enabled = True
            app.NOTES.error = ""
            app.STATE.autostart_suppressed = False
            app._save_settings()
            await app._set_health("ok")
            return {"ok": True}
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            return {"ok": False, "error": tr("DAS-Anmeldung nicht abgeschlossen. Bitte erneut versuchen oder den DAS-Support kontaktieren.", "DAS sign-in not completed. Please try again or contact DAS support.")}
        finally:
            app.NOTES.setup_busy = False
            app._save_settings()

    @api.post("/api/notes/credentials/remove")
    async def remove_credential(request: Request):
        try:
            if app.STATE.active or app.STATE.notes_starting: raise ValueError()
            app.NOTES.remove_credential((await body(request))["key"])
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": tr("Schlüssel konnte nicht entfernt werden. Aufnahme beenden und erneut versuchen.", "The key could not be removed. Stop the recording and try again.")}

    @api.post("/api/notes/show")
    async def show():
        app._show_notes_window()
        return {"ok": True}

    @api.post("/api/notes/fit")
    async def fit(request: Request):
        try:
            data = await body(request)
            height = data.get("height")
            if type(height) is not int or not 0 < height <= 100000:
                raise ValueError("Invalid height")
            expanded = data.get("expanded")
            if "expanded" in data and type(expanded) is not bool:
                raise ValueError("Invalid expansion state")
            if expanded is None:
                app._fit_notes_window(height)
            else:
                app._fit_notes_window(height, expanded=expanded)
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": tr("Fenstergröße konnte nicht angepasst werden.", "The window size could not be adjusted.")}

    @api.post("/api/notes/open-folder")
    async def open_folder():
        try:
            app.NOTES.folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(app.NOTES.folder))
            return {"ok": True}
        except OSError:
            return {"ok": False, "error": tr("Speicherordner konnte nicht geöffnet werden.", "The storage folder could not be opened.")}

    @api.post("/api/notes/{review_id}/summarize")
    async def summarize(review_id: str, request: Request):
        notes = app.NOTES
        review = notes.reviews.get(review_id)
        if review is None:
            return {"ok": False, "error": tr("Meeting nicht mehr verfügbar.", "Meeting no longer available.")}
        try:
            await notes.summarize(review)
            await app.broadcast()
            return {"ok": not bool(review.error), "error": review.error}
        except Exception:
            return {"ok": False, "error": tr("Rohtext bereits verworfen oder Verarbeitung läuft schon.", "Raw transcript already discarded or processing already running.")}

    @api.post("/api/notes/{review_id}/onenote/targets")
    async def onenote_targets(review_id: str, request: Request):
        try:
            data = await body(request)
            result = await app.NOTES.onenote.targets(app.NOTES.reviews[review_id], app._own_domains(),
                interactive=bool(data.get("connect")), sharepoint=bool(data.get("sharepoint")))
            return {"ok": True, **result}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            return {"ok": False, "error": tr("OneNote nicht erreichbar. Bitte verbinden oder erneut versuchen.", "OneNote not reachable. Please connect or try again.")}

    @api.post("/api/notes/onenote/sections")
    async def onenote_sections(request: Request):
        try:
            data = await body(request)
            return {"ok": True, **await app.NOTES.onenote.load_sections(data["account"], data["book"])}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            return {"ok": False, "error": tr("OneNote-Abschnitte konnten nicht geladen werden.", "OneNote sections could not be loaded.")}

    @api.post("/api/notes/{review_id}/onenote/select")
    async def onenote_select(review_id: str, request: Request):
        try:
            review = app.NOTES.reviews[review_id]
            app.NOTES.onenote.select(review, await body(request), app._own_domains())
            app.NOTES.onenote.schedule(review, app._own_domains())
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            return {"ok": False, "error": tr("Speicherort konnte nicht gesichert werden. Bitte erneut versuchen.", "The storage location could not be saved. Please try again.")}

    @api.post("/api/notes/{review_id}/onenote/publish")
    async def onenote_publish(review_id: str, request: Request):
        try:
            data = await body(request)
            await app.NOTES.onenote.publish(app.NOTES.reviews[review_id], data.get("revision"))
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            return {"ok": False, "error": tr("OneNote nicht erreichbar. Ihr Entwurf bleibt lokal gesichert.", "OneNote not reachable. Your draft remains saved locally.")}

    @api.post("/api/notes/{review_id}/onenote/tasks-sync")
    async def onenote_tasks_sync(review_id: str):
        try:
            await app.NOTES.onenote.sync_tasks(app.NOTES.reviews[review_id])
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": tr("Aufgaben konnten nicht geprüft werden. Bitte erneut versuchen.", "Tasks could not be checked. Please try again.")}

    @api.post("/api/notes/{review_id}/onenote/open")
    async def onenote_open(review_id: str):
        import webbrowser
        from engine.notes_onenote import web_url
        try:
            state = app.NOTES.reviews[review_id].onenote
            url = web_url(state.get("url") or state.get("target", {}).get("webUrl"))
            if not url: raise ValueError()
            await asyncio.to_thread(webbrowser.open, url)
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": tr("OneNote-Link nicht verfügbar. Bitte OneNote direkt öffnen.", "OneNote link not available. Please open OneNote directly.")}

    @api.post("/api/notes/{review_id}/people-refresh")
    async def people_refresh(review_id: str):
        from engine.notes_people import load_people
        from engine.notes_calls import refresh_people
        review = app.NOTES.reviews.get(review_id)
        if review is None: return {"ok": False, "error": tr("Meeting nicht verfügbar.", "Meeting not available.")}
        if not review.calendar_selected: await load_people(app.NOTES, review)
        await refresh_people(app.NOTES, review)
        return {"ok": True}

    @api.post("/api/notes/{review_id}/calendar-refresh")
    async def calendar_refresh(review_id: str):
        from engine.notes_people import load_people
        review = app.NOTES.reviews.get(review_id)
        if review is None: return {"ok": False, "error": tr("Meeting nicht verfügbar.", "Meeting not available.")}
        await load_people(app.NOTES, review)
        return {"ok": True}

    @api.post("/api/notes/{review_id}/calendar-connect")
    async def calendar_connect(review_id: str):
        from engine.notes_people import SCOPES, load_people
        from engine.graph_auth import get_token_for_scopes
        review = app.NOTES.reviews.get(review_id)
        if review is None: return {"ok": False, "error": tr("Meeting nicht verfügbar.", "Meeting not available.")}
        try:
            token = await asyncio.to_thread(get_token_for_scopes, SCOPES, interactive=True)
            if not token: raise ValueError()
            token = None
            await load_people(app.NOTES, review)
            return {"ok": not review.calendar_access_needed, "error": tr("Kalenderzugriff nicht freigegeben.", "Calendar access not granted.") if review.calendar_access_needed else ""}
        except Exception:
            return {"ok": False, "error": tr("Kalenderzugriff nicht freigegeben. Microsoft-Anmeldung oder Organisationsfreigabe prüfen.", "Calendar access not granted. Check the Microsoft sign-in or your organization's approval.")}

    @api.post("/api/notes/{review_id}/calendar-select")
    async def calendar_select(review_id: str, request: Request):
        from engine.notes_people import select_calendar
        try:
            select_calendar(app.NOTES, app.NOTES.reviews[review_id], (await body(request))["id"])
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": tr("Termin konnte nicht zugeordnet werden. Personen erneut laden.", "The appointment could not be assigned. Reload people.")}

    @api.post("/api/notes/{review_id}/people-connect")
    async def people_connect(review_id: str):
        from engine.notes_calls import SCOPES, schedule_people, refresh_people
        from engine.graph_auth import get_token_for_scopes
        notes = app.NOTES
        review = notes.reviews.get(review_id)
        if review is None or review.discarded:
            return {"ok": False, "error": tr("Meeting nicht mehr verfügbar.", "Meeting no longer available.")}
        try:
            token = await asyncio.to_thread(get_token_for_scopes, SCOPES, interactive=True)
            if not token: raise ValueError("No token")
            token = None
            await refresh_people(notes, review)
            if review.people_access_needed: raise ValueError("No Teams access")
            if not review.ended: schedule_people(notes, review)
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": tr("Teams-Zugriff nicht freigegeben. Je nach Organisation muss ein Administrator Chat.Read erlauben. Personen können weiterhin manuell ergänzt werden.", "Teams access not granted. Depending on your organization, an administrator must allow Chat.Read. People can still be added manually.")}

    @api.post("/api/notes/{review_id}/copy")
    async def copy(review_id: str):
        from engine.notes_window import copy_text
        from engine.notes_markdown import render
        review = app.NOTES.reviews.get(review_id)
        if review is None or review.draft is None:
            return {"ok": False, "error": tr("Noch keine Notizen zum Kopieren vorhanden.", "No notes to copy yet.")}
        try:
            await asyncio.to_thread(copy_text, app.STATE.notes_window, render(review))
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": tr("Zwischenablage nicht verfügbar. Bitte erneut versuchen.", "Clipboard not available. Please try again.")}

    @api.post("/api/notes/{review_id}/edit")
    async def edit(review_id: str, request: Request):
        try:
            return {"ok": True, **app.NOTES.patch(review_id, await body(request))}
        except Exception:
            return {"ok": False, "error": tr("Änderung nicht übernommen. Eingabe prüfen und erneut versuchen.", "Change not applied. Check your input and try again.")}

    @api.post("/api/notes/{review_id}/save")
    async def save(review_id: str, request: Request):
        notes = app.NOTES
        try:
            result = notes.export(review_id, await body(request))
            return {"ok": True, **result}
        except Exception:
            return {"ok": False, "error": tr("Änderung nicht übernommen. Notizen eventuell anderweitig geändert oder Eingabe ungültig. Änderungen kopieren und Fenster neu öffnen.", "Change not applied. The notes may have been changed elsewhere or the input is invalid. Copy your changes and reopen the window.")}

    @api.post("/api/notes/{review_id}/discard")
    async def discard(review_id: str):
        notes = app.NOTES
        try:
            notes.discard(review_id)
            await app.broadcast()
            return {"ok": True}
        except ValueError:
            return {"ok": False, "error": tr("Bitte zuerst Aufnahme oder Verarbeitung beenden.", "Please finish the recording or processing first.")}
