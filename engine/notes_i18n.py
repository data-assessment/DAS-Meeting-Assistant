"""App language for Meeting Notes texts: UI messages, notes documents and summaries.

Separate from the meeting (speech recognition) language. Texts live in
engine/locales/<language>.json under nested English keys, in the same format as the
frontend (frontend/src/locales): {{name}} placeholders and key_one/key_other plurals
chosen by the count parameter.

UI messages follow the current app language. A meeting's notes keep the language they
were created in: render them inside `using(review.language)`, so a later switch never
turns one document or OneNote page into a mix of both languages.

t() returns a Message: a plain str that remembers its key and parameters. Messages that
are stored (review errors, notices, OneNote reasons) can therefore be shown again in
another language with localize(), and persist() / restore() keep that across restarts.
"""
import contextlib
import contextvars
import functools
import json
import os
import re

import paths

LANGUAGES = ("de", "en")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

_language = "de"
_override = contextvars.ContextVar("notes_language", default=None)
_catalogs = {}


def _catalog(language):
    if language not in _catalogs:
        try:
            path = paths.resource_path("engine", "locales", f"{language}.json")
            _catalogs[language] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # A damaged install must still reach its own error message; keys are the last resort.
            print(f"locale catalog {language} unavailable:", exc)
            _catalogs[language] = {}
    return _catalogs[language]


def normalize(value):
    return value if value in LANGUAGES else "de"


def system_language():
    """Default app language when none is saved: the Windows display language."""
    try:
        if os.name == "nt":
            import ctypes
            return "de" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x07 else "en"
        return "de" if (os.environ.get("LC_ALL") or os.environ.get("LANG") or "").startswith("de") else "en"
    except Exception:
        return "de"


def language():
    """Language texts are rendered in now: a meeting's language inside using(), else the app language."""
    return _override.get() or _language


def app_language():
    """The app language chosen by the user, never a meeting override."""
    return _language


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
        with using(review.language):
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


class Message(str):
    """Translated text that can be translated again (see localize)."""
    __slots__ = ("key", "params")

    def __new__(cls, text, key, params):
        message = super().__new__(cls, text)
        message.key, message.params = key, params
        return message

    def __str__(self):  # str(exc) keeps the key of a translated exception message
        return self

    def __reduce__(self):  # copy.deepcopy / pickle, e.g. of a OneNote task baseline
        return Message, (str.__str__(self), self.key, self.params)


def _text(key, lang, params):
    text = None
    if type(params.get("count")) is int:
        text = _lookup(f"{key}_{'one' if params['count'] == 1 else 'other'}", lang)
    for candidate in (lang, "de"):
        if text is None:
            text = _lookup(key, candidate)
    return key if text is None else text


def t(key, /, **params):
    lang = language()
    text = _PLACEHOLDER.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0),
                            _text(key, lang, params))
    return Message(text, key, params)


def localize(value):
    """`value` in the current language: stored messages are translated again, other values kept.
    Works on nested dicts and lists, e.g. a meeting's OneNote state."""
    if isinstance(value, Message):
        return t(value.key, **{name: localize(param) for name, param in value.params.items()})
    if isinstance(value, dict):
        return {key: localize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [localize(item) for item in value]
    return value


# Stored as "message", not "key": saved notes must never contain the word "key" (secret check).
def _encode(value):
    if isinstance(value, Message):
        return {"text": str.__str__(value), "message": value.key,
                "params": {name: _encode(param) for name, param in value.params.items()}}
    return value


def _decode(value):
    if isinstance(value, dict) and set(value) == {"text", "message", "params"} and isinstance(value["params"], dict):
        return Message(value["text"], value["message"], {name: _decode(param) for name, param in value["params"].items()})
    return value


def persist(data):
    """Plain JSON data plus a list of the messages in it, so older releases still read the file."""
    found = []
    def walk(value, path):
        if isinstance(value, Message):
            found.append({"path": path, **_encode(value)})
            return str.__str__(value)
        if isinstance(value, dict):
            return {key: walk(item, path + [key]) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item, path + [index]) for index, item in enumerate(value)]
        return value
    return walk(data, []), found


def restore(data, messages):
    """Undo persist(): turn stored texts back into messages where they are unchanged."""
    for entry in messages if isinstance(messages, list) else []:
        try:
            *parents, last = entry["path"]
            node = data
            for part in parents:
                node = node[part]
            if node[last] == entry["text"]:
                node[last] = _decode({k: entry[k] for k in ("text", "message", "params")})
        except (KeyError, IndexError, TypeError, ValueError):
            continue  # a message that no longer fits the data stays plain text
    return data
