"""Tiny Markdown renderer used for the summary on platforms that don't parse Markdown:
OneNote expects HTML. Supports just what our meeting summaries
use — headings (#..######), unordered lists (- / *), task lists (- [ ] / - [x]),
**bold**, and paragraphs — so '###', '[ ]' etc. render natively instead of showing as
literal text. Not a full Markdown implementation.
"""
import html
import re

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_TASK = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.*)$")   # must be tried before _BULLET
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _blocks(md: str) -> list[tuple]:
    """Parse into ('heading',(level,text)) | ('list',[items]) |
    ('tasks',[(checked, text)]) | ('para', text)."""
    out: list[tuple] = []
    para: list[str] = []
    items: list[str] | None = None
    tasks: list[tuple[bool, str]] | None = None

    def flush_para():
        if para:
            out.append(("para", " ".join(para).strip()))
            para.clear()

    def flush_list():
        nonlocal items
        if items is not None:
            out.append(("list", items))
            items = None

    def flush_tasks():
        nonlocal tasks
        if tasks is not None:
            out.append(("tasks", tasks))
            tasks = None

    for raw in (md or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        heading = _HEADING.match(line)
        task = _TASK.match(line)
        bullet = _BULLET.match(line)
        if heading:
            flush_para(); flush_list(); flush_tasks()
            out.append(("heading", (len(heading.group(1)), heading.group(2).strip())))
        elif task:
            flush_para(); flush_list()
            if tasks is None:
                tasks = []
            tasks.append((task.group(1) in ("x", "X"), task.group(2).strip()))
        elif bullet:
            flush_para(); flush_tasks()
            if items is None:
                items = []
            items.append(bullet.group(1).strip())
        elif not line:
            flush_para(); flush_list(); flush_tasks()
        else:
            flush_list(); flush_tasks()
            para.append(line)
    flush_para(); flush_list(); flush_tasks()
    return out


def _inline_html(text: str) -> str:
    return _BOLD.sub(r"<strong>\1</strong>", html.escape(text))


def to_html(md: str) -> str:
    parts = []
    for kind, data in _blocks(md):
        if kind == "heading":
            level, text = data
            parts.append(f"<h{level}>{_inline_html(text)}</h{level}>")
        elif kind == "list":
            parts.append("<ul>" + "".join(f"<li>{_inline_html(it)}</li>" for it in data) + "</ul>")
        elif kind == "tasks":
            # OneNote note tag: data-tag="to-do" renders a checkbox (":completed" = ticked).
            parts.append("".join(
                f'<p data-tag="{"to-do:completed" if checked else "to-do"}">{_inline_html(text)}</p>'
                for checked, text in data))
        else:
            parts.append(f"<p>{_inline_html(data)}</p>")
    return "".join(parts)
