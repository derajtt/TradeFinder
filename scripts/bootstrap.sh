#!/usr/bin/env bash
# Premarket Hunter first-run bootstrap.
# Path A (Docker present): full compose stack with PostgreSQL.
# Path B (no Docker): local venv + Node processes with SQLite (same code paths).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "→ .env missing. Creating from .env.example — insert your rotated FMP/OpenAI keys, then re-run."
  cp .env.example .env
  exit 1
fi

# never echo values; verify presence only
for var in FMP_API_KEY APP_SECRET; do
  if ! grep -qE "^${var}=.+" .env; then
    echo "✗ ${var} is not set in .env (value not shown). Aborting."
    exit 1
  fi
done
echo "✓ required secrets present (values never printed)"

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "→ Docker detected: starting web + api + PostgreSQL"
  docker compose up -d --build
  echo "→ waiting for health…"
  for i in $(seq 1 60); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then break; fi
    sleep 2
  done
  curl -sf http://localhost:8000/health >/dev/null || { echo "✗ API failed health check"; docker compose logs api | tail -20; exit 1; }
else
  echo "→ Docker not available: local processes with SQLite"
  [ -d .venv ] || python3 -m venv .venv
  ./.venv/bin/pip -q install -r backend/requirements.txt
  (cd backend && ../.venv/bin/python -m alembic upgrade head)
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  mkdir -p logs
  (cd backend && nohup ../.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >> ../logs/backend.log 2>&1 &)
  export PATH="$HOME/opt/node22/bin:$PATH"
  command -v node >/dev/null || { echo "✗ Node.js not found. Install Node 20+ then re-run."; exit 1; }
  (cd frontend && [ -d node_modules ] || npm ci --no-audit --no-fund)
  (cd frontend && [ -d .next ] || npm run build)
  pkill -f "next start" 2>/dev/null || true
  (cd frontend && nohup npm run start >> ../logs/frontend.log 2>&1 &)
  for i in $(seq 1 45); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then break; fi
    sleep 2
  done
fi

echo
echo "✓ Premarket Hunter is running"
echo "  Dashboard:   http://localhost:3002"
echo "  API health:  http://localhost:8000/health"
curl -s http://localhost:8000/api/status | python3 -c "import json,sys; d=json.load(sys.stdin); print('  Phase:', d['phase'], '| next scan:', d['next_scan_start'])" 2>/dev/null || true
