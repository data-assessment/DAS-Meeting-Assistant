"""App language for Meeting Notes texts: UI messages, notes documents and summaries.

Separate from the meeting (speech recognition) language. Texts stay inline as
tr("Deutsch", "English") pairs next to the code that uses them; the frontend uses the
same pattern (frontend/src/i18n.ts).
"""
LANGUAGES = ("de", "en")

_language = "de"


def language():
    return _language


def set_language(value):
    global _language
    _language = value if value in LANGUAGES else "de"


def tr(de, en):
    return en if _language == "en" else de
