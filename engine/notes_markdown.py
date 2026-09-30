"""Readable meeting documents; internal UI state never appears in the export."""
import datetime as dt
import re
from pathlib import Path
from engine.notes_i18n import in_review_language, t
from engine.notes_schema import Draft

def plain(value):
    return re.sub(r"[\r\n\t]+", " ", value).strip()

@in_review_language
def document_name(review, folder, reserved=()):
    if review.document_name:
        name = review.document_name
        if Path(name).name != name or not name.endswith(".md") or any(c in name for c in '/\\:'):
            raise ValueError("Invalid document filename")
        return name
    from engine.notes_people import meeting_start, meeting_title
    try: stamp = meeting_start(review).strftime("%Y-%m-%d %H-%M-%S")
    except ValueError: stamp = "Meeting"
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", meeting_title(review))
    title = re.sub(r"\s+", " ", title).strip(" .")[:80].rstrip(" .") or t("document.fallbackTitle")
    stem = stamp + " – " + title
    name, number = stem + ".md", 1
    reserved = {n.casefold() for n in reserved}
    while (folder / name).exists() or name.casefold() in reserved:
        number += 1; name = f"{stem} ({number}).md"
    return name

@in_review_language
def render(review):
    draft = Draft.model_validate(review.draft).model_dump()
    try: started = dt.datetime.fromisoformat(review.started).strftime(t("document.formats.date") + ", %H:%M")
    except ValueError: started = plain(review.started)
    status = (t("document.status.complete") if review.phase == "complete"
              else t("document.status.inProgress") if not review.ended
              else t("document.status.finalizing"))
    unclear = t("document.labels.toBeClarified")
    from engine.notes_people import meeting_heading
    lines = ["# " + plain(meeting_heading(review)), "", f"**Meeting:** {started}  ", f"**{t('document.labels.status')}:** {status}"]
    from engine.notes_people import invitation_lines
    invitation = invitation_lines(review)
    if invitation:
        lines += ["", "## " + t("document.headings.invitation"), ""] + [plain(line) + "  " for line in invitation]
    lines += ["", "## " + t("document.headings.summary"), "", draft["summary"].strip()]
    for heading, key in ((t("document.headings.decisions"), "decisions"), (t("document.headings.openQuestions"), "openQuestions")):
        if draft[key].strip(): lines += ["", "## " + heading, "", draft[key].strip()]
    tasks = [task for task in draft["tasks"] if task["included"]]
    lines += ["", "## " + t("document.headings.tasks"), ""]
    if not tasks: lines += [t("document.noTasks")]
    for task in tasks:
        lines += ["- [ ] " + plain(task["title"]), "  - " + t("document.labels.owner") + ": " + (plain(task["owner"]) or unclear)]
        for label, key in ((t("document.labels.recipient"), "recipient"), (t("document.labels.due"), "due")):
            if task[key].strip(): lines += ["  - " + label + ": " + plain(task[key])]
        for q in task["questions"]:
            if q["field"] == "owner": continue
            answer = q["answer"] or (task["recipient"] if q["field"] == "recipient" else "")
            lines += ["  - " + plain(q["label"]) + " " + (plain(answer) or unclear)]
    if review.warning: lines += ["", "> " + t("document.labels.captureNote") + ": " + plain(review.warning)]
    if review.error: lines += ["", "> " + t("document.labels.processingNote") + ": " + plain(review.error)]
    return "\n".join(lines).rstrip() + "\n"
