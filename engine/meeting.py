"""Transcript writer: Markdown metadata, attendees, summary, and body."""
import datetime
import json
import os
import re

import config


def _split_sentences(text: str) -> list[str]:
    """Split a plain transcript blob into sentence-ish lines."""
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _fmt_duration(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def _fmt_ts(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def is_diarize_label(speaker) -> bool:
    """A short token like 'A'/'B'/'?' is a diarization label, not a name."""
    return bool(speaker) and len(str(speaker)) <= 2


def seg_label(speaker) -> str | None:
    """Display label for a segment: 'Speaker A' for diarization, the name for a
    Teams transcript, or None when there's no speaker (whisper)."""
    if not speaker:
        return None
    return f"Speaker {speaker}" if is_diarize_label(speaker) else str(speaker)


def _write_attendees(
    f,
    attendees: list[dict] | None,
    attendees_note: str | None,
    source: str = "Teams attendance report",
) -> None:
    f.write(f"## Attendees\n\n*Source: {source}.*\n\n")
    if attendees:
        for a in attendees:
            email = f" <{a['email']}>" if a.get("email") else ""
            extra = ", ".join(
                part for part in (
                    a.get("role"),
                    _fmt_duration(a["seconds"]) if a.get("seconds") else "",
                )
                if part
            )
            suffix = f" — {extra}" if extra else ""
            f.write(f"- **{a.get('name', '(unknown)')}**{email}{suffix}\n")
    else:
        f.write(f"_None — {attendees_note or 'no attendees available'}._\n")
    f.write("\n")


def _needs_part_markers(segments: list[dict]) -> bool:
    diarized = any(is_diarize_label(s.get("speaker")) for s in segments)
    return diarized and len({s.get("chunk", 0) for s in segments}) > 1


def _write_segment_line(f, seg: dict) -> None:
    ts = _fmt_ts(seg.get("start", 0))
    label = seg_label(seg.get("speaker"))
    body = (seg.get("text") or "").strip()
    if label:
        f.write(f"**`[{ts}]` {label}:** {body}\n\n")
    else:
        f.write(f"**`[{ts}]`** {body}\n\n")


def _write_segments(f, segments: list[dict], note: str | None = None) -> None:
    multi = _needs_part_markers(segments)
    current_chunk = None
    for seg in segments:
        chunk = seg.get("chunk", 0)
        if multi and chunk != current_chunk:
            f.write(f"\n### Part {chunk + 1} _(speaker labels reset)_\n\n")
            current_chunk = chunk
        _write_segment_line(f, seg)
    if note:
        f.write(f"_Note: {note}_\n")


def _write_text_transcript(f, text: str) -> None:
    # Cleaned text is one utterance per line; a raw blob is one line, so
    # sentence-split it. Either way, render as bullets like the live view.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        lines = _split_sentences(text)
    for line in lines:
        f.write(f"- {line}\n")


def _write_transcript_body(f, *, text=None, note=None, segments=None,
                           source=None) -> None:
    if source:
        f.write(f"*Source: {source}.*\n\n")
    if segments:
        _write_segments(f, segments, note)
    elif text:
        _write_text_transcript(f, text)
    else:
        f.write(f"_Unavailable — {note or 'speech-to-text not run'}._\n")


def write_transcript(
    title: str,
    started_at: datetime.datetime,
    ended_at: datetime.datetime,
    attendees: list[dict] | None = None,
    attendees_note: str | None = None,
    attendees_source: str = "Teams attendance report",
    meeting_id: str | None = None,
    audio_path: str | None = None,
    transcript_text: str | None = None,
    transcript_note: str | None = None,
    transcript_segments: list[dict] | None = None,
    transcript_source: str | None = None,
    extras: dict | None = None,
) -> str:
    extras = extras or {}
    transcript_versions = extras.get("transcript_versions") or []
    summary = extras.get("summary")
    summary_note = extras.get("summary_note")
    os.makedirs(config.TRANSCRIPT_DIR, exist_ok=True)
    duration = ended_at - started_at
    slug = "".join(
        c for c in title if c.isalnum() or c in " -_"
    ).strip().replace(" ", "_") or "meeting"
    fname = f"{started_at:%Y-%m-%d_%H%M}_{slug}.md"
    path = os.path.join(config.TRANSCRIPT_DIR, fname)

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")

        # Machine-readable identity for the export dialogs: which recurring
        # meeting this was, so a target chosen once can be offered again next
        # time. An HTML comment stays invisible in rendered Markdown, and one
        # line of JSON means new fields never break older parsers.
        meta = {k: v for k, v in (extras.get("meta") or {}).items() if v}
        if meta:
            blob = json.dumps(meta, ensure_ascii=False)
            f.write(f"<!-- vt-meta {blob} -->\n\n")

        # Metadata as a table (renders nicely in Markdown viewers).
        f.write("| | |\n|---|---|\n")
        f.write(f"| **Start** | {started_at:%Y-%m-%d %H:%M:%S} |\n")
        f.write(f"| **End** | {ended_at:%Y-%m-%d %H:%M:%S} |\n")
        f.write(f"| **Duration** | {duration} |\n")
        if audio_path:
            f.write(f"| **Audio** | `{audio_path}` |\n")
        if meeting_id:
            # Pass this to attendance_cli.py to fetch the report manually.
            f.write(f"| **Meeting ID** | `{meeting_id}` |\n")
        f.write("\n")

        _write_attendees(f, attendees, attendees_note, attendees_source)

        if summary:
            f.write("## Summary\n\n")
            f.write(summary.rstrip("\n") + "\n\n")
        elif summary_note:
            f.write(f"## Summary\n\n_Unavailable — {summary_note}._\n\n")

        f.write("## Transcript\n\n")
        if transcript_versions:
            title = transcript_source or "Primary transcript"
            f.write(f"### {title}\n\n")
        _write_transcript_body(
            f,
            text=transcript_text,
            note=transcript_note,
            segments=transcript_segments,
            source=transcript_source,
        )
        for version in transcript_versions or []:
            heading = version.get("title") or "Transcript version"
            f.write(f"\n### {heading}\n\n")
            _write_transcript_body(
                f,
                text=version.get("text"),
                note=version.get("note"),
                segments=version.get("segments"),
                source=version.get("source"),
            )
    return path
