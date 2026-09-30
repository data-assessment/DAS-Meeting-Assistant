"""Publish meeting notes once; synchronize later task corrections on that page.

A durable sending marker precedes POST. An ambiguous outcome is reconciled by
reading the marker back, never by repeating POST. Different clients deliberately
have different meeting UUIDs. Task patches compare remote paragraphs first;
there is no cross-client deduplication or whole-page replacement.
"""
import asyncio
import copy
import datetime as dt
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlsplit

import requests
from engine import mdfmt, suggest, notes_onenote_tasks as task_sync
from engine.graph_auth import get_token_for_scopes
from engine.notes_i18n import t
from engine.notes_schema import Draft

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["User.Read", "Notes.ReadWrite.All"]
LOCKED = ("preparing", "sending", "uncertain", "saved")


class GraphReadError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__(t("oneNote.errors.readFailed", status=status))


def graph_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com"
            or not parsed.path.startswith("/v1.0/") or parsed.fragment):
        raise ValueError(t("oneNote.errors.invalidDestination"))
    return value


def web_url(value):
    parsed = urlsplit(value or "")
    return value if parsed.scheme == "https" and parsed.netloc and not parsed.username else ""


class Graph:
    def __init__(self, interactive=False):
        token = get_token_for_scopes(SCOPES, interactive=interactive)
        if not token:
            raise ValueError(t("oneNote.errors.notConnected"))
        self.headers = {"Authorization": "Bearer " + token}
        self.user = self.get(GRAPH + "/me?$select=id,displayName,userPrincipalName").json()
        self.account = self.user["id"] + ":" + self.user.get("userPrincipalName", "").casefold()

    def get(self, url):
        response = requests.get(graph_url(url), headers=self.headers, timeout=30, allow_redirects=False)
        if response.status_code != 200:
            raise GraphReadError(response.status_code)
        return response

    def collection(self, url):
        result, seen = [], set()
        while url:
            if url in seen or len(seen) >= 100:
                raise ValueError(t("oneNote.errors.listTooLarge"))
            seen.add(url)
            data = self.get(url).json()
            result.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
        return result

    def notebooks(self):
        from engine import onenote, graph_auth
        import config
        books, warnings = [], []
        try:
            books.extend(onenote._notebook_payload(b) for b in self.collection(GRAPH + "/me/onenote/notebooks?includeSharedNotebooks=true"))
        except (GraphReadError, requests.RequestException):
            warnings.append(t("oneNote.warnings.personalNotebooks"))
        # User.Read exposes own memberships, sometimes with only type and ID.
        # Null group names/types must not hide accessible group notebooks.
        try:
            groups = {g["id"]: g for g in self.collection(GRAPH + "/me/memberOf")
                      if g.get("@odata.type") == "#microsoft.graph.group" and g.get("id")}
        except (GraphReadError, requests.RequestException):
            groups = {}
            warnings.append(t("oneNote.warnings.groupMemberships"))

        def group_books(group):
            try:
                values = self.collection(GRAPH + "/groups/" + quote(group["id"], safe="") + "/onenote/notebooks")
                result = [onenote._notebook_payload(b) for b in values]
                for book in result:
                    if group.get("displayName") and group["displayName"].casefold() not in book["name"].casefold():
                        book["label"] += " · " + group["displayName"]
                return result, False
            except GraphReadError as exc:
                # Groups without a provisioned notebook are normal.
                return [], exc.status != 404
            except requests.RequestException:
                return [], True

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(group_books, groups.values()))
        for extra, failed in results:
            books.extend(extra)
        if any(failed for _, failed in results):
            warnings.append(t("oneNote.warnings.groupNotebooks"))
        if config.ONENOTE_SITE_PATHS:
            token = graph_auth.get_token_for_scopes(SCOPES + ["Sites.Read.All"], interactive=False)
            if token:
                extra, site_warnings = onenote._site_notebooks({"Authorization": "Bearer " + token})
                books.extend(extra)
                if site_warnings: warnings.append(t("oneNote.warnings.sharePointNotebooks"))
            else:
                warnings.append(t("oneNote.warnings.sharePointConsent"))
        unique = {}
        for book in books:
            if book.get("sectionsUrl"):
                # Keep existing personal URLs so saved targets remain valid.
                unique.setdefault(book.get("id") or book["sectionsUrl"], book)
        return sorted(unique.values(), key=lambda b: b["label"].casefold()), " ".join(warnings)

    def sections(self, book):
        # Keep the service root returned by Graph, including /sites and /groups.
        result, seen = [], set()
        def walk(url, prefix="", depth=0):
            if url in seen or depth > 8 or len(seen) >= 100:
                raise ValueError(t("oneNote.errors.sectionsTooLarge"))
            seen.add(url)
            for section in self.collection(url):
                if section.get("pagesUrl") and section.get("id"):
                    result.append({"id": section["id"], "name": prefix + section.get("displayName", t("oneNote.defaults.section")),
                                   "pagesUrl": graph_url(section["pagesUrl"])})
            for group in self.collection(url.rsplit("/sections", 1)[0] + "/sectionGroups"):
                walk(group["sectionsUrl"], prefix + group.get("displayName", t("oneNote.defaults.sectionGroup")) + " › ", depth + 1)
        walk(graph_url(book["sectionsUrl"]))
        return sorted(result, key=lambda s: s["name"].casefold())

    def find(self, target, marker, attempted_at=""):
        # All pages, all pagination: titles can be changed in OneNote after POST.
        url = target["pagesUrl"] + "?$orderby=createdDateTime%20desc&$top=100"
        for page in self.collection(url):
            # Only read content from around this submission, not years of notes.
            created = page.get("createdDateTime", "")
            if attempted_at and created:
                lower = dt.datetime.fromisoformat(attempted_at) - dt.timedelta(minutes=10)
                if dt.datetime.fromisoformat(created.replace("Z", "+00:00")) < lower:
                    continue
            content = self.get(page["contentUrl"]).text
            if re.search(r'data-id=[\"\']' + re.escape(marker) + r'[\"\']', content):
                return page
        return None

    def create(self, target, content):
        return requests.post(graph_url(target["pagesUrl"]), headers=self.headers | {"Content-Type": "text/html"},
                             data=content.encode("utf-8"), timeout=45, allow_redirects=False)

    def update_tasks(self, url, commands):
        return requests.patch(graph_url(url), headers=self.headers | {"Content-Type": "application/json"},
                              json=commands, timeout=45, allow_redirects=False)


def page_html(review, author):
    draft = Draft.model_validate(review.draft).model_dump()
    esc = lambda value: html.escape(str(value or ""))
    stamp = dt.datetime.fromisoformat(review.started).strftime("%d.%m.%Y · %H:%M")
    from engine.notes_people import meeting_heading
    title = meeting_heading(review)
    parts = [f'<p data-id="meeting-{review.id}">{esc(stamp)} · {t("oneNote.page.notesBy", name=esc(author))}</p>']
    from engine.notes_people import invitation_lines
    invitation = invitation_lines(review)
    if invitation:
        parts.append("<h2>" + t("document.headings.invitation") + "</h2>")
        parts.extend("<p>" + esc(line) + "</p>" for line in invitation)
    parts.extend(["<h2>" + t("document.headings.summary") + "</h2>", mdfmt.to_html(draft["summary"])])
    for heading, key in ((t("document.headings.decisions"), "decisions"), (t("document.headings.openQuestions"), "openQuestions")):
        if draft[key]: parts.extend([f"<h2>{heading}</h2>", mdfmt.to_html(draft[key])])
    # Task sync finds this heading in every language (task_sync.task_headings).
    parts.append("<h2>" + t("document.headings.tasks") + "</h2>")
    parts.extend(task_sync.paragraph(key, line) for key, line in task_sync.snapshot(draft).items())
    if review.warning: parts.append("<p>" + t("document.labels.captureNote") + ": " + esc(review.warning) + "</p>")
    return "<!DOCTYPE html><html><head><title>" + esc(title) + "</title></head><body>" + "".join(parts) + "</body></html>"


class Publisher:
    def __init__(self, notes):
        self.notes = notes
        self.path = notes.state_folder / "onenote-preferences.json"
        self.preferences = {"mode": "local", "accounts": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if value.get("mode") in ("local", "onenote") and isinstance(value.get("accounts"), dict):
                self.preferences = value
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        # The old global mode is deliberately ignored. Only explicit customer
        # rules may choose OneNote for another meeting.
        self.preferences["mode"] = "local"
        self.catalogs, self.sections, self.locks = {}, {}, {}
        self.pending, self.checked = {}, {}

    @staticmethod
    def remembered(prefs, domain):
        value = prefs.get("domains", {}).get(domain)
        if isinstance(value, dict):
            return value.copy()
        if isinstance(value, str):
            # Migrate the former book-only rule using its remembered section.
            return {"book": value, "sectionId": prefs.get("sections", {}).get(value, "")}
        return {}

    def domains(self, review, own_domains, graph):
        own_domains = own_domains | frozenset([graph.user.get("userPrincipalName", "").split("@")[-1].casefold()])
        return self.context(review, own_domains).domains

    @staticmethod
    def complete(review):
        return bool(review.ended and review.phase == "complete" and review.draft is not None
                    and not review.busy and not review.raw_deadline)

    def schedule(self, review, own_domains):
        """Run independently of the window, with at most one worker per meeting."""
        if self.notes.closed or review.discarded or review.id in self.pending:
            return
        state = review.onenote
        if state.get("status") == "saved":
            sync = state.get("taskSync", {})
            if sync.get("status") != "pending" or review.store_error:
                return
            task = asyncio.create_task(self.sync_tasks(review, debounce=True))
            self.pending[review.id] = task
            self.notes.jobs.add(task)
            task.add_done_callback(self.notes.jobs.discard)
            task.add_done_callback(lambda _: self.pending.pop(review.id, None))
            return
        if not state.get("autoSave") or state.get("status") in ("preparing", "sending", "saved"):
            return
        key = (json.dumps(review.people, sort_keys=True), tuple(sorted(own_domains)),
               json.dumps(self.preferences, sort_keys=True), self.complete(review))
        needs_default = (state.get("selection", "default") != "manual" and not state.get("finalized")
                         and self.checked.get(review.id) != key)
        needs_save = (self.complete(review) and not state.get("error") and
                      (not state.get("finalized") or state.get("mode") == "onenote"))
        # An uncertain attempt is reconciled once per process, never posted again.
        reconcile = state.get("status") == "uncertain" and review.id not in self.checked
        if not (needs_default or needs_save or reconcile):
            return
        task = asyncio.create_task(self.process(review, own_domains, key, needs_default))
        self.pending[review.id] = task
        self.notes.jobs.add(task)
        task.add_done_callback(self.notes.jobs.discard)
        task.add_done_callback(lambda _: self.pending.pop(review.id, None))

    async def process(self, review, own_domains, key, resolve):
        try:
            if resolve:
                if await self.resolve_default(review, own_domains) is False:
                    return
            self.checked[review.id] = key
            if not self.complete(review) or review.discarded or self.notes.closed:
                return
            state = review.onenote
            if not state.get("finalized"):
                state["finalized"] = True
                if not self.notes.persist(review):
                    state["finalized"] = False
                    return
            if state.get("mode") == "onenote" and state.get("status") != "saved":
                await self.publish(review, review.revision)
        except Exception as exc:
            if not review.discarded and not self.notes.closed:
                review.onenote["error"] = str(exc) if isinstance(exc, ValueError) else t("oneNote.errors.unreachable")
                self.notes.persist(review)

    async def resolve_default(self, review, own_domains):
        state = review.onenote
        if state.get("selection") == "manual" or state.get("finalized") or state.get("status") in LOCKED:
            return
        # No cloud lookup at all when no customer has ever been remembered.
        if not any(p.get("domains") for p in self.preferences["accounts"].values()):
            if state.get("mode") == "onenote":
                state.update(mode="local", target={}, selection="default", reason="", notice="")
                return bool(self.notes.persist(review))
            return
        people = copy.deepcopy(review.people)
        preferences = copy.deepcopy(self.preferences)
        target, reason, notice = {}, "", ""
        try:
            graph = await asyncio.to_thread(Graph)
            prefs = self.preferences["accounts"].get(graph.account, {})
            domains = self.domains(review, own_domains, graph)
            rules = [self.remembered(prefs, d) for d in domains if d in prefs.get("domains", {})]
            identities = {(r.get("book"), r.get("sectionId")) for r in rules}
            if len(identities) > 1:
                notice = t("oneNote.notices.conflictingRules")
            elif rules:
                rule = rules[0]
                if not rule.get("sectionId"):
                    notice = t("oneNote.notices.missingSection")
                else:
                    sections = await asyncio.to_thread(graph.sections, {"sectionsUrl": rule["book"]})
                    section = next((s for s in sections if s["id"] == rule["sectionId"]), None)
                    if section:
                        target = rule | {"account": graph.account, "sectionName": section["name"], "pagesUrl": section["pagesUrl"]}
                        target.setdefault("bookName", t("oneNote.defaults.customerNotebook"))
                        remembered = ", ".join(d for d in domains if d in prefs.get("domains", {}))
                        reason = t("oneNote.reasons.rememberedForDomains", domains=remembered)
                    else:
                        notice = t("oneNote.notices.sectionUnavailable")
        except Exception:
            notice = t("oneNote.notices.checkFailed")
        # People and manual choices may change while Graph is loading.
        if review.onenote is not state or review.people != people or self.preferences != preferences or review.discarded or self.notes.closed:
            return False
        old = state.copy()
        state.update(mode="onenote" if target else "local", target=target, selection="customer" if target else "default", reason=reason, notice=notice)
        if not self.notes.persist(review):
            review.onenote = old
            return False

    def save_preferences(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.preferences, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    def context(self, review, own_domains):
        return suggest.MeetingContext(title=review.title, own_domains=own_domains,
            attendees=[suggest.Attendee(name=p["name"], email=p.get("email", "")) for p in review.people
                       if p.get("source") in ("calendar", "call", "manual")])

    async def targets(self, review, own_domains, interactive=False, sharepoint=False):
        if sharepoint:
            await asyncio.to_thread(get_token_for_scopes, SCOPES + ["Sites.Read.All"], interactive=True)
        graph = await asyncio.to_thread(Graph, interactive)
        books, warning = await asyncio.to_thread(graph.notebooks)
        self.catalogs[graph.account] = books
        prefs = self.preferences["accounts"].get(graph.account, {})
        own_domains = own_domains | frozenset([graph.user.get("userPrincipalName", "").split("@")[-1].casefold()])
        context = self.context(review, own_domains)
        mapping = prefs.get("domains", {})
        learned = {self.remembered(prefs, d).get("book") for d in context.domains if d in mapping}
        book, reason = "", ""
        current = review.onenote.get("target", {})
        if current.get("account") == graph.account:
            book, reason = current.get("book", ""), t("oneNote.reasons.selectedForMeeting")
        elif len(learned) == 1:
            book, reason = learned.pop(), t("oneNote.reasons.rememberedForDomain")
        else:
            candidates = suggest.by_domain(context, [b | {"id": b["sectionsUrl"]} for b in books])
            matches = {c.value for c in candidates}
            if len(matches) == 1 and len(context.domains) == 1:
                book, reason = matches.pop(), t("oneNote.reasons.matchesEmailDomain", domain=context.domains[0])
            elif not context.domains:
                book, reason = prefs.get("lastBook", ""), t("oneNote.reasons.lastUsed")
        if not any(b["sectionsUrl"] == book for b in books): book, reason = "", ""
        domain = context.domains[0] if len(context.domains) == 1 else ""
        return {"account": graph.account, "notebooks": books, "suggestedBook": book, "reason": reason,
                "domain": domain, "rememberDomain": bool(domain and book and self.remembered(prefs, domain).get("book") == book),
                "suggestedSection": self.remembered(prefs, domain).get("sectionId", "") if domain and self.remembered(prefs, domain).get("book") == book else "",
                "warning": warning}

    async def load_sections(self, account, book_url):
        book = next((b for b in self.catalogs.get(account, []) if b["sectionsUrl"] == book_url), None)
        if not book: raise ValueError(t("oneNote.errors.reloadNotebooks"))
        graph = await asyncio.to_thread(Graph)
        if graph.account != account: raise ValueError(t("oneNote.errors.accountChanged"))
        sections = await asyncio.to_thread(graph.sections, book)
        self.sections[(account, book_url)] = sections
        remembered = self.preferences["accounts"].get(account, {}).get("sections", {}).get(book_url, "")
        return {"sections": sections, "suggestedSection": remembered if any(s["id"] == remembered for s in sections) else (sections[0]["id"] if len(sections) == 1 else "")}

    def select(self, review, data, own_domains):
        if review.onenote.get("status") in LOCKED:
            raise ValueError(t("oneNote.errors.transferStarted"))
        mode = data.get("mode")
        if mode not in ("local", "onenote"): raise ValueError(t("oneNote.errors.chooseLocation"))
        target = {}
        if mode == "onenote":
            account, book_url = data.get("account"), data.get("book")
            book = next((b for b in self.catalogs.get(account, []) if b["sectionsUrl"] == book_url), None)
            section = next((s for s in self.sections.get((account, book_url), []) if s["id"] == data.get("section")), None)
            if not book or not section: raise ValueError(t("oneNote.errors.chooseNotebookAndSection"))
            target = {"account": account, "book": book_url, "bookName": book["label"], "sectionId": section["id"],
                      "sectionName": section["name"], "pagesUrl": section["pagesUrl"], "webUrl": web_url(book.get("webUrl"))}
        old = review.onenote
        review.onenote = {"mode": mode, "target": target, "status": "ready", "autoSave": True,
                          "selection": "manual", "reason": t("oneNote.reasons.chosenForMeeting"),
                          "finalized": self.complete(review)}
        if not self.notes.persist(review):
            review.onenote = old
            raise ValueError(t("oneNote.errors.locationNotSaved"))
        previous_preferences = copy.deepcopy(self.preferences)
        if target:
            prefs = self.preferences["accounts"].setdefault(target["account"], {})
            prefs["lastBook"] = target["book"]
            prefs.setdefault("sections", {})[target["book"]] = target["sectionId"]
            context = self.context(review, own_domains)
            domain = data.get("rememberDomain")
            if domain and context.domains == [domain]:
                prefs.setdefault("domains", {})[domain] = target.copy()
            forget = data.get("forgetDomain")
            if forget and context.domains == [forget] and not domain:
                prefs.setdefault("domains", {}).pop(forget, None)
        try:
            self.save_preferences()
        except OSError:
            self.preferences = previous_preferences
            review.onenote = old
            self.notes.persist(review)
            raise ValueError(t("oneNote.errors.selectionNotSaved"))

    async def publish(self, review, revision):
        try:
            await self._publish(review, revision)
        except Exception as exc:
            state = review.onenote
            if state.get("status") == "preparing": state["status"] = "ready"
            if not review.discarded and not self.notes.closed:
                state["error"] = str(exc) if isinstance(exc, ValueError) else t("oneNote.errors.unreachable")
                self.notes.persist(review)
            raise

    async def _publish(self, review, revision):
        lock = self.locks.setdefault(review.id, asyncio.Lock())
        async with lock:
            state = review.onenote
            if state.get("status") == "saved": return
            if review.discarded or self.notes.closed: raise ValueError(t("oneNote.errors.meetingUnavailable"))
            if not review.ended or review.busy or review.phase != "complete" or review.raw_deadline or review.draft is None:
                raise ValueError(t("oneNote.errors.notFinalized"))
            if type(revision) is not int or revision != review.revision:
                raise ValueError(t("oneNote.errors.notesChanged"))
            target = state.get("target", {})
            if state.get("mode") != "onenote" or not target.get("pagesUrl"):
                raise ValueError(t("oneNote.errors.chooseLocationFirst"))
            reconcile = state.get("status") in ("sending", "uncertain")
            if not reconcile:
                state.update(status="preparing", error="")
                if not self.notes.persist(review):
                    raise ValueError(t("oneNote.errors.storageUnavailable"))
            graph = await asyncio.to_thread(Graph)
            if graph.account != target["account"]: raise ValueError(t("oneNote.errors.wrongAccount"))
            if reconcile:
                page = await asyncio.to_thread(graph.find, target, "meeting-" + review.id, state.get("attemptedAt", ""))
                if page:
                    self.saved(review, page)
                    return
                state.update(status="uncertain", error=t("oneNote.errors.uncertain"))
                self.notes.persist(review)
                raise ValueError(t("oneNote.errors.uncertain"))
            # Recheck after sign-in yielded: edits or destination changes may have arrived.
            if review.revision != revision or review.onenote is not state or review.discarded or self.notes.closed:
                raise ValueError(t("oneNote.errors.notesOrLocationChanged"))
            # Validate the actual remembered section before writing, even when
            # it was selected earlier in the meeting or restored after a restart.
            sections = await asyncio.to_thread(graph.sections, {"sectionsUrl": target["book"]})
            section = next((s for s in sections if s["id"] == target["sectionId"]), None)
            if not section:
                raise ValueError(t("oneNote.errors.sectionUnavailable"))
            if review.discarded or self.notes.closed:
                raise ValueError(t("oneNote.errors.meetingUnavailable"))
            target["pagesUrl"] = section["pagesUrl"]
            content = page_html(review, graph.user.get("displayName") or graph.user.get("userPrincipalName", ""))
            state.update(status="sending", error="", taskBase=task_sync.snapshot(review.draft),
                         attemptedAt=dt.datetime.now(dt.timezone.utc).isoformat())
            if not self.notes.persist(review):
                state.update(status="ready")
                raise ValueError(t("oneNote.errors.storageUnavailable"))
            try:
                response = await asyncio.to_thread(graph.create, target, content)
                if response.status_code in (200, 201):
                    self.saved(review, response.json())
                    return
                if 400 <= response.status_code < 500 and response.status_code != 408:
                    state.update(status="ready", error=t("oneNote.errors.pageRejected", status=response.status_code))
                    self.notes.persist(review)
                    raise ValueError(state["error"])
            except (requests.RequestException, json.JSONDecodeError, KeyError):
                pass
            state.update(status="uncertain", error=t("oneNote.errors.uncertain"))
            self.notes.persist(review)
            raise ValueError(t("oneNote.errors.uncertain"))

    def saved(self, review, page):
        if not page.get("id"): raise KeyError("Missing page id")
        review.onenote.update(status="saved", pageId=page["id"], error="",
            url=web_url(page.get("links", {}).get("oneNoteWebUrl", {}).get("href", "")))
        # Legacy pending publications may not yet have a task baseline.
        review.onenote.setdefault("taskBase", task_sync.snapshot(review.draft))
        review.onenote.setdefault("taskSync", {"status": "saved"})
        if not self.notes.persist(review):
            raise ValueError(t("oneNote.errors.localStatusPending"))
        self.cleanup_document(review)

    def prepare_task_edit(self, review):
        if review.onenote.get("status") == "saved" and review.draft is not None:
            # Capture legacy pages before the first local correction.
            review.onenote.setdefault("taskBase", task_sync.snapshot(review.draft))

    def tasks_edited(self, review):
        state = review.onenote
        if state.get("status") != "saved":
            return
        sync = state.setdefault("taskSync", {"status": "saved"})
        # Never discard the reconciliation record of an in-flight/uncertain
        # write, even if more local edits arrive while Graph is responding.
        if sync.get("status") not in ("sending", "uncertain", "conflict"):
            state["taskSync"] = {"status": "pending" if state["taskBase"] != task_sync.snapshot(review.draft) else "saved"}

    @staticmethod
    def page_content_url(state):
        # Retain /sites or /groups from the validated original destination.
        root = graph_url(state["target"]["pagesUrl"]).split("/onenote/", 1)[0]
        return graph_url(root + "/onenote/pages/" + quote(state["pageId"], safe="") + "/content")

    async def sync_tasks(self, review, debounce=False):
        if debounce:
            await asyncio.sleep(0.8)
        async with self.locks.setdefault(review.id, asyncio.Lock()):
            state = review.onenote
            if review.discarded or self.notes.closed or state.get("status") != "saved" or review.store_error:
                return
            sync = state.get("taskSync", {})
            if sync.get("status") == "saved":
                return
            uncertain = sync.get("status") in ("sending", "uncertain")
            before = copy.deepcopy(state["taskBase"])
            after = copy.deepcopy(sync["attempt"]) if uncertain else task_sync.snapshot(review.draft)
            try:
                graph = await asyncio.to_thread(Graph)
                if graph.account != state["target"]["account"]:
                    raise ValueError(t("oneNote.errors.wrongAccount"))
                url = self.page_content_url(state)
                response = await asyncio.to_thread(graph.get, url + "?includeIDs=true")
                commands = task_sync.plan(response.text, review.id, before, after, reconcile=uncertain)
                if review.discarded or self.notes.closed:
                    return
                if commands:
                    state["taskSync"] = {"status": "sending", "attempt": after}
                    if not self.notes.persist(review):
                        state["taskSync"] = {"status": "pending"}
                        return
                    # Once sent, ambiguous failures permit reads only, never a
                    # blind retry (insert operations might otherwise duplicate).
                    uncertain = True
                    response = await asyncio.to_thread(graph.update_tasks, url, commands)
                    if review.discarded or self.notes.closed:
                        return  # The durable sending record is reconciled on restart.
                    if response.status_code != 204:
                        if response.status_code in (401, 403, 404, 429):
                            uncertain = False  # Request rejected before changes.
                            raise ValueError(t("oneNote.errors.tasksRejected", status=response.status_code))
                        raise ValueError(t("oneNote.errors.taskChangeUnconfirmed"))
                state["taskBase"] = after
                state["taskSync"] = {"status": "pending" if after != task_sync.snapshot(review.draft) else "saved"}
                self.notes.persist(review)
            except Exception as exc:
                status = "uncertain" if uncertain else "conflict" if isinstance(exc, task_sync.Conflict) else "error"
                error = str(exc) if isinstance(exc, ValueError) else t("oneNote.errors.tasksUnreachable")
                state["taskSync"] = {"status": status, "error": error}
                if uncertain:
                    state["taskSync"]["attempt"] = after
                self.notes.persist(review)

    def cleanup_document(self, review):
        """Delete only our unchanged MD, and only after a durable success record."""
        import hashlib
        from pathlib import Path
        if review.onenote.get("status") != "saved" or not review.document_path:
            return
        path = Path(review.document_path)
        if path.parent.resolve() != self.notes.folder.resolve():
            return
        try:
            if path.exists():
                if not review.document_hash or hashlib.sha256(path.read_bytes()).hexdigest() != review.document_hash:
                    review.onenote["localNotice"] = t("oneNote.local.changedFileKept")
                    self.notes.persist(review)
                    return
                path.unlink()
            review.document_path = ""
            review.document_hash = ""
            review.onenote.pop("localNotice", None)
            self.notes.persist(review)
        except OSError:
            review.onenote["localNotice"] = t("oneNote.local.fileNotRemoved")
            self.notes.persist(review)
