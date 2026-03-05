from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from email_generator import read_csv_rows, send_all, validate_resume_file, write_csv_rows

# Local run default: ./data (inside current project). Docker can override with APP_DATA_DIR=/data.
DATA_DIR = Path(os.getenv("APP_DATA_DIR", "data")).resolve()
UPLOADS_DIR = DATA_DIR / "uploads"
TZ = ZoneInfo("America/Chicago")

app = FastAPI(title="Email Generator API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def _safe_name(name: str) -> str:
    return Path(name or "upload.bin").name


async def save_upload(file: UploadFile, prefix: str) -> Path:
    ensure_dirs()
    filename = f"{prefix}_{uuid4().hex}_{_safe_name(file.filename or '')}"
    destination = UPLOADS_DIR / filename
    content = await file.read()
    destination.write_bytes(content)
    return destination


def validate_daily_limit(limit: int) -> None:
    if limit < 1 or limit > 250:
        raise HTTPException(status_code=400, detail="daily_limit must be between 1 and 250")


def validate_schedule_time(value: str) -> tuple[int, int]:
    try:
        h_str, m_str = value.split(":", 1)
        hour = int(h_str)
        minute = int(m_str)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="schedule_time must be HH:MM") from exc

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise HTTPException(status_code=400, detail="schedule_time must be HH:MM in 24h format")
    return hour, minute


def progress(rows: list[dict[str, str]]) -> dict[str, int | None]:
    total = sum(1 for row in rows if row.get("email", "").strip())
    completed = sum(1 for row in rows if row.get("email", "").strip() and row.get("completed", "").strip())
    next_pending = None
    for i, row in enumerate(rows, start=1):
        if row.get("email", "").strip() and not row.get("completed", "").strip():
            next_pending = i
            break
    return {"total": total, "completed": completed, "next_pending": next_pending}


def run_single_batch(config: dict) -> dict:
    csv_path = Path(config["csv_path"])
    rows, fieldnames = read_csv_rows(csv_path)
    before = progress(rows)

    if before["next_pending"] is None:
        return {
            "message": "CSV already completed",
            "before": before,
            "after": before,
            "csv_path": str(csv_path),
        }

    send_all(
        smtp_host=config["smtp_host"],
        smtp_port=config["smtp_port"],
        use_tls=config["use_tls"],
        sender_email=config["sender_email"],
        sender_password=config["sender_password"],
        rows=rows,
        subject=config["subject"],
        base_body=config["body"],
        resume_path=Path(config["resume_path"]),
        personalize=config["personalize"],
        dry_run=False,
        daily_limit=config["daily_limit"],
        first_only=False,
    )

    write_csv_rows(csv_path, rows, fieldnames)
    after = progress(rows)
    return {
        "message": "batch completed",
        "before": before,
        "after": after,
        "csv_path": str(csv_path),
    }


class JobState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.running = False
        self.paused = False
        self.cancelled = False
        self.config: dict | None = None
        self.run_hour = 9
        self.run_minute = 0
        self.status = "idle"
        self.last_message = ""
        self.last_run: str | None = None
        self.next_run: str | None = None

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "paused": self.paused,
                "cancelled": self.cancelled,
                "status": self.status,
                "last_message": self.last_message,
                "last_run": self.last_run,
                "next_run": self.next_run,
                "config": {
                    "csv_path": self.config.get("csv_path") if self.config else None,
                    "daily_limit": self.config.get("daily_limit") if self.config else None,
                    "schedule_time": f"{self.run_hour:02d}:{self.run_minute:02d}",
                },
            }


job = JobState()


def _next_run(now: datetime, hour: int, minute: int) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return target


def _scheduler_loop() -> None:
    while True:
        with job.lock:
            if not job.running or job.cancelled or not job.config:
                job.running = False
                if job.cancelled:
                    job.status = "cancelled"
                break
            paused = job.paused
            cfg = dict(job.config)
            run_hour = job.run_hour
            run_minute = job.run_minute

        if paused:
            with job.lock:
                job.status = "paused"
            time.sleep(1)
            continue

        now = datetime.now(TZ)
        run_at = _next_run(now, run_hour, run_minute)
        with job.lock:
            job.status = "scheduled"
            job.next_run = run_at.isoformat(timespec="seconds")
            job.last_message = f"next run at {run_at.strftime('%Y-%m-%d %H:%M:%S %Z')}"

        while datetime.now(TZ) < run_at:
            with job.lock:
                if not job.running or job.cancelled:
                    job.running = False
                    if job.cancelled:
                        job.status = "cancelled"
                    return
                if job.paused:
                    job.status = "paused"
            if job.paused:
                time.sleep(1)
                continue
            time.sleep(2)

        try:
            with job.lock:
                job.status = "running"
            result = run_single_batch(cfg)
            with job.lock:
                job.last_run = datetime.now(TZ).isoformat(timespec="seconds")
                job.last_message = result["message"]
                if result["after"]["next_pending"] is None:
                    job.running = False
                    job.status = "completed"
                    return
        except Exception as exc:
            with job.lock:
                job.running = False
                job.status = "failed"
                job.last_message = str(exc)
            return


class StartJobBody(BaseModel):
    csv_path: str
    body_path: str
    resume_path: str
    subject: str
    sender_email: str
    sender_password: str
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    use_tls: bool = True
    personalize: bool = False
    daily_limit: int = 200
    schedule_time: str = "09:00"
    mode: Literal["run_today_then_schedule", "schedule_only"] = "run_today_then_schedule"


@app.get("/health")
def health() -> dict:
    return {"ok": True, "time": datetime.now(TZ).isoformat(timespec="seconds")}


@app.post("/api/upload")
async def upload_assets(
    csv_file: UploadFile = File(...),
    body_file: UploadFile = File(...),
    resume_file: UploadFile = File(...),
) -> dict:
    csv_path = await save_upload(csv_file, "csv")
    body_path = await save_upload(body_file, "body")
    resume_path = await save_upload(resume_file, "resume")
    validate_resume_file(resume_path)

    rows, _ = read_csv_rows(csv_path)
    p = progress(rows)
    return {
        "csv_path": str(csv_path),
        "body_path": str(body_path),
        "resume_path": str(resume_path),
        "summary": p,
    }


@app.post("/api/send")
def send_once(payload: StartJobBody) -> dict:
    validate_daily_limit(payload.daily_limit)

    csv_path = Path(payload.csv_path)
    body_path = Path(payload.body_path)
    resume_path = Path(payload.resume_path)

    if not csv_path.exists() or not body_path.exists() or not resume_path.exists():
        raise HTTPException(status_code=400, detail="csv_path/body_path/resume_path must exist on server")

    validate_resume_file(resume_path)
    body = body_path.read_text(encoding="utf-8").strip()
    if not body:
        raise HTTPException(status_code=400, detail="body file is empty")

    config = {
        "csv_path": str(csv_path),
        "body": body,
        "resume_path": str(resume_path),
        "subject": payload.subject,
        "sender_email": payload.sender_email,
        "sender_password": payload.sender_password,
        "smtp_host": payload.smtp_host,
        "smtp_port": payload.smtp_port,
        "use_tls": payload.use_tls,
        "personalize": payload.personalize,
        "daily_limit": payload.daily_limit,
    }
    return run_single_batch(config)


@app.post("/api/job/start")
def start_job(payload: StartJobBody) -> dict:
    validate_daily_limit(payload.daily_limit)
    run_hour, run_minute = validate_schedule_time(payload.schedule_time)

    csv_path = Path(payload.csv_path)
    body_path = Path(payload.body_path)
    resume_path = Path(payload.resume_path)

    if not csv_path.exists() or not body_path.exists() or not resume_path.exists():
        raise HTTPException(status_code=400, detail="csv_path/body_path/resume_path must exist on server")

    validate_resume_file(resume_path)
    body = body_path.read_text(encoding="utf-8").strip()
    if not body:
        raise HTTPException(status_code=400, detail="body file is empty")

    with job.lock:
        if job.running:
            raise HTTPException(status_code=409, detail="job is already running")
        job.running = True
        job.paused = False
        job.cancelled = False
        job.run_hour = run_hour
        job.run_minute = run_minute
        job.status = "running"
        job.last_message = "job started"
        job.config = {
            "csv_path": str(csv_path),
            "body": body,
            "resume_path": str(resume_path),
            "subject": payload.subject,
            "sender_email": payload.sender_email,
            "sender_password": payload.sender_password,
            "smtp_host": payload.smtp_host,
            "smtp_port": payload.smtp_port,
            "use_tls": payload.use_tls,
            "personalize": payload.personalize,
            "daily_limit": payload.daily_limit,
        }

    if payload.mode == "run_today_then_schedule":
        try:
            result = run_single_batch(job.config)
            with job.lock:
                job.last_run = datetime.now(TZ).isoformat(timespec="seconds")
                job.last_message = "today batch completed"
                if result["after"]["next_pending"] is None:
                    job.running = False
                    job.status = "completed"
                    return {"status": "completed", "result": result, "job": job.snapshot()}
        except Exception as exc:
            with job.lock:
                job.running = False
                job.status = "failed"
                job.last_message = str(exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    with job.lock:
        job.thread = thread
    thread.start()
    return {"status": "started", "job": job.snapshot()}


@app.post("/api/job/pause")
def pause_job() -> dict:
    with job.lock:
        if not job.running:
            raise HTTPException(status_code=409, detail="job is not running")
        job.paused = True
        job.status = "paused"
        job.last_message = "paused by user"
    return {"status": "paused", "job": job.snapshot()}


@app.post("/api/job/resume")
def resume_job() -> dict:
    with job.lock:
        if not job.running:
            raise HTTPException(status_code=409, detail="job is not running")
        job.paused = False
        job.status = "running"
        job.last_message = "resumed by user"
    return {"status": "running", "job": job.snapshot()}


@app.post("/api/job/cancel")
def cancel_job() -> dict:
    with job.lock:
        if not job.running:
            raise HTTPException(status_code=409, detail="job is not running")
        job.cancelled = True
        job.paused = False
        job.status = "cancelled"
        job.last_message = "cancel requested"
    return {"status": "cancel_requested", "job": job.snapshot()}


@app.get("/api/job/status")
def job_status() -> dict:
    return job.snapshot()


@app.get("/api/csv/preview")
def csv_preview(
    csv_path: str,
    page: int = 1,
    page_size: int = 50,
    filter_status: Literal["all", "completed", "pending"] = "all",
    sort_by: Literal["index", "email", "status", "completed"] = "index",
    sort_order: Literal["asc", "desc"] = "asc",
) -> dict:
    path = Path(csv_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="csv_path not found")
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be >= 1")
    if page_size < 1 or page_size > 500:
        raise HTTPException(status_code=400, detail="page_size must be between 1 and 500")

    rows, _ = read_csv_rows(path)
    entries = []
    for idx, row in enumerate(rows, start=1):
        email = row.get("email", "").strip()
        if not email:
            continue
        entries.append(
            {
                "index": idx,
                "email": email,
                "completed": row.get("completed", "").strip(),
                "status": row.get("status", "").strip() or "pending",
            }
        )

    if filter_status == "completed":
        filtered = [e for e in entries if e["completed"]]
    elif filter_status == "pending":
        filtered = [e for e in entries if not e["completed"]]
    else:
        filtered = entries

    reverse = sort_order == "desc"
    if sort_by == "email":
        filtered.sort(key=lambda e: e["email"].lower(), reverse=reverse)
    elif sort_by == "status":
        filtered.sort(key=lambda e: e["status"].lower(), reverse=reverse)
    elif sort_by == "completed":
        filtered.sort(key=lambda e: (e["completed"] or ""), reverse=reverse)
    else:
        filtered.sort(key=lambda e: e["index"], reverse=reverse)

    total_filtered = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    out = filtered[start:end]
    total_pages = max(1, (total_filtered + page_size - 1) // page_size)

    return {
        "csv_path": str(path),
        "total": len(entries),
        "completed": sum(1 for e in entries if e["completed"]),
        "pending": sum(1 for e in entries if not e["completed"]),
        "page": page,
        "page_size": page_size,
        "total_filtered": total_filtered,
        "total_pages": total_pages,
        "filter_status": filter_status,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "emails": out,
    }
