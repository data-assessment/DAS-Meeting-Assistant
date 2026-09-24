"""Field-level user edits and stable task reconciliation across AI updates."""
import copy
import hashlib
import json
import re
from difflib import SequenceMatcher
from engine.notes_schema import Draft, Person

TASK_FIELDS = {"title", "owner", "ownerId", "recipient", "due", "questions", "included"}
TEXT_FIELDS = {"summary", "decisions", "openQuestions"}

def normalized(title):
    return " ".join(re.findall(r"\w+", title.casefold()))

def merge_generated(review, generated):
    incoming = copy.deepcopy(generated)
    if not review.draft:
        review.draft = incoming
        for task in incoming["tasks"]:
            review.task_titles[task["id"]] = [task["title"]]
        return
    old = review.draft
    remaining = list(incoming["tasks"])
    merged = []
    for previous in old["tasks"]:
        identity = previous["id"]
        titles = review.task_titles.setdefault(identity, [previous["title"]])
        match = next((t for t in remaining if t["id"] == identity), None)
        if match is None:
            exact = [t for t in remaining if normalized(t["title"]) in {normalized(v) for v in titles}]
            if len(exact) == 1:
                match = exact[0]
        if match is None:
            # Conservative fallback for model wording changes; ambiguous tasks stay separate.
            scores = sorted([(max(SequenceMatcher(None, normalized(v), normalized(t["title"])).ratio() for v in titles), i) for i,t in enumerate(remaining)], reverse=True)
            if scores and scores[0][0] >= .86 and (len(scores) == 1 or scores[0][0] - scores[1][0] >= .12):
                match = remaining[scores[0][1]]
        overrides = review.task_edits.get(identity, {})
        if match is not None:
            remaining.remove(match)
            if match["title"] not in titles: titles.append(match["title"])
            task = match | {"id": identity, "suggested": True}
            task.update(copy.deepcopy(overrides))
        else:
            # Keep rows stable (also while an editor has focus). Withdrawn proposals
            # are no longer selected unless the user explicitly changed this task.
            task = copy.deepcopy(previous)
            task["suggested"] = False
            if not overrides: task["included"] = False
        merged.append(task)
    for task in remaining:
        if len(merged) >= 40:
            review.warning = "Weitere Aufgabenvorschläge konnten nicht aufgenommen werden: maximal 40 pro Gespräch."
            break
        if task["id"] in {t["id"] for t in merged}: continue
        review.task_titles[task["id"]] = [task["title"]]
        merged.append(task)
    incoming["tasks"] = merged
    incoming["people"] = old.get("people", [])
    review.draft = Draft.model_validate(incoming).model_dump()

def apply_edit(review, data):
    if review.discarded or review.draft is None or not isinstance(data, dict):
        raise ValueError("Notizen nicht verfügbar")
    if set(data) - {"operationId", "tasks", "people", "text"}:
        raise ValueError("Unbekanntes Feld")
    operation = data.get("operationId")
    if not isinstance(operation, str) or not 1 <= len(operation) <= 100:
        raise ValueError("Operation erforderlich")
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()
    if operation in review.edit_operations:
        if review.edit_operations[operation] != digest: raise ValueError("Operation verändert")
        return
    draft = copy.deepcopy(review.draft)
    overrides = copy.deepcopy(review.task_edits)
    patches = data.get("tasks", {})
    if not isinstance(patches, dict) or len(patches) > 40: raise ValueError("Ungültige Aufgaben")
    for identity, patch in patches.items():
        task = next((t for t in draft["tasks"] if t["id"] == identity), None)
        if task is None or not isinstance(patch, dict) or set(patch) - TASK_FIELDS:
            raise ValueError("Ungültige Aufgabe")
        task.update(patch)
        overrides.setdefault(identity, {}).update(copy.deepcopy(patch))
    if "people" in data:
        values = data["people"]
        if not isinstance(values, list) or len(values) > 100: raise ValueError("Ungültige Personen")
        people = {p["id"]: p for p in draft["people"]}
        for value in values:
            person = Person.model_validate(value).model_dump()
            if person["source"] != "manual": raise ValueError("Nur manuelle Ergänzungen")
            people[person["id"]] = person
        draft["people"] = list(people.values())
    if "text" in data:
        if not review.public()["editable"]: raise ValueError("Zusammenfassung wird aktualisiert")
        if not isinstance(data["text"], dict) or set(data["text"]) != TEXT_FIELDS:
            raise ValueError("Ungültige Zusammenfassung")
        draft.update(data["text"])
    draft = Draft.model_validate(draft).model_dump()
    review.draft, review.task_edits, review.edited = draft, overrides, True
    review.revision += 1
    review.edit_operations[operation] = digest
    # Covers HTTP retries without retaining an unbounded editing history.
    while len(review.edit_operations) > 2000:
        review.edit_operations.pop(next(iter(review.edit_operations)))
