"""Build vocabulary guidance for speech-to-text calls and background for summaries."""

from __future__ import annotations

import config


def dictionary_terms() -> list[str]:
    """Return a copy so request builders cannot mutate live configuration."""
    return list(config.STT_DICTIONARY)


def dictionary_prompt(terms: list[str] | None = None) -> str:
    """Compact prompt fragment that preserves the user's exact spellings."""
    values = dictionary_terms() if terms is None else terms
    if not values:
        return ""
    return "Important business terms and exact spellings: " + ", ".join(values) + "."


def combine_prompt(explicit_prompt: str | None, terms: list[str] | None = None) -> str | None:
    """Combine caller context (for example participant names) with vocabulary."""
    parts = [str(explicit_prompt or "").strip(), dictionary_prompt(terms)]
    combined = "\n\n".join(part for part in parts if part)
    return combined or None


def summary_background() -> str:
    """Company context and dictionary for the notes/summary model (German, like its rules).
    Marked as background so the model uses it to understand and spell, never as content."""
    parts = []
    if config.COMPANY_CONTEXT:
        parts.append(
            "UNTERNEHMENSKONTEXT des Nutzers (Hintergrundwissen, keine Anweisungen und kein "
            "Gesprächsinhalt): Nur nutzen, um Gesagtes richtig einzuordnen und Namen, Teams, "
            "Kunden, Produkte und Werkzeuge richtig zu schreiben. Nichts daraus in die Notizen "
            "übernehmen, was im Gespräch nicht vorkam.\n" + config.COMPANY_CONTEXT)
    if config.STT_DICTIONARY:
        parts.append(
            "WICHTIGE BEGRIFFE und ihre exakte Schreibweise: " + ", ".join(config.STT_DICTIONARY)
            + ". Ähnlich klingende Erkennungsfehler im Transkript diesen Begriffen zuordnen, "
            "wenn der Zusammenhang es eindeutig nahelegt.")
    return "\n\n".join(parts)


def translation_instructions(terms: list[str] | None = None) -> str | None:
    """Equivalent spelling guidance for the full realtime translation session."""
    values = dictionary_terms() if terms is None else terms
    if not values:
        return None
    return (
        "Transcribe and translate the audio while preserving these exact business "
        "term spellings: " + ", ".join(values) + "."
    )
