"""Readable meeting documents; internal UI state never appears in the export."""
import datetime as dt
import re
from pathlib import Path
from engine.notes_schema import Draft

def plain(value):
    return re.sub(r"[\r\n\t]+", " ", value).strip()

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
    title = re.sub(r"\s+", " ", title).strip(" .")[:80].rstrip(" .") or "Teams-Gespräch"
    stem = stamp + " – " + title
    name, number = stem + ".md", 1
    reserved = {n.casefold() for n in reserved}
    while (folder / name).exists() or name.casefold() in reserved:
        number += 1; name = f"{stem} ({number}).md"
    return name

def render(review):
    draft = Draft.model_validate(review.draft).model_dump()
    try: started = dt.datetime.fromisoformat(review.started).strftime("%d.%m.%Y, %H:%M")
    except ValueError: started = plain(review.started)
    status = "Abgeschlossen" if review.phase == "complete" else "Vorläufig – Gespräch läuft" if not review.ended else "Vorläufig – Abschluss noch nicht vollständig"
    from engine.notes_people import meeting_heading
    lines = ["# " + plain(meeting_heading(review)), "", f"**Meeting:** {started}  ", f"**Stand:** {status}"]
    from engine.notes_people import invitation_lines
    invitation = invitation_lines(review)
    if invitation:
        lines += ["", "## Outlook-Einladung", ""] + [plain(line) + "  " for line in invitation]
    lines += ["", "## Zusammenfassung", "", draft["summary"].strip()]
    for heading, key in (("Entscheidungen", "decisions"), ("Offene Fragen", "openQuestions")):
        if draft[key].strip(): lines += ["", "## " + heading, "", draft[key].strip()]
    tasks = [t for t in draft["tasks"] if t["included"]]
    lines += ["", "## Aufgaben", ""]
    if not tasks: lines += ["Keine Aufgaben ausgewählt."]
    for task in tasks:
        lines += ["- [ ] " + plain(task["title"]), "  - Verantwortlich: " + (plain(task["owner"]) or "Noch zu klären")]
        for label, key in (("Empfänger", "recipient"), ("Termin", "due")):
            if task[key].strip(): lines += ["  - " + label + ": " + plain(task[key])]
        for q in task["questions"]:
            if q["field"] == "owner": continue
            answer = q["answer"] or (task["recipient"] if q["field"] == "recipient" else "")
            lines += ["  - " + plain(q["label"]) + " " + (plain(answer) or "Noch zu klären")]
    if review.warning: lines += ["", "> Hinweis zur Erfassung: " + plain(review.warning)]
    if review.error: lines += ["", "> Hinweis zur Verarbeitung: " + plain(review.error)]
    return "\n".join(lines).rstrip() + "\n"
