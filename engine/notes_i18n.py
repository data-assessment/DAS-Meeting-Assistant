"""App language for Meeting Notes texts: UI messages, notes documents and summaries.

Separate from the meeting (speech recognition) language. Texts live in
engine/locales/<language>.json under nested English keys, in the same format as the
frontend (frontend/src/locales): {{name}} placeholders and key_one/key_other plurals
chosen by the count parameter.

UI messages follow the current app language. A meeting's notes keep the language they
were created in: render them inside `using(review.language)`, so a later switch never
turns one document or OneNote page into a mix of both languages.
"""
import contextlib
import contextvars
import functools
import json
import re

import paths

LANGUAGES = ("de", "en")

_language = "de"
_override = contextvars.ContextVar("notes_language", default=None)
_catalogs = {}


def _catalog(language):
    if language not in _catalogs:
        path = paths.resource_path("engine", "locales", f"{language}.json")
        _catalogs[language] = json.loads(path.read_text(encoding="utf-8"))
    return _catalogs[language]


def normalize(value):
    return value if value in LANGUAGES else "de"


def language():
    return _override.get() or _language


def set_language(value):
    global _language
    _language = normalize(value)


@contextlib.contextmanager
def using(value):
    """Render texts in `value` (a meeting's language) regardless of the app language."""
    token = _override.set(normalize(value))
    try:
        yield
    finally:
        _override.reset(token)


def in_review_language(render):
    """Decorator for functions that render a meeting passed as their first argument."""
    @functools.wraps(render)
    def wrapper(review, *args, **kwargs):
        with using(getattr(review, "language", "de")):
            return render(review, *args, **kwargs)
    return wrapper


def _lookup(key, lang=None):
    node = _catalog(lang or language())
    for part in key.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node if isinstance(node, str) else None


def variants(key):
    """The text of `key` in every language, for recognizing saved documents written in any of them."""
    return tuple(dict.fromkeys(text for lang in LANGUAGES if (text := _lookup(key, lang))))


def t(key, **params):
    text = None
    if isinstance(params.get("count"), int):
        text = _lookup(f"{key}_{'one' if params['count'] == 1 else 'other'}")
    if text is None:
        text = _lookup(key)
    if text is None:
        text = key
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), text)
