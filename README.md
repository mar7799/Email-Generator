# Email Generator

A local email campaign tool with:
- Python email sending engine (`email_generator.py`)
- FastAPI backend (`backend/main.py`)
- Next.js frontend (`frontend/app/page.js`)
- Optional Tkinter desktop UI (`email_generator_ui.py`)
- Docker Compose for one-command startup

## Features
- Upload CSV recipients, email body file, and resume attachment
- Send in batches with a configurable limit (`1-250`)
- Resume from pending rows only (completed rows are skipped)
- Per-recipient status tracking in CSV (`status`, `completed`)
- Schedule daily job with two modes:
  - Start today, then continue on schedule
  - Ignore today, start at next schedule time
- CSV preview with pagination, sorting, and filtering (all/completed/pending)

## Required User Inputs
The UI/API expects the following inputs:

1. `CSV file`
- Recipient list
- Can be plain one-email-per-line, or a CSV with `email` column

2. `Body file`
- Plain text file used as email body

3. `Resume file`
- Attachment file (PDF recommended)
- Must exist and not be empty

4. `Subject`
- Email subject line

5. `Sender Email`
- SMTP sender account email

6. `Sender Password`
- SMTP password / app password

7. Optional SMTP settings
- `SMTP host` (default: `smtp.gmail.com`)
- `SMTP port` (default: `587`)
- `Use TLS` toggle

8. Batch/Schedule settings
- `Daily Limit` (`1-250`)
- `Schedule Time` (`HH:MM`, 24h, America/Chicago in scheduler flow)
- Schedule mode:
  - `run_today_then_schedule`
  - `schedule_only`

## CSV Behavior
- Rows with `completed` already set are skipped
- New sends always continue from pending rows
- `status` is updated as `success` or `failed: <reason>`
- `completed` stores ISO timestamp for successful sends

## Schedule Job Functionality
When user selects **Schedule Job**:

1. App validates all required inputs.
2. Based on selected mode:
- `run_today_then_schedule`: runs one batch now, then schedules daily runs.
- `schedule_only`: waits until next scheduled time, then starts.
3. Each batch sends up to the configured `daily_limit` from pending rows.
4. CSV is rewritten after each batch with updated `status` and `completed` values.
5. Job state can be queried (running/paused/scheduled/cancelled/completed/failed).

## Docker Ports
Docker Compose exposes non-default ports to avoid clashing with common local development services:

- Frontend (Next.js): `http://localhost:1300`
- Backend (FastAPI): `http://localhost:1800`
- Backend Swagger docs: `http://localhost:1800/docs`

These are configured in `docker-compose.yml`:
- `1300:3000` for frontend
- `1800:8000` for backend

## Run With Docker
From project root:

```bash
docker compose up --build
```

Open:
- UI: `http://localhost:1300`
- API docs: `http://localhost:1800/docs`

Stop:

```bash
docker compose down
```

## Run Locally (Without Docker)
### Backend
```bash
cd /Users/mar/projects/email-generator
uv run uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload
```

### Frontend
```bash
cd /Users/mar/projects/email-generator/frontend
NEXT_PUBLIC_API_BASE=http://localhost:8080 npm run dev -- -p 13000
```

## Security Notes
- Do not commit sensitive files (recipient CSVs, resumes, local state files).
- `.gitignore` already excludes common sensitive/generated files (`*.csv`, `*.pdf`, caches, local runtime data).
- Prefer app passwords for SMTP providers like Gmail.

## API Endpoints (Backend)
- `GET /health`
- `POST /api/upload`
- `POST /api/send`
- `POST /api/job/start`
- `POST /api/job/pause`
- `POST /api/job/resume`
- `POST /api/job/cancel`
- `GET /api/job/status`
- `GET /api/csv/preview`
