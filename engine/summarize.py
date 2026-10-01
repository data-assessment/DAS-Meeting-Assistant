"""Summarize a meeting transcript with a deployed Azure OpenAI chat model.

Reuses the same resource/key as transcription (config.AOAI_*). Optional step,
gated by config.SUMMARIZE. The transcript comes from speech-to-text and may
contain recognition errors, so the prompt tells the model to interpret sensibly
and not invent content.
"""
from dataclasses import dataclass

import config
from engine import prompts

try:
    from openai import AzureOpenAI
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    AzureOpenAI = None
    _IMPORT_ERROR = exc


@dataclass
class SummaryResult:
    text: str = ""
    error: str | None = None


def _enabled() -> tuple[bool, str | None]:
    if not config.SUMMARIZE:
        return False, "summary disabled (SUMMARIZE=false)"
    return _chat_available()


def _chat_available() -> tuple[bool, str | None]:
    if _IMPORT_ERROR is not None:
        return False, f"openai SDK unavailable: {_IMPORT_ERROR}"
    from engine.ai_auth import available
    return available()


def _chat(messages: list[dict]) -> tuple[str, str | None]:
    """Run a chat completion, failing over to the second resource on error."""
    last = None
    for i, p in enumerate(config.chat_providers()):
        try:
            from engine.ai_auth import openai_auth_kwargs
            client = AzureOpenAI(azure_endpoint=p["endpoint"],
                                 api_version=p["api_version"],
                                 **openai_auth_kwargs(p))
            resp = client.chat.completions.create(model=p["deployment"], messages=messages)
            usage = getattr(resp, "usage", None)
            try:
                from engine import usage as usage_meter
                usage_meter.record(
                    "chat",
                    p["deployment"],
                    input_tokens=getattr(usage, "prompt_tokens", 0),
                    output_tokens=getattr(usage, "completion_tokens", 0),
                    total_tokens=getattr(usage, "total_tokens", 0),
                    request_id=getattr(resp, "_request_id", ""),
                )
            except Exception as exc:
                print("usage metering failed:", exc)
            return (resp.choices[0].message.content or "").strip(), None
        except Exception as exc:
            last = exc
            more = " — trying fallback" if i + 1 < len(config.chat_providers()) else ""
            print(f"chat failed on {p['deployment']}@{p['endpoint']}: {exc}{more}")
    return "", f"chat failed (all providers): {last}"


def clean_transcript(text: str, attendees: list[dict] | None = None) -> tuple[str, str | None]:
    """Clean a raw plain-text transcript: drop hallucinated gibberish and best-effort
    attribute lines to speakers from context. Returns (cleaned, error); on failure
    returns the original text so nothing is lost."""
    ok, why = _chat_available()
    if not ok:
        return text, why
    if not text or not text.strip():
        return text, None
    names = ", ".join(a.get("name", "") for a in attendees) if attendees else "unknown"
    instruction = (
        "You clean up a raw speech-to-text meeting transcript. Rules:\n"
        "- Keep the original language(s); do NOT translate.\n"
        "- For clearly garbled or hallucinated passages — e.g. words or "
        "characters from a script/language that don't fit nearby speech "
        "(random CJK, gibberish) — repair them only when the intended "
        "statement is clear from nearby context. If it is not clear, replace "
        "the passage with the placeholder <unintelligible>.\n"
        "- Prefix a line with an attendee's real NAME ONLY when you are "
        "genuinely sure who spoke (clear evidence: a self-introduction, "
        f"being addressed by name and replying, etc.). Known attendees: "
        f"{names}. Do NOT guess — when the speaker is uncertain, prefix the "
        "line with the neutral placeholder 'Speaker:' instead of a name. "
        "Always prefer 'Speaker:' over a name you're not confident about.\n"
        "- One utterance per line. Do not add, remove, summarize or invent "
        "content beyond confident garble repair and placeholder "
        "substitution.\n"
        "Output only the cleaned transcript."
    )
    out, err = transform(text, instruction)
    return (out, None) if (out and not err) else (text, err)


def transform(text: str, instruction: str) -> tuple[str, str | None]:
    """Run a one-shot chat transform (translate / correct) over text. Returns
    (result, error). Independent of SUMMARIZE — used by the popup buttons."""
    ok, why = _chat_available()
    if not ok:
        return "", why
    if not text or not text.strip():
        return "", "nothing to transform"
    return _chat([{"role": "system", "content": instruction},
                  {"role": "user", "content": text}])


def summarize(transcript_text: str, attendees: list[dict] | None = None,
              language: str | None = None, local_speaker_labels: bool = False) -> SummaryResult:
    """Summarize a transcript. Returns the markdown summary, or an error string.

    local_speaker_labels=True (multi-chunk transcripts) adds a caveat that speaker
    labels reset at each "--- part N ---" boundary and aren't consistent across them.
    """
    ok, why = _enabled()
    if not ok:
        return SummaryResult(error=why)
    if not transcript_text or not transcript_text.strip():
        return SummaryResult(error="no transcript to summarize")

    system = prompts.summary_system(language or config.SUMMARY_LANGUAGE, local_speaker_labels)
    user = prompts.summary_user(transcript_text, attendees)

    out, err = _chat([{"role": "system", "content": system},
                      {"role": "user", "content": user}])
    return SummaryResult(text=out, error=err)
