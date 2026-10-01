"""Local UI routes for meeting notes; derived notes are saved automatically."""
import asyncio
import json
import os
from fastapi import Request
from fastapi.responses import JSONResponse
from engine import notes_i18n
from engine.notes_i18n import localize, t


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
                return JSONResponse({"ok": False, "error": t("api.errors.foreignOrigin")}, status_code=403)
            if not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"ok": False, "error": t("api.errors.jsonRequired")}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    async def body(request):
        raw = await request.body()
        if len(raw) > 1000000:
            raise ValueError(t("api.errors.inputTooLarge"))
        return json.loads(raw)

    @api.get("/api/notes")
    async def state():
        notes = app.NOTES
        notes.sweep()
        current = notes.current
        return {"ok": True, "enabled": notes.enabled, "options": notes.public_options(),
                "active": app.STATE.active, "autoStart": app.STATE.auto_start, "error": localize(notes.error),
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
            return {"ok": False, "error": t("api.errors.audioDevicesLoadFailed")}

    @api.post("/api/notes/ui-language")
    async def ui_language(request: Request):
        # App language only: allowed during a meeting, unlike device and access settings.
        notes = app.NOTES
        previous = notes_i18n.app_language()
        try:
            data = await body(request)
            notes.set_ui_language(data.get("language") if isinstance(data, dict) else None)
        except Exception:
            return {"ok": False, "error": t("api.errors.languageChangeFailed")}
        try:
            app._save_settings(strict=True)
        except Exception:
            # Unsaved, the choice would silently be lost at the next start.
            notes.set_ui_language(previous)
            return {"ok": False, "error": t("api.errors.languageNotSaved")}
        await app.broadcast()
        return {"ok": True}

    @api.post("/api/notes/configure")
    async def configure(request: Request):
        notes = app.NOTES
        try:
            if notes.managed and (not notes.setup_complete or notes.setup_busy):
                return {"ok": False, "error": t("api.errors.signInFirst")}
            if app.STATE.active or app.STATE.notes_starting:
                return {"ok": False, "error": t("settings.errors.changeAfterStop")}
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
                return {"ok": False, "error": localize(notes.credential_error)}
            return {"ok": False, "error": t("api.errors.invalidSettingsManaged") if notes.managed else t("api.errors.invalidSettingsDirect")}

    @api.post("/api/notes/finish-setup")
    async def finish_setup(request: Request):
        notes = app.NOTES
        try:
            data = await body(request)
            if not isinstance(data, dict) or type(data.get("autoStart")) is not bool:
                raise ValueError()
        except Exception:
            return {"ok": False, "error": t("api.errors.invalidSettings")}
        # Check after reading the body; sign-in or capture may start while it arrives.
        if not notes.managed or not notes.setup_complete or notes.setup_busy:
            return {"ok": False, "error": t("api.errors.signInFirst")}
        if app.STATE.active or app.STATE.notes_starting:
            return {"ok": False, "error": t("api.errors.changeAfterMeeting")}
        previous = (notes.options.copy(), notes.enabled, notes.onboarding_complete, app.STATE.auto_start)
        try:
            auto_start = data.pop("autoStart")
            notes.configure(data)
            notes.onboarding_complete = True
            app.STATE.auto_start = auto_start
            app._save_settings(strict=True)
        except Exception:
            notes.options, notes.enabled, notes.onboarding_complete, app.STATE.auto_start = previous
            return {"ok": False, "error": t("api.errors.settingsSaveFailed")}
        notes.error = ""
        app.STATE.autostart_suppressed = False
        if notes.enabled:
            app.STATE.live_on = False
            app._hide_window()
        await app.broadcast()
        try:
            app._hide_notes_window()
        except Exception:
            return {"ok": False, "error": t("api.errors.setupSaved")}
        return {"ok": True}

    @api.post("/api/notes/close")
    async def close():
        app._hide_notes_window()
        return {"ok": True}

    @api.post("/api/notes/connect")
    async def connect():
        if not app.NOTES.managed:
            return {"ok": False, "error": t("api.errors.dasSignInUnavailable")}
        if app.STATE.active or app.STATE.notes_starting:
            return {"ok": False, "error": t("api.errors.endMeetingFirst")}
        if app.NOTES.setup_busy:
            return {"ok": False, "error": t("api.errors.dasSignInRunning")}
        app.NOTES.setup_busy = True
        app.NOTES.setup_complete = False
        app.NOTES.onboarding_complete = False
        try:
            from engine.graph_auth import get_token
            from engine.ai_auth import cloud_token
            from engine.notes_cloud import check_access
            token = await asyncio.to_thread(get_token, interactive=True)
            if not token:
                raise RuntimeError(t("api.errors.microsoftSignInIncomplete"))
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
            return {"ok": False, "error": t("api.errors.dasSignInFailed")}
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
            return {"ok": False, "error": t("api.errors.keyRemoveFailed")}

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
            return {"ok": False, "error": t("api.errors.windowResizeFailed")}

    @api.post("/api/notes/open-folder")
    async def open_folder():
        try:
            app.NOTES.folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(app.NOTES.folder))
            return {"ok": True}
        except OSError:
            return {"ok": False, "error": t("api.errors.folderOpenFailed")}

    @api.post("/api/notes/{review_id}/summarize")
    async def summarize(review_id: str, request: Request):
        notes = app.NOTES
        review = notes.reviews.get(review_id)
        if review is None:
            return {"ok": False, "error": t("api.errors.meetingGone")}
        try:
            await notes.summarize(review)
            await app.broadcast()
            return {"ok": not bool(review.error), "error": review.error}
        except Exception:
            return {"ok": False, "error": t("api.errors.rawAlreadyDiscarded")}

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
            return {"ok": False, "error": t("api.errors.oneNoteUnreachable")}

    @api.post("/api/notes/onenote/sections")
    async def onenote_sections(request: Request):
        try:
            data = await body(request)
            return {"ok": True, **await app.NOTES.onenote.load_sections(data["account"], data["book"])}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            return {"ok": False, "error": t("api.errors.oneNoteSectionsFailed")}

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
            return {"ok": False, "error": t("api.errors.storageSaveFailed")}

    @api.post("/api/notes/{review_id}/onenote/publish")
    async def onenote_publish(review_id: str, request: Request):
        try:
            data = await body(request)
            await app.NOTES.onenote.publish(app.NOTES.reviews[review_id], data.get("revision"))
            return {"ok": True}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            return {"ok": False, "error": t("api.errors.oneNoteUnreachableDraftKept")}

    @api.post("/api/notes/{review_id}/onenote/tasks-sync")
    async def onenote_tasks_sync(review_id: str):
        try:
            await app.NOTES.onenote.sync_tasks(app.NOTES.reviews[review_id])
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": t("api.errors.tasksCheckFailed")}

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
            return {"ok": False, "error": t("api.errors.oneNoteLinkUnavailable")}

    @api.post("/api/notes/{review_id}/people-refresh")
    async def people_refresh(review_id: str):
        from engine.notes_people import load_people
        from engine.notes_calls import refresh_people
        review = app.NOTES.reviews.get(review_id)
        if review is None: return {"ok": False, "error": t("api.errors.meetingUnavailable")}
        if not review.calendar_selected: await load_people(app.NOTES, review)
        await refresh_people(app.NOTES, review)
        return {"ok": True}

    @api.post("/api/notes/{review_id}/calendar-refresh")
    async def calendar_refresh(review_id: str):
        from engine.notes_people import load_people
        review = app.NOTES.reviews.get(review_id)
        if review is None: return {"ok": False, "error": t("api.errors.meetingUnavailable")}
        await load_people(app.NOTES, review)
        return {"ok": True}

    @api.post("/api/notes/{review_id}/calendar-connect")
    async def calendar_connect(review_id: str):
        from engine.notes_people import SCOPES, load_people
        from engine.graph_auth import get_token_for_scopes
        review = app.NOTES.reviews.get(review_id)
        if review is None: return {"ok": False, "error": t("api.errors.meetingUnavailable")}
        try:
            token = await asyncio.to_thread(get_token_for_scopes, SCOPES, interactive=True)
            if not token: raise ValueError()
            token = None
            await load_people(app.NOTES, review)
            return {"ok": not review.calendar_access_needed, "error": t("api.errors.calendarAccess") if review.calendar_access_needed else ""}
        except Exception:
            return {"ok": False, "error": t("api.errors.calendarAccessCheck")}

    @api.post("/api/notes/{review_id}/calendar-select")
    async def calendar_select(review_id: str, request: Request):
        from engine.notes_people import select_calendar
        try:
            select_calendar(app.NOTES, app.NOTES.reviews[review_id], (await body(request))["id"])
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": t("api.errors.appointmentAssignFailed")}

    @api.post("/api/notes/{review_id}/people-connect")
    async def people_connect(review_id: str):
        from engine.notes_calls import SCOPES, schedule_people, refresh_people
        from engine.graph_auth import get_token_for_scopes
        notes = app.NOTES
        review = notes.reviews.get(review_id)
        if review is None or review.discarded:
            return {"ok": False, "error": t("api.errors.meetingGone")}
        try:
            token = await asyncio.to_thread(get_token_for_scopes, SCOPES, interactive=True)
            if not token: raise ValueError("No token")
            token = None
            await refresh_people(notes, review)
            if review.people_access_needed: raise ValueError("No Teams access")
            if not review.ended: schedule_people(notes, review)
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": t("api.errors.teamsAccess")}

    @api.post("/api/notes/{review_id}/copy")
    async def copy(review_id: str):
        from engine.notes_window import copy_text
        from engine.notes_markdown import render
        review = app.NOTES.reviews.get(review_id)
        if review is None or review.draft is None:
            return {"ok": False, "error": t("api.errors.nothingToCopy")}
        try:
            await asyncio.to_thread(copy_text, app.STATE.notes_window, render(review))
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": t("api.errors.clipboardUnavailable")}

    @api.post("/api/notes/{review_id}/edit")
    async def edit(review_id: str, request: Request):
        try:
            return {"ok": True, **app.NOTES.patch(review_id, await body(request))}
        except Exception:
            return {"ok": False, "error": t("api.errors.changeNotApplied")}

    @api.post("/api/notes/{review_id}/save")
    async def save(review_id: str, request: Request):
        notes = app.NOTES
        try:
            result = notes.export(review_id, await body(request))
            return {"ok": True, **result}
        except Exception:
            return {"ok": False, "error": t("api.errors.changeConflict")}

    @api.post("/api/notes/{review_id}/discard")
    async def discard(review_id: str):
        notes = app.NOTES
        try:
            notes.discard(review_id)
            await app.broadcast()
            return {"ok": True}
        except ValueError:
            return {"ok": False, "error": t("api.errors.finishFirst")}
