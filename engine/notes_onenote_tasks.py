"""Task-only OneNote patches, with read/compare before writing.

Use generated Graph IDs for replacements, stable data IDs for reconciliation.
Legacy pages are matched conservatively by their original paragraph text. Never
replace the page body, summary, or an unrecognized/externally changed paragraph.
"""
import hashlib
import html
from html.parser import HTMLParser


class Conflict(ValueError):
    def __init__(self):
        super().__init__("Diese Aufgabe wurde in OneNote geändert oder konnte nicht eindeutig zugeordnet werden. "
                         "Ihre Korrektur bleibt lokal gesichert. Bitte die Aufgabe in OneNote prüfen und dort ergänzen.")


def snapshot(draft):
    lines = {}
    for task in draft["tasks"]:
        if not task["included"]:
            continue
        prefix = "task-" + hashlib.sha256(task["id"].encode()).hexdigest()[:24]
        detail = [task["title"], task["owner"] or "Verantwortlich: noch zu klären"]
        if task["due"]:
            detail.append("Termin: " + task["due"])
        lines[prefix] = {"text": " — ".join(detail), "todo": True}
        if task["recipient"]:
            lines[prefix + "-recipient"] = {"text": "Empfänger: " + task["recipient"], "todo": False}
        for question in task["questions"]:
            if question["field"] != "owner":
                answer = question["answer"] or (task["recipient"] if question["field"] == "recipient" else "") or "Noch zu klären"
                key = prefix + "-" + hashlib.sha256(question["id"].encode()).hexdigest()[:16]
                lines[key] = {"text": question["label"] + " " + answer, "todo": False}
    if not lines:
        lines["tasks-empty"] = {"text": "Keine Aufgaben ausgewählt.", "todo": False}
    return lines


def paragraph(key, line, tags=""):
    if line is None:
        tags = ""
    line = line or {"text": "", "todo": False}
    tag = tags or ("to-do" if line["todo"] else "")
    attr = ' data-tag="' + html.escape(tag, quote=True) + '"' if tag else ""
    return '<p data-id="' + key + '"' + attr + '>' + html.escape(line["text"]) + '</p>'


def normalize(text):
    return " ".join(text.split())


class Element:
    def __init__(self, tag="", attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def text(self):
        return "".join(c.text() if isinstance(c, Element) else c for c in self.children)

    def safe(self):
        # Do not erase attachments, links, tables, or unfamiliar annotations.
        return all(not isinstance(c, Element) or c.tag in ("span", "b", "strong", "i", "em", "u", "s", "br", "font") and c.safe()
                   for c in self.children)


class Page(HTMLParser):
    def __init__(self, content, meeting_id):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Element()
        self.elements = []
        self.feed(content)
        markers = [e for e in self.elements if e.attrs.get("data-id") == "meeting-" + meeting_id]
        headings = [e for e in self.elements if e.tag == "h2" and normalize(e.text()) == "Aufgaben"]
        if len(markers) != 1 or len(headings) != 1 or self.elements.index(headings[0]) < self.elements.index(markers[0]):
            raise Conflict()
        self.heading = headings[0]
        start = self.elements.index(self.heading) + 1
        end = next((i for i in range(start, len(self.elements)) if self.elements[i].tag in ("h1", "h2")), len(self.elements))
        self.paragraphs = [e for e in self.elements[start:end] if e.tag == "p"]

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs, self.current)
        self.current.children.append(node)
        self.elements.append(node)
        if tag == "br":
            node.children.append(" ")
        if tag not in ("br", "img", "meta", "hr", "input", "link", "source", "wbr"):
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)

    @staticmethod
    def matches(node, line):
        if node is None:
            return line is None
        if not node.safe():
            return False
        if line is None:
            return not normalize(node.text()) and not node.attrs.get("data-tag")
        todo = any(t.strip().startswith("to-do") for t in node.attrs.get("data-tag", "").split(","))
        return normalize(node.text()) == normalize(line["text"]) and todo == line["todo"]

    def locate(self, key, old, desired):
        tagged = [e for e in self.elements if e.attrs.get("data-id") == key]
        if tagged:
            if len(tagged) != 1 or tagged[0] not in self.paragraphs:
                raise Conflict()
            return tagged[0]
        # Releases through 0.40.5 did not assign IDs to task paragraphs.
        if old:
            candidates = [e for e in self.paragraphs if not e.attrs.get("data-id") and self.matches(e, old)]
            if not candidates and desired:
                candidates = [e for e in self.paragraphs if not e.attrs.get("data-id") and self.matches(e, desired)]
            if len(candidates) != 1:
                raise Conflict()
            return candidates[0]
        return None

    @staticmethod
    def target(node):
        value = node.attrs.get("id") if node else None
        if not value:
            raise Conflict()
        return value


def plan(content, meeting_id, before, after, *, reconcile=False):
    """Return a minimal patch or, for an uncertain write, only verify its result.

    Uncertain writes are never reissued. A read must confirm every changed line
    before another update can run. This also covers partially applied batches.
    """
    page = Page(content, meeting_id)
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    nodes, replacements, inserts = {}, [], []
    used = set()
    for key in changed:
        old, desired = before.get(key), after.get(key)
        node = page.locate(key, old, desired)
        if node is not None:
            if id(node) in used:
                raise Conflict()
            used.add(id(node))
        nodes[key] = node
        if page.matches(node, desired):
            continue
        if reconcile:
            raise ValueError("Die Aufgabenänderung ist noch nicht bestätigt. Ihre Korrektur bleibt lokal gesichert. "
                             "Bitte OneNote öffnen oder den Status später erneut prüfen. Es wird nichts erneut übertragen.")
        if not page.matches(node, old):
            raise Conflict()
        if node is not None:
            replacements.append({"target": page.target(node), "action": "replace",
                                 "content": paragraph(key, desired, node.attrs.get("data-tag", ""))})
    if reconcile:
        return []
    # Insert consecutive new paragraphs beside their preceding task paragraph.
    # Inserts precede replacements so their generated anchor IDs are still valid.
    anchor, pending = page.heading, []
    def flush():
        if pending:
            inserts.append({"target": page.target(anchor), "action": "insert", "position": "after", "content": "".join(pending)})
            pending.clear()
    for key, desired in after.items():
        if key in changed:
            node = nodes[key]
        else:
            # An unrelated externally changed task must not block an edit. Its
            # ID is useful as an insertion anchor, but never used for replacement.
            try:
                node = page.locate(key, before.get(key), desired)
            except Conflict:
                node = None
        if node is not None:
            flush()
            anchor = node
        elif key in changed:
            pending.append(paragraph(key, desired))
    flush()
    return inserts + replacements
