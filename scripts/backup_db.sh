#!/usr/bin/env bash
# Safe database backup. Docker/Postgres -> pg_dump; local SQLite -> file copy.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
STAMP=$(date +%Y%m%d_%H%M%S)
if docker compose ps db 2>/dev/null | grep -q running; then
  docker compose exec -T db pg_dump -U "${POSTGRES_USER:-premarket}" "${POSTGRES_DB:-premarket}" | gzip > "backups/premarket_${STAMP}.sql.gz"
  echo "✓ backups/premarket_${STAMP}.sql.gz"
else
  cp backend/data/premarket.db "backups/premarket_${STAMP}.db"
  echo "✓ backups/premarket_${STAMP}.db"
fi
