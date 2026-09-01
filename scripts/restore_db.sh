#!/usr/bin/env bash
# Restore a backup created by backup_db.sh. Usage: scripts/restore_db.sh <backup-file>
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -eq 1 ] || { echo "usage: $0 <backup-file>"; exit 1; }
case "$1" in
  *.sql.gz)
    gunzip -c "$1" | docker compose exec -T db psql -U "${POSTGRES_USER:-premarket}" "${POSTGRES_DB:-premarket}"
    echo "✓ restored into PostgreSQL";;
  *.db)
    pkill -f "uvicorn app.main:app" 2>/dev/null || true
    cp "$1" backend/data/premarket.db
    echo "✓ restored SQLite file. Restart the backend (scripts/bootstrap.sh).";;
  *) echo "unknown backup type"; exit 1;;
esac
