"""CLI: fetch a Teams attendance report by online-meeting id (delegated Graph).

Uses the same auth + token cache as the app (engine.graph_auth), so the first
call may prompt you to sign in. Requires the OnlineMeetingArtifact.Read.All scope
(set FETCH_ATTENDEES=true so it's requested) and that you organized the meeting.

Usage:
    python attendance_cli.py <meeting-id>
    python attendance_cli.py --join-url "<teams join url>"   # resolve id first
    python attendance_cli.py <meeting-id> --json             # raw JSON of every report

The Meeting ID is written into each transcript (when resolvable) as "Meeting ID: ...".
"""
import argparse
import json
import sys

import requests

from engine import attendance


def _fmt_duration(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def _print_report(headers: dict, meeting_id: str, report: dict) -> None:
    full = attendance.get_attendance_report(headers, meeting_id, report["id"])
    records = full.get("attendanceRecords", [])
    print(f"\nReport {report['id']}")
    print(f"  session: {full.get('meetingStartDateTime', '?')} -> {full.get('meetingEndDateTime', '?')}")
    print(f"  participants: {full.get('totalParticipantCount', len(records))}")
    if not records:
        print("  (no attendance records — session may not have ended yet)")
        return
    for rec in records:
        ident = (rec.get("identity") or {}).get("displayName") or "(unknown)"
        email = rec.get("emailAddress") or ""
        role = rec.get("role") or ""
        secs = rec.get("totalAttendanceInSeconds") or 0
        print(f"  - {ident} <{email}>  {role}  {_fmt_duration(secs)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a Teams attendance report by meeting id.")
    parser.add_argument("meeting_id", nargs="?", help="onlineMeeting id (from the transcript's 'Meeting ID:')")
    parser.add_argument("--join-url", help="resolve the meeting id from a Teams join URL instead")
    parser.add_argument("--json", action="store_true", help="dump raw JSON of every report (with records)")
    args = parser.parse_args()

    if not args.meeting_id and not args.join_url:
        parser.error("provide a meeting id, or --join-url to resolve one")

    try:
        headers = attendance.graph_headers()

        meeting_id = args.meeting_id
        if not meeting_id:
            meeting_id = attendance.resolve_meeting_id(headers, args.join_url)
            if not meeting_id:
                print("No online meeting found for that join URL (organized by you?).", file=sys.stderr)
                return 2
            print(f"Resolved meeting id: {meeting_id}")

        reports = attendance.list_attendance_reports(headers, meeting_id)
    except requests.HTTPError as exc:
        print(f"Graph request failed: {exc}", file=sys.stderr)
        body = getattr(exc.response, "text", "")
        if body:
            print(body, file=sys.stderr)
        return 1

    if not reports:
        print("No attendance reports yet. The report is generated only when the meeting "
              "session ends (last participant leaves) — not when you leave or when the "
              "scheduled slot ends. Try again after the meeting is fully over.")
        return 0

    if args.json:
        full = [attendance.get_attendance_report(headers, meeting_id, r["id"]) for r in reports]
        print(json.dumps(full, indent=2))
        return 0

    print(f"{len(reports)} report(s) for meeting {meeting_id}:")
    for report in reports:
        _print_report(headers, meeting_id, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
