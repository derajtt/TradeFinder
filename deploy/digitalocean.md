# Deploying Premarket Hunter to a DigitalOcean Droplet

The same Docker stack that runs locally runs on the droplet with zero architectural
changes. Recommended: Ubuntu 24.04 LTS, Basic 2 vCPU / 4 GB ($24/mo) or larger.

## 1. Create the droplet

- Image: **Ubuntu 24.04 LTS**
- Auth: **SSH key** — add the public key you use (the `eddsa-key-20260824` public key
  the owner supplied belongs in the droplet's authorized keys; the private half stays
  on the owner's machine and is never shared or uploaded anywhere).
- Enable the DigitalOcean firewall: allow inbound **22/tcp** (SSH), **3000/tcp** (web),
  **8000/tcp** (API) — or keep 3000/8000 closed and use an SSH tunnel / reverse proxy.

## 2. Install Docker on the droplet

```bash
ssh root@<DROPLET_IP>
curl -fsSL https://get.docker.com | sh
```

## 3. Ship the project (no secrets in git)

```bash
git clone https://github.com/derajtt/TradeFinder.git /opt/premarket
cd /opt/premarket
cp .env.example .env
nano .env           # insert rotated FMP/OpenAI keys, a strong POSTGRES_PASSWORD & APP_SECRET
```

Set in `.env` on the droplet:

```
DATABASE_URL is provided by docker-compose (Postgres service) — leave the example value.
CORS_ORIGINS=http://localhost:3000,http://<DROPLET_IP>:3000
NEXT_PUBLIC_API_BASE=http://<DROPLET_IP>:8000
```

## 4. Start

```bash
docker compose up -d --build
docker compose ps          # wait for healthy
curl -s http://localhost:8000/health
```

The scheduler runs on America/New_York time regardless of server timezone.

## 5. Split frontend/backend (frontend on your laptop, backend on the droplet)

On the droplet run only the API + DB:

```bash
docker compose up -d --build db api
```

On your laptop set `frontend/.env.local`:

```
NEXT_PUBLIC_API_BASE=http://<DROPLET_IP>:8000
```

and add `http://localhost:3000` to `CORS_ORIGINS` in the droplet's `.env`, then
`docker compose restart api`.

## 6. Persistence, backups, updates

- Postgres data lives in the `pgdata` named volume; app bar history in `apidata`.
- Backup: `scripts/backup_db.sh` (pg_dump into ./backups). Restore: `scripts/restore_db.sh <file>`.
- Update: `git pull && docker compose up -d --build`.

## 7. Later: domain + TLS

Point DNS A record at the droplet, put Caddy or nginx in front of ports 3000/8000,
and switch `NEXT_PUBLIC_API_BASE`/`CORS_ORIGINS` to the https URLs. Not required for
IP-based use.

## Security notes

- Never commit `.env`; never paste keys into shells you don't control.
- The SSH **private** key is never requested, stored, or used by this repository.
- Rotate the FMP and OpenAI keys that were previously shared in chat.
