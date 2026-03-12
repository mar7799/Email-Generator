#!/usr/bin/env bash
# start_all.sh — start backend and frontend together (docker or local)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
  cat <<-USAGE
Usage: $0 [docker|local]

Commands:
  docker   Start both services with docker compose (recommended)
  local    Start backend (uvicorn) and frontend (npm dev) locally

Before running locally, copy backend/.env.example -> backend/.env and set secrets.
USAGE
}

if [ "$#" -ne 1 ]; then
  usage
  exit 2
fi

MODE="$1"

if [ "$MODE" = "docker" ]; then
  echo "Starting services with Docker Compose..."
  docker compose up --build
  exit $?
fi

if [ "$MODE" = "local" ]; then
  echo "Starting local backend (uvicorn) on :8000 and frontend (Next.js) on :3000"
  cd "$ROOT_DIR"

  # Ensure backend .env exists
  if [ ! -f backend/.env ]; then
    echo "Warning: backend/.env not found. Copy backend/.env.example and set values before running." >&2
  fi

  # Start backend (background)
  uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000 &
  UV_PID=$!
  echo "Backend started (pid $UV_PID)"

  # Ensure we return to project root and start frontend
  cd "$ROOT_DIR/frontend"
  export NEXT_PUBLIC_API_BASE=http://localhost:8000

  # When frontend exits, kill backend
  trap 'echo "Stopping backend..."; kill $UV_PID 2>/dev/null || true; exit' INT TERM EXIT

  npm run dev

  # cleanup (if frontend exits normally)
  kill $UV_PID 2>/dev/null || true
  exit 0
fi

usage
exit 2
