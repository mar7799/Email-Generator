#!/usr/bin/env python3
"""Run daily email batches until CSV recipients are completed."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from email_generator import read_csv_rows, send_all, validate_resume_file, write_csv_rows


STATE_FILE = Path(".email_scheduler_state.json")


def parse_clock(value: str) -> tuple[int, int]:
    try:
        hour_str, minute_str = value.split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
    except ValueError as exc:
        raise ValueError("--schedule-time must be in HH:MM format.") from exc

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("--schedule-time must use valid 24h time (00:00-23:59).")
    return hour, minute


def next_run_time(now: datetime, run_hour: int, run_minute: int) -> datetime:
    candidate = now.replace(hour=run_hour, minute=run_minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def get_progress(rows: list[dict[str, str]]) -> tuple[int, int, int | None]:
    total = len([row for row in rows if row.get("email", "").strip()])
    completed_indices = [idx for idx, row in enumerate(rows) if row.get("email", "").strip() and row.get("completed", "").strip()]
    completed = len(completed_indices)
    next_pending_index = None
    for idx, row in enumerate(rows):
        if row.get("email", "").strip() and not row.get("completed", "").strip():
            next_pending_index = idx
            break
    return total, completed, next_pending_index


def run_batch(args: argparse.Namespace) -> bool:
    rows, fieldnames = read_csv_rows(args.csv)
    total, completed_before, next_pending_index = get_progress(rows)

    if next_pending_index is None:
        print("CSV is fully completed. Scheduler will stop.")
        return False

    print(
        f"Starting batch. Total: {total}, completed: {completed_before}, "
        f"next row: {next_pending_index + 1}, daily limit: {args.daily_limit}."
    )

    send_all(
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        use_tls=not args.no_tls,
        sender_email=args.sender_email,
        sender_password=args.sender_password,
        rows=rows,
        subject=args.subject,
        base_body=args.body,
        resume_path=args.resume,
        personalize=args.personalize,
        dry_run=False,
        daily_limit=args.daily_limit,
        first_only=False,
    )

    write_csv_rows(args.csv, rows, fieldnames)

    total_after, completed_after, next_pending_after = get_progress(rows)

    last_sent_index = None
    last_sent_email = None
    for idx in range(len(rows) - 1, -1, -1):
        if rows[idx].get("completed", "").strip():
            last_sent_index = idx + 1
            last_sent_email = rows[idx].get("email", "").strip()
            break

    state = {
        "updated_at": datetime.now(args.tz).isoformat(timespec="seconds"),
        "last_sent_index": last_sent_index,
        "last_sent_email": last_sent_email,
        "total_recipients": total_after,
        "completed_recipients": completed_after,
        "next_pending_index": None if next_pending_after is None else (next_pending_after + 1),
    }
    save_state(args.state_file, state)

    print(
        f"Batch done. Completed: {completed_after}/{total_after}. "
        f"Next row: {('none' if next_pending_after is None else next_pending_after + 1)}"
    )

    return next_pending_after is not None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Schedule daily email sending until CSV completes.")
    parser.add_argument("--csv", required=True, type=Path, help="Recipient CSV path.")
    parser.add_argument("--resume", required=True, type=Path, help="Resume file path.")
    parser.add_argument("--body-file", required=True, type=Path, help="Email body text file path.")
    parser.add_argument("--subject", required=True, help="Email subject line.")

    parser.add_argument("--sender-email", required=True, help="Sender email address.")
    parser.add_argument("--sender-password", required=True, help="SMTP password/app password.")

    parser.add_argument("--smtp-host", default="smtp.gmail.com", help="SMTP server host.")
    parser.add_argument("--smtp-port", type=int, default=587, help="SMTP server port.")
    parser.add_argument("--no-tls", action="store_true", help="Disable STARTTLS.")

    parser.add_argument("--personalize", action="store_true", help="Enable name personalization.")
    parser.add_argument("--daily-limit", type=int, default=200, help="Max emails to send per day (default: 200).")
    parser.add_argument("--timezone", default="America/Chicago", help="IANA timezone, default America/Chicago.")
    parser.add_argument("--schedule-time", default="09:00", help="Daily run time in HH:MM (24h), default 09:00.")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run one batch immediately on startup, then continue daily schedule.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=STATE_FILE,
        help="Path to progress state file (default: .email_scheduler_state.json).",
    )

    args = parser.parse_args()

    if not args.csv.exists():
        raise FileNotFoundError(f"CSV file not found: {args.csv}")
    if not args.body_file.exists():
        raise FileNotFoundError(f"Body file not found: {args.body_file}")
    validate_resume_file(args.resume)

    if args.daily_limit <= 0:
        raise ValueError("--daily-limit must be > 0")

    args.tz = ZoneInfo(args.timezone)
    args.run_hour, args.run_minute = parse_clock(args.schedule_time)

    body = args.body_file.read_text(encoding="utf-8").strip()
    if not body:
        raise ValueError("Body file is empty.")
    args.body = body

    return args


def main() -> None:
    args = parse_args()

    state = load_state(args.state_file)
    if state:
        print(
            "Loaded state: "
            f"last_sent_index={state.get('last_sent_index')}, "
            f"next_pending_index={state.get('next_pending_index')}, "
            f"completed={state.get('completed_recipients')}/{state.get('total_recipients')}"
        )

    should_continue = True
    if args.run_now:
        should_continue = run_batch(args)

    while should_continue:
        now = datetime.now(args.tz)
        run_at = next_run_time(now, args.run_hour, args.run_minute)
        seconds = (run_at - now).total_seconds()

        print(
            f"Now: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}. "
            f"Next run: {run_at.strftime('%Y-%m-%d %H:%M:%S %Z')} "
            f"(in {int(seconds)} seconds)."
        )

        time.sleep(max(1, int(seconds)))
        should_continue = run_batch(args)

    print("All recipients completed. Exiting scheduler.")


if __name__ == "__main__":
    main()
