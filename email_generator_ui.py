#!/usr/bin/env python3
"""Simple local UI for sending emails via email_generator.py logic."""

from __future__ import annotations

import csv
import json
import threading
import time
import tkinter as tk
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from zoneinfo import ZoneInfo

from email_generator import (
    read_csv_rows,
    send_all,
    validate_resume_file,
    write_csv_rows,
)

PREFS_FILE = Path(".email_ui_prefs.json")
JOB_STATE_FILE = Path(".email_ui_job_state.json")
SCHEDULER_TZ = ZoneInfo("America/Chicago")
DEFAULT_DAILY_LIMIT = 200
MIN_DAILY_LIMIT = 1
MAX_DAILY_LIMIT = 250


class EmailUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Email Generator UI")
        self.root.geometry("760x620")

        self.csv_var = tk.StringVar()
        self.body_var = tk.StringVar()
        self.resume_var = tk.StringVar()
        self.subject_var = tk.StringVar()
        self.sender_email_var = tk.StringVar()
        self.sender_password_var = tk.StringVar()
        self.smtp_host_var = tk.StringVar(value="smtp.gmail.com")
        self.smtp_port_var = tk.StringVar(value="587")
        self.daily_limit_var = tk.StringVar(value=str(DEFAULT_DAILY_LIMIT))
        self.schedule_time_var = tk.StringVar(value="09:00")
        self.schedule_mode_var = tk.StringVar(value="run_today_then_schedule")
        self.job_status_var = tk.StringVar(value="Job status: Idle")

        self.no_tls_var = tk.BooleanVar(value=False)
        self.personalize_var = tk.BooleanVar(value=False)
        self.first_only_var = tk.BooleanVar(value=False)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.scheduler_running = False
        self.scheduler_paused = False
        self.scheduler_cancelled = False

        self._build_form()
        self._setup_live_refresh()
        self._load_prefs()
        self._refresh_email_list()
        self.root.after(150, self._prompt_resume_previous_job)

    def _build_form(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        self._file_row(frame, "CSV File", self.csv_var, self._pick_csv, 0)
        self._file_row(frame, "Body File", self.body_var, self._pick_body, 1)
        self._file_row(frame, "Resume File", self.resume_var, self._pick_resume, 2)

        self._entry_row(frame, "Subject", self.subject_var, 3)
        self._entry_row(frame, "Sender Email", self.sender_email_var, 4)
        self._entry_row(frame, "Sender Password", self.sender_password_var, 5, show="*")
        self._entry_row(frame, "SMTP Host", self.smtp_host_var, 6)
        self._entry_row(frame, "SMTP Port", self.smtp_port_var, 7)
        self._entry_row(frame, f"Daily Limit ({MIN_DAILY_LIMIT}-{MAX_DAILY_LIMIT})", self.daily_limit_var, 8)
        self._entry_row(frame, "Schedule Time (CST/CDT)", self.schedule_time_var, 9)

        flags = ttk.Frame(frame)
        flags.grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 10))

        ttk.Checkbutton(flags, text="Disable TLS", variable=self.no_tls_var).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(flags, text="Personalize (name column)", variable=self.personalize_var).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(flags, text="First Only (testing)", variable=self.first_only_var).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(flags, text="Dry Run", variable=self.dry_run_var).pack(side=tk.LEFT)

        schedule_mode = ttk.Frame(frame)
        schedule_mode.grid(row=11, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(schedule_mode, text="Daily Job Mode:").pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(
            schedule_mode,
            text="Run daily limit today, then daily schedule",
            value="run_today_then_schedule",
            variable=self.schedule_mode_var,
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(
            schedule_mode,
            text="Ignore today, start on schedule",
            value="schedule_only",
            variable=self.schedule_mode_var,
        ).pack(side=tk.LEFT)

        self.send_button = ttk.Button(frame, text="Send Emails", command=self._start_send)
        self.send_button.grid(row=12, column=0, sticky="w", pady=(6, 8))
        self.job_button = ttk.Button(frame, text="Run Daily Job", command=self._start_daily_job)
        self.job_button.grid(row=12, column=1, sticky="w", pady=(6, 8))
        ttk.Button(frame, text="Refresh Preview", command=self._refresh_email_list).grid(
            row=12, column=2, sticky="w", pady=(6, 8)
        )
        self.stop_button = ttk.Button(frame, text="Stop Job", command=self._stop_daily_job, state=tk.DISABLED)
        self.stop_button.grid(row=13, column=1, sticky="w", pady=(0, 8))
        self.resume_button = ttk.Button(frame, text="Resume Job", command=self._resume_daily_job, state=tk.DISABLED)
        self.resume_button.grid(row=13, column=2, sticky="w", pady=(0, 8))
        self.cancel_button = ttk.Button(frame, text="Cancel Job", command=self._cancel_daily_job, state=tk.DISABLED)
        self.cancel_button.grid(row=13, column=0, sticky="w", pady=(0, 8))

        ttk.Label(frame, textvariable=self.job_status_var).grid(row=14, column=0, columnspan=3, sticky="w")

        ttk.Label(frame, text="Emails In CSV").grid(row=15, column=0, sticky="w")
        self.email_list = tk.Text(frame, height=10, wrap=tk.NONE)
        self.email_list.grid(row=16, column=0, columnspan=3, sticky="nsew", pady=(4, 8))
        self.email_list.configure(state=tk.DISABLED)

        email_scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.email_list.yview)
        email_scroll.grid(row=16, column=3, sticky="ns")
        self.email_list.configure(yscrollcommand=email_scroll.set)

        ttk.Label(frame, text="Sent Day Wise").grid(row=17, column=0, sticky="w")
        self.daily_stats = tk.Text(frame, height=6, wrap=tk.NONE)
        self.daily_stats.grid(row=18, column=0, columnspan=3, sticky="nsew", pady=(4, 8))
        self.daily_stats.configure(state=tk.DISABLED)

        stats_scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.daily_stats.yview)
        stats_scroll.grid(row=18, column=3, sticky="ns")
        self.daily_stats.configure(yscrollcommand=stats_scroll.set)

        ttk.Label(frame, text="Log").grid(row=19, column=0, sticky="w")
        self.log = tk.Text(frame, height=18, wrap=tk.WORD)
        self.log.grid(row=20, column=0, columnspan=3, sticky="nsew", pady=(4, 0))

        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.log.yview)
        scrollbar.grid(row=20, column=3, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(16, weight=1)
        frame.rowconfigure(18, weight=1)
        frame.rowconfigure(20, weight=1)

    def _entry_row(self, parent: ttk.Frame, label: str, variable: tk.StringVar, row: int, show: str | None = None) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=variable, width=70, show=show)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=4)

    def _file_row(self, parent: ttk.Frame, label: str, variable: tk.StringVar, picker: callable, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(parent, textvariable=variable, width=70).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=4)
        ttk.Button(parent, text="Browse", command=picker).grid(row=row, column=2, sticky="e", pady=4)

    def _pick_csv(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if path:
            self.csv_var.set(path)
            self._save_prefs()

    def _pick_body(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self.body_var.set(path)
            self._save_prefs()

    def _pick_resume(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if path:
            self.resume_var.set(path)
            self._save_prefs()

    def _append_log(self, line: str) -> None:
        self.log.insert(tk.END, line + "\n")
        self.log.see(tk.END)

    def _setup_live_refresh(self) -> None:
        self.csv_var.trace_add("write", self._on_csv_path_changed)

    def _on_csv_path_changed(self, *_: object) -> None:
        # Run on the UI loop after typing settles.
        self.root.after(50, self._refresh_email_list)

    def _set_job_status(self, text: str) -> None:
        self.root.after(0, lambda: self.job_status_var.set(f"Job status: {text}"))

    def _load_job_state(self) -> dict:
        if not JOB_STATE_FILE.exists():
            return {}
        try:
            return json.loads(JOB_STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_job_state(self, status: str, message: str = "") -> None:
        state = {
            "status": status,
            "message": message,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "csv_path": self.csv_var.get().strip(),
            "schedule_time": self.schedule_time_var.get().strip(),
            "schedule_mode": self.schedule_mode_var.get().strip(),
            "daily_limit": self.daily_limit_var.get().strip(),
        }
        JOB_STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    def _prompt_resume_previous_job(self) -> None:
        state = self._load_job_state()
        status = str(state.get("status", "")).strip().lower()
        if status not in {"running", "paused", "scheduled"}:
            return

        should_resume = messagebox.askyesno(
            "Resume Previous Job",
            "A previous job was active when the app closed.\n\n"
            f"Last status: {status}\n"
            "Do you want to resume it?",
        )
        if should_resume:
            self._append_log("Resuming previous job from cached state.")
            self._start_daily_job()
        else:
            self._save_job_state("cancelled", "User chose to start a new process.")
            self._set_job_status("Idle")

    def _next_run_time(self, now: datetime, run_hour: int, run_minute: int) -> datetime:
        run_at = now.replace(hour=run_hour, minute=run_minute, second=0, microsecond=0)
        if run_at <= now:
            run_at = run_at + timedelta(days=1)
        return run_at

    def _parse_schedule_time(self) -> tuple[int, int]:
        value = self.schedule_time_var.get().strip()
        try:
            hour_str, minute_str = value.split(":", 1)
            hour = int(hour_str)
            minute = int(minute_str)
        except ValueError as exc:
            raise ValueError("Schedule time must be HH:MM (24h).") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Schedule time must be HH:MM (24h).")
        return hour, minute

    def _count_progress(self, rows: list[dict[str, str]]) -> tuple[int, int, int | None]:
        total = len([row for row in rows if row.get("email", "").strip()])
        completed = len([row for row in rows if row.get("email", "").strip() and row.get("completed", "").strip()])
        next_pending = None
        for idx, row in enumerate(rows):
            if row.get("email", "").strip() and not row.get("completed", "").strip():
                next_pending = idx + 1
                break
        return total, completed, next_pending

    def _extract_date_key(self, value: str) -> str | None:
        completed_value = (value or "").strip()
        if not completed_value:
            return None
        if len(completed_value) >= 10 and completed_value[4] == "-" and completed_value[7] == "-":
            return completed_value[:10]
        try:
            return datetime.fromisoformat(completed_value).date().isoformat()
        except ValueError:
            return None

    def _write_daily_stats_csv(self, source_csv: Path, daily_counts: Counter[str]) -> Path:
        output_path = source_csv.with_name(f"{source_csv.stem}_daily_stats.csv")
        with output_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "sent_count"])
            for day in sorted(daily_counts.keys()):
                writer.writerow([day, daily_counts[day]])
        return output_path

    def _set_daily_stats_text(self, text: str) -> None:
        self.daily_stats.configure(state=tk.NORMAL)
        self.daily_stats.delete("1.0", tk.END)
        self.daily_stats.insert(tk.END, text)
        self.daily_stats.configure(state=tk.DISABLED)

    def _refresh_email_list(self) -> None:
        csv_value = self.csv_var.get().strip()
        self.email_list.configure(state=tk.NORMAL)
        self.email_list.delete("1.0", tk.END)
        if not csv_value:
            self.email_list.insert(tk.END, "Select a CSV file to preview emails.\n")
            self.email_list.configure(state=tk.DISABLED)
            self._set_daily_stats_text("Select a CSV file to view day-wise stats.\n")
            return

        try:
            csv_path = Path(csv_value)
            rows, _ = read_csv_rows(csv_path)
            emails = [row.get("email", "").strip() for row in rows if row.get("email", "").strip()]
            completed_count = sum(1 for row in rows if row.get("completed", "").strip())
            pending_count = max(0, len(emails) - completed_count)
            if not emails:
                self.email_list.insert(tk.END, "No valid emails found.\n")
            else:
                self.email_list.insert(tk.END, f"Total emails: {len(emails)}\n\n")
                self.email_list.insert(tk.END, f"Completed: {completed_count}\n")
                self.email_list.insert(tk.END, f"Pending: {pending_count}\n\n")
                preview_limit = 500
                for index, row in enumerate(rows[:preview_limit], start=1):
                    email = row.get("email", "").strip()
                    if not email:
                        continue
                    completed_at = row.get("completed", "").strip()
                    status = row.get("status", "").strip() or "pending"
                    if completed_at:
                        self.email_list.insert(
                            tk.END,
                            f"{index}. {email} | COMPLETED @ {completed_at} | status: {status}\n",
                        )
                    else:
                        self.email_list.insert(tk.END, f"{index}. {email} | PENDING | status: {status}\n")
                if len(emails) > preview_limit:
                    self.email_list.insert(
                        tk.END,
                        f"\n... showing first {preview_limit} of {len(emails)} emails.\n",
                    )

            daily_counts: Counter[str] = Counter()
            for row in rows:
                date_key = self._extract_date_key(row.get("completed", ""))
                if date_key:
                    daily_counts[date_key] += 1

            if not daily_counts:
                self._set_daily_stats_text("No sent history yet.\n")
            else:
                stats_lines = [f"{day}: {daily_counts[day]}" for day in sorted(daily_counts.keys())]
                stats_csv_path = self._write_daily_stats_csv(csv_path, daily_counts)
                self._set_daily_stats_text(
                    "Emails sent per day:\n"
                    + "\n".join(stats_lines)
                    + f"\n\nStats CSV: {stats_csv_path}"
                )
        except Exception as exc:
            self.email_list.insert(tk.END, f"Unable to read CSV: {exc}\n")
            self._set_daily_stats_text(f"Unable to build daily stats: {exc}\n")

        self.email_list.configure(state=tk.DISABLED)

    def _save_prefs(self) -> None:
        prefs = {
            "csv_path": self.csv_var.get().strip(),
            "body_path": self.body_var.get().strip(),
            "resume_path": self.resume_var.get().strip(),
            "subject": self.subject_var.get().strip(),
            "sender_email": self.sender_email_var.get().strip(),
            "sender_password": self.sender_password_var.get().strip(),
            "smtp_host": self.smtp_host_var.get().strip(),
            "smtp_port": self.smtp_port_var.get().strip(),
            "daily_limit": self.daily_limit_var.get().strip(),
            "schedule_time": self.schedule_time_var.get().strip(),
            "schedule_mode": self.schedule_mode_var.get().strip(),
            "no_tls": self.no_tls_var.get(),
            "personalize": self.personalize_var.get(),
            "first_only": self.first_only_var.get(),
            "dry_run": self.dry_run_var.get(),
        }
        PREFS_FILE.write_text(json.dumps(prefs, indent=2, sort_keys=True), encoding="utf-8")

    def _load_prefs(self) -> None:
        if not PREFS_FILE.exists():
            return
        try:
            prefs = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return

        self.csv_var.set(prefs.get("csv_path", ""))
        self.body_var.set(prefs.get("body_path", ""))
        self.resume_var.set(prefs.get("resume_path", ""))
        self.subject_var.set(prefs.get("subject", ""))
        self.sender_email_var.set(prefs.get("sender_email", ""))
        self.sender_password_var.set(prefs.get("sender_password", ""))
        self.smtp_host_var.set(prefs.get("smtp_host", "smtp.gmail.com"))
        self.smtp_port_var.set(str(prefs.get("smtp_port", "587")))
        self.daily_limit_var.set(str(prefs.get("daily_limit", str(DEFAULT_DAILY_LIMIT))))
        self.schedule_time_var.set(str(prefs.get("schedule_time", "09:00")))
        self.schedule_mode_var.set(str(prefs.get("schedule_mode", "run_today_then_schedule")))
        self.no_tls_var.set(bool(prefs.get("no_tls", False)))
        self.personalize_var.set(bool(prefs.get("personalize", False)))
        self.first_only_var.set(bool(prefs.get("first_only", False)))
        self.dry_run_var.set(bool(prefs.get("dry_run", False)))

    def _collect_inputs(self) -> dict:
        csv_path = Path(self.csv_var.get().strip())
        body_path = Path(self.body_var.get().strip())
        resume_path = Path(self.resume_var.get().strip())
        subject = self.subject_var.get().strip()
        sender_email = self.sender_email_var.get().strip()
        sender_password = self.sender_password_var.get().strip()
        smtp_host = self.smtp_host_var.get().strip()
        smtp_port = int(self.smtp_port_var.get().strip())
        daily_limit = int(self.daily_limit_var.get().strip())

        if not csv_path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")
        if not body_path.exists():
            raise FileNotFoundError(f"Body file not found: {body_path}")
        if not subject:
            raise ValueError("Subject is required.")
        if not sender_email:
            raise ValueError("Sender email is required.")
        if not sender_password:
            raise ValueError("Sender password is required.")
        if daily_limit < MIN_DAILY_LIMIT or daily_limit > MAX_DAILY_LIMIT:
            raise ValueError(f"Daily limit must be between {MIN_DAILY_LIMIT} and {MAX_DAILY_LIMIT}.")

        validate_resume_file(resume_path)
        body = body_path.read_text(encoding="utf-8").strip()
        if not body:
            raise ValueError("Body file is empty.")

        return {
            "csv_path": csv_path,
            "subject": subject,
            "sender_email": sender_email,
            "sender_password": sender_password,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "daily_limit": daily_limit,
            "resume_path": resume_path,
            "body": body,
        }

    def _start_send(self) -> None:
        if self.scheduler_running:
            messagebox.showinfo("Daily Job Running", "Daily job is running. Wait for it to finish.")
            return
        self._save_prefs()
        self.send_button.configure(state=tk.DISABLED)
        self.job_button.configure(state=tk.DISABLED)
        thread = threading.Thread(target=self._send, daemon=True)
        thread.start()

    def _start_daily_job(self) -> None:
        if self.scheduler_running:
            messagebox.showinfo("Already Running", "Daily job is already running.")
            return
        self._save_prefs()
        self.scheduler_running = True
        self.scheduler_paused = False
        self.scheduler_cancelled = False
        self.send_button.configure(state=tk.DISABLED)
        self.job_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.resume_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL)
        self._set_job_status("Running")
        self._save_job_state("running", "Daily job started.")
        thread = threading.Thread(target=self._run_daily_job_loop, daemon=True)
        thread.start()

    def _stop_daily_job(self) -> None:
        if not self.scheduler_running:
            return
        self.scheduler_paused = True
        self.stop_button.configure(state=tk.DISABLED)
        self.resume_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.NORMAL)
        self._set_job_status("Paused")
        self._save_job_state("paused", "Paused by user.")
        self._append_log("Daily job paused. Click Resume Job to continue or Cancel Job to stop.")

    def _resume_daily_job(self) -> None:
        if not self.scheduler_running:
            return
        self.scheduler_paused = False
        self.stop_button.configure(state=tk.NORMAL)
        self.resume_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL)
        self._set_job_status("Running")
        self._save_job_state("running", "Resumed by user.")
        self._append_log("Daily job resumed.")

    def _cancel_daily_job(self) -> None:
        if not self.scheduler_running:
            return
        self.scheduler_cancelled = True
        self.scheduler_paused = False
        self.stop_button.configure(state=tk.DISABLED)
        self.resume_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.DISABLED)
        self._set_job_status("Cancelling")
        self._save_job_state("cancelled", "Cancellation requested by user.")
        self._append_log("Cancelling daily job...")

    def _run_single_batch(self, config: dict, daily_limit: int) -> bool:
        rows, fieldnames = read_csv_rows(config["csv_path"])
        total, completed_before, next_pending = self._count_progress(rows)
        if next_pending is None:
            self.root.after(0, lambda: self._append_log("All emails are completed."))
            return False

        self.root.after(
            0,
            lambda: self._append_log(
                f"Batch start: completed {completed_before}/{total}, next row {next_pending}, limit {daily_limit}."
            ),
        )
        send_all(
            smtp_host=config["smtp_host"],
            smtp_port=config["smtp_port"],
            use_tls=not self.no_tls_var.get(),
            sender_email=config["sender_email"],
            sender_password=config["sender_password"],
            rows=rows,
            subject=config["subject"],
            base_body=config["body"],
            resume_path=config["resume_path"],
            personalize=self.personalize_var.get(),
            dry_run=False,
            daily_limit=daily_limit,
            first_only=False,
        )
        write_csv_rows(config["csv_path"], rows, fieldnames)
        total_after, completed_after, next_pending_after = self._count_progress(rows)
        self.root.after(
            0,
            lambda: self._append_log(
                f"Batch done: completed {completed_after}/{total_after}, next row {next_pending_after or 'none'}."
            ),
        )
        self.root.after(0, self._refresh_email_list)
        return next_pending_after is not None

    def _run_daily_job_loop(self) -> None:
        try:
            config = self._collect_inputs()
            run_hour, run_minute = self._parse_schedule_time()
            run_today_first = self.schedule_mode_var.get() == "run_today_then_schedule"
            self.root.after(
                0,
                lambda: self._append_log(
                    f"Daily job started. Schedule: {self.schedule_time_var.get()} America/Chicago, "
                    f"limit: {config['daily_limit']}/day."
                ),
            )

            should_continue = True
            if run_today_first:
                self._set_job_status("Running today's batch")
                self._save_job_state("running", "Running today's batch.")
                should_continue = self._run_single_batch(config, config["daily_limit"])

            while should_continue:
                now = datetime.now(SCHEDULER_TZ)
                run_at = self._next_run_time(now, run_hour, run_minute)
                wait_seconds = int((run_at - now).total_seconds())
                self._set_job_status(f"Scheduled for {run_at.strftime('%Y-%m-%d %H:%M:%S %Z')}")
                self._save_job_state("scheduled", f"Next run at {run_at.strftime('%Y-%m-%d %H:%M:%S %Z')}.")
                self.root.after(
                    0,
                    lambda: self._append_log(
                        f"Waiting until {run_at.strftime('%Y-%m-%d %H:%M:%S %Z')} for next batch."
                    ),
                )

                while wait_seconds > 0 and self.scheduler_running:
                    if self.scheduler_cancelled:
                        break
                    if self.scheduler_paused:
                        self._set_job_status("Paused")
                        self._save_job_state("paused", "Paused by user.")
                        time.sleep(1)
                        continue
                    time.sleep(min(30, wait_seconds))
                    wait_seconds -= 30

                if self.scheduler_cancelled:
                    self.root.after(0, lambda: self._append_log("Daily job cancelled by user."))
                    self._set_job_status("Cancelled")
                    self._save_job_state("cancelled", "Cancelled by user.")
                    break

                if not self.scheduler_running:
                    break

                self._set_job_status("Running scheduled batch")
                self._save_job_state("running", "Running scheduled batch.")
                should_continue = self._run_single_batch(config, config["daily_limit"])

            if should_continue is False:
                self._set_job_status("Completed")
                self._save_job_state("completed", "CSV fully completed.")
                self.root.after(0, lambda: self._append_log("Daily job finished: CSV is fully completed."))
        except Exception as exc:
            err_text = str(exc)
            self._set_job_status(f"Failed: {err_text}")
            self._save_job_state("failed", err_text)
            self.root.after(0, lambda: self._append_log(f"Daily job error: {err_text}"))
            self.root.after(0, lambda: messagebox.showerror("Daily Job Failed", err_text))
        finally:
            self.scheduler_running = False
            self.scheduler_paused = False
            self.scheduler_cancelled = False
            self.root.after(0, lambda: self.send_button.configure(state=tk.NORMAL))
            self.root.after(0, lambda: self.job_button.configure(state=tk.NORMAL))
            self.root.after(0, lambda: self.stop_button.configure(state=tk.DISABLED))
            self.root.after(0, lambda: self.resume_button.configure(state=tk.DISABLED))
            self.root.after(0, lambda: self.cancel_button.configure(state=tk.DISABLED))

    def _send(self) -> None:
        try:
            config = self._collect_inputs()
            rows, fieldnames = read_csv_rows(config["csv_path"])

            self.root.after(0, lambda: self._append_log("Starting send..."))
            send_all(
                smtp_host=config["smtp_host"],
                smtp_port=config["smtp_port"],
                use_tls=not self.no_tls_var.get(),
                sender_email=config["sender_email"],
                sender_password=config["sender_password"],
                rows=rows,
                subject=config["subject"],
                base_body=config["body"],
                resume_path=config["resume_path"],
                personalize=self.personalize_var.get(),
                dry_run=self.dry_run_var.get(),
                daily_limit=config["daily_limit"],
                first_only=self.first_only_var.get(),
            )

            if not self.dry_run_var.get():
                write_csv_rows(config["csv_path"], rows, fieldnames)
                self.root.after(0, lambda: self._append_log(f"Updated CSV: {config['csv_path']}"))
                self.root.after(0, self._refresh_email_list)

            self.root.after(0, lambda: self._append_log("Done."))
        except Exception as exc:
            err_text = str(exc)
            self.root.after(0, lambda: self._append_log(f"Error: {err_text}"))
            self.root.after(0, lambda: messagebox.showerror("Send Failed", err_text))
        finally:
            self.root.after(0, lambda: self.send_button.configure(state=tk.NORMAL))
            self.root.after(0, lambda: self.job_button.configure(state=tk.NORMAL))


def main() -> None:
    root = tk.Tk()
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    EmailUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
