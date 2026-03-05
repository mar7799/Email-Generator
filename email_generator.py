#!/usr/bin/env python3
"""Standalone email sender from CSV with resume attachment.

CSV format (minimum):
email
person1@example.com
person2@example.com

Optional columns:
- name: used for personalization when --personalize is enabled
- body: per-recipient body override (falls back to --body-file content)
- completed: timestamp set automatically after a successful send
- status: result of latest send attempt (success/failed)
"""

from __future__ import annotations

import argparse
import csv
import mimetypes
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path


def validate_resume_file(resume_path: Path) -> None:
    if not resume_path.exists():
        raise FileNotFoundError(f"Resume file not found: {resume_path}")
    if not resume_path.is_file():
        raise ValueError(f"Resume path is not a file: {resume_path}")
    if resume_path.stat().st_size == 0:
        raise ValueError(f"Resume file is empty: {resume_path}")


def read_csv_rows(csv_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        raw_rows = list(csv.reader(f))
        if not raw_rows:
            raise ValueError("CSV has no rows.")

        first_row = [cell.strip() for cell in raw_rows[0]]
        is_headered = "email" in [cell.lower() for cell in first_row]

        rows: list[dict[str, str]] = []
        if is_headered:
            f.seek(0)
            reader = csv.DictReader(f)
            fieldnames = [str(h).strip().lower() for h in (reader.fieldnames or []) if h is not None]
            for row in reader:
                normalized = {str(k).strip().lower(): (v or "").strip() for k, v in row.items()}
                normalized.setdefault("completed", "")
                normalized.setdefault("status", "")
                rows.append(normalized)
        else:
            # Support plain files where each line is just an email address.
            fieldnames = ["email", "completed", "status"]
            for row in raw_rows:
                email = (row[0] if row else "").strip()
                if not email:
                    continue
                rows.append({"email": email, "completed": "", "status": ""})

        if "completed" not in fieldnames:
            fieldnames.append("completed")
        if "status" not in fieldnames:
            fieldnames.append("status")

        if not any(row.get("email", "") for row in rows):
            raise ValueError("No valid recipient emails found in CSV.")

        return rows, fieldnames


def write_csv_rows(csv_path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def read_email_list(raw_emails: str) -> list[dict[str, str]]:
    emails = [item.strip() for item in raw_emails.split(",") if item.strip()]
    if not emails:
        raise ValueError("No valid emails provided in --emails.")
    return [{"email": email} for email in emails]


def build_message(
    sender_email: str,
    recipient_email: str,
    subject: str,
    body: str,
    resume_path: Path,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg["Subject"] = subject
    msg.set_content(body)

    data = resume_path.read_bytes()
    mime_type, _ = mimetypes.guess_type(str(resume_path))
    if mime_type:
        maintype, subtype = mime_type.split("/", 1)
    else:
        maintype, subtype = "application", "octet-stream"

    msg.add_attachment(
        data,
        maintype=maintype,
        subtype=subtype,
        filename=resume_path.name,
    )
    return msg


def maybe_personalize(base_body: str, row: dict[str, str], personalize: bool) -> str:
    row_override = row.get("body", "")
    if row_override:
        return row_override

    if personalize:
        name = row.get("name", "")
        if name:
            return f"Hi {name},\n\n{base_body}"

    return base_body


def send_all(
    smtp_host: str,
    smtp_port: int,
    use_tls: bool,
    sender_email: str,
    sender_password: str,
    rows: list[dict[str, str]],
    subject: str,
    base_body: str,
    resume_path: Path,
    personalize: bool,
    dry_run: bool,
    daily_limit: int,
    first_only: bool,
) -> None:
    if dry_run:
        print("[DRY RUN] No emails sent. Preview recipients:")
        shown = 0
        for row in rows:
            if row.get("email", "") and not row.get("completed", ""):
                print(f" - {row.get('email', '')}")
                shown += 1
                if first_only and shown >= 1:
                    break
        print(f"[DRY RUN] Will send up to {daily_limit} pending emails in a run.")
        return

    sent_this_run = 0
    attempted_this_run = 0
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if use_tls:
            server.starttls()
        server.login(sender_email, sender_password)

        for row in rows:
            recipient = row.get("email", "")
            if not recipient:
                continue
            if row.get("completed", ""):
                continue
            if sent_this_run >= daily_limit:
                print(
                    f"Reached run limit: {daily_limit}. "
                    f"Sent in this run: {sent_this_run}."
                )
                break
            if first_only and attempted_this_run >= 1:
                break

            attempted_this_run += 1
            body = maybe_personalize(base_body, row, personalize)
            msg = build_message(sender_email, recipient, subject, body, resume_path)
            try:
                server.send_message(msg)
            except Exception as exc:
                row["status"] = f"failed: {str(exc).strip()[:180]}"
                print(f"Failed: {recipient} ({exc})")
                continue

            row["completed"] = datetime.now().isoformat(timespec="seconds")
            row["status"] = "success"
            sent_this_run += 1
            print(f"Sent: {recipient}")

    print(
        f"Run finished. Sent: {sent_this_run}. Run limit: {daily_limit}."
    )



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send resume emails from CSV or inline email list.")
    parser.add_argument("--csv", help="Path to CSV file with at least an email column.")
    parser.add_argument(
        "--emails",
        help="Comma-separated recipient emails for quick testing. Example: a@x.com,b@y.com",
    )
    parser.add_argument("--resume", required=True, help="Path to resume file to attach.")
    parser.add_argument("--body-file", required=True, help="Path to text file containing email body.")
    parser.add_argument("--subject", required=True, help="Email subject line.")

    parser.add_argument("--smtp-host", default="smtp.gmail.com", help="SMTP server host.")
    parser.add_argument("--smtp-port", type=int, default=587, help="SMTP server port.")
    parser.add_argument("--no-tls", action="store_true", help="Disable STARTTLS.")

    parser.add_argument("--sender-email", required=True, help="Your sender email address.")
    parser.add_argument("--sender-password", required=True, help="SMTP password / app password.")

    parser.add_argument("--personalize", action="store_true", help="Add 'Hi <name>' when name column exists.")
    parser.add_argument("--dry-run", action="store_true", help="Preview recipients without sending.")
    parser.add_argument(
        "--daily-limit",
        type=int,
        default=300,
        help="Maximum emails to send per day (default: 300).",
    )
    parser.add_argument(
        "--first-only",
        action="store_true",
        help="When using --csv, attempt only the first pending email row.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    resume_path = Path(args.resume)
    body_path = Path(args.body_file)

    if not args.csv and not args.emails:
        raise ValueError("Provide either --csv or --emails.")
    if args.csv and args.emails:
        raise ValueError("Use only one input source: --csv or --emails.")

    validate_resume_file(resume_path)
    if not body_path.exists():
        raise FileNotFoundError(f"Body file not found: {body_path}")

    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")
        rows, csv_fieldnames = read_csv_rows(csv_path)
    else:
        rows = read_email_list(args.emails)
        csv_path = None
        csv_fieldnames = []

    body = body_path.read_text(encoding="utf-8").strip()
    if not body:
        raise ValueError("Body file is empty.")

    send_all(
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        use_tls=not args.no_tls,
        sender_email=args.sender_email,
        sender_password=args.sender_password,
        rows=rows,
        subject=args.subject,
        base_body=body,
        resume_path=resume_path,
        personalize=args.personalize,
        dry_run=args.dry_run,
        daily_limit=args.daily_limit,
        first_only=args.first_only,
    )

    if csv_path and not args.dry_run:
        write_csv_rows(csv_path, rows, csv_fieldnames)


if __name__ == "__main__":
    main()
