"""CLI: (re)transcribe a saved meeting WAV, optionally summarize it.

Handy for re-running failed/old recordings and for A/B-testing transcription
models (e.g. gpt-4o-transcribe-diarize vs gpt-4o-transcribe) on the same audio.
Uses the same Azure OpenAI resource/key as the app (config.AOAI_*).

Usage:
    python transcribe_cli.py recordings/<file>.wav
    python transcribe_cli.py <file>.wav --deployment gpt-4o-transcribe --language de \\
        --prompt "Teilnehmer: Robin Beispiel, Nora Muster"
    python transcribe_cli.py <file>.wav --summary          # also print a summary
    python transcribe_cli.py <file>.wav --out out.txt      # write transcript to a file
"""
import argparse
import sys

from engine.meeting import _fmt_ts
from engine.transcribe import transcribe_wav


def main() -> int:
    p = argparse.ArgumentParser(description="Transcribe a meeting WAV via Azure OpenAI.")
    p.add_argument("wav", help="path to a 16 kHz mono WAV (e.g. recordings/...wav)")
    p.add_argument(
        "--deployment",
        help="override the default TRANSCRIBE_MODELS entry (e.g. gpt-4o-transcribe)",
    )
    p.add_argument("--language", help="ISO language hint, e.g. 'de' (default: config)")
    p.add_argument("--prompt", help="bias names/jargon (non-diarize models only)")
    p.add_argument("--summary", action="store_true", help="also generate a summary")
    p.add_argument("--out", help="write the rendered transcript to this file")
    args = p.parse_args()

    res = transcribe_wav(args.wav, deployment=args.deployment,
                         language=args.language, prompt=args.prompt)
    if res.error and not res.text and not res.segments:
        print(f"transcription failed: {res.error}", file=sys.stderr)
        return 1

    lines: list[str] = []
    if res.segments:
        for s in res.segments:
            lines.append(f"[{_fmt_ts(s['start'])}] Speaker {s['speaker']}: {s['text']}")
    else:
        lines.append(res.text)
    body = "\n".join(lines)

    print(body)
    if res.note:
        print(f"\n(note: {res.note})")
    if res.error:
        print(f"\n(note: {res.error})")

    if args.summary:
        from engine.summarize import summarize
        s = summarize(res.text)
        print("\n===== SUMMARY =====")
        print(s.text if s.text else f"(unavailable — {s.error})")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(body + "\n")
        print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
