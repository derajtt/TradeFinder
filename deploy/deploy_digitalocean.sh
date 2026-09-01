#!/usr/bin/env bash
# One-command deploy of backend+db (and optionally web) to a DigitalOcean droplet.
# Usage: DROPLET_IP=x.x.x.x [DEPLOY_WEB=1] ./deploy/deploy_digitalocean.sh
set -euo pipefail
: "${DROPLET_IP:?set DROPLET_IP=<ip>}"
SSH="ssh -o StrictHostKeyChecking=accept-new root@${DROPLET_IP}"

echo "→ ensuring Docker on droplet"
$SSH 'command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh'

echo "→ syncing project (secrets excluded)"
rsync -az --delete \
  --exclude '.git' --exclude '.env' --exclude '.env.*' --exclude 'node_modules' \
  --exclude '.next' --exclude '.venv' --exclude 'data' --exclude 'logs' \
  --exclude 'backups' --exclude '__pycache__' \
  ./ "root@${DROPLET_IP}:/opt/premarket/"

echo "→ preparing droplet .env (created from example on first run; edit it there)"
$SSH 'cd /opt/premarket && ([ -f .env ] || cp .env.example .env)'
$SSH 'cd /opt/premarket && grep -qE "^FMP_API_KEY=.+" .env || echo "!! EDIT /opt/premarket/.env with rotated keys, then re-run"'

SERVICES="db api"
[ "${DEPLOY_WEB:-0}" = "1" ] && SERVICES="db api web"
echo "→ building & starting: $SERVICES"
$SSH "cd /opt/premarket && docker compose up -d --build $SERVICES"

echo "→ health"
$SSH 'for i in $(seq 1 45); do curl -sf http://localhost:8000/health && break || sleep 2; done; echo'
echo "✓ done — API: http://${DROPLET_IP}:8000/health"
