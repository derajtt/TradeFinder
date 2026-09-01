# Premarket Hunter

A private research dashboard that detects early U.S. premarket momentum, confirms
whether a credible catalyst exists (news + SEC filings, AI-classified), applies
transparent risk penalties, emits deterministic **BUY** labels when every gate passes,
and permanently tracks every signal from its immutable initiation price.

> **BUY is a rules-based research signal — not investment advice, not a recommendation,
> and never connected to order execution.**

## Quick start (localhost)

Prerequisites: Python 3.9+ · Node 20+ (Docker optional; with Docker you also get PostgreSQL).

```bash
cp .env.example .env
# insert your rotated FMP + OpenAI keys into .env (never commit it)
./scripts/bootstrap.sh
```

- Dashboard: **http://localhost:3002** (3000 in Docker; the local bootstrap uses 3002 to avoid clashing with other dev servers)
- API health: **http://localhost:8000/health**

To keep the site permanently live on macOS (start at login, restart on crash):

```bash
./scripts/install_launchagents.sh
```

`bootstrap.sh` picks the right path automatically:
- **Docker present** → full compose stack (web + api/worker + PostgreSQL), health-checked, detached.
- **No Docker** → local venv + Node processes with SQLite (identical code paths; SQLAlchemy
  swaps the engine via `DATABASE_URL`).

## v2 architecture (September 2026 rebuild)

**Canonical analytics** — every displayed total (dashboard, CSV, API, reports) comes
from one source: `backend/app/analytics.py::canonical_report`. Counts always reconcile.

**Lifecycle** — each candidate has exactly one status: DISCOVERED → EARLY_WATCH
(pre-7:00, non-actionable) / QUALIFIED_WATCH / ACTIONABLE_BUY (7:00–9:20 only) /
REJECTED (reason + shadow-tracked) / INVALIDATED / EXPIRED / CLOSED / DATA_ERROR.

**Executable pricing** — signals store bid/ask/sizes/spread at signal time; simulated
buys fill at ask+slippage, exits at bid−slippage; no-fill reasons recorded. Records
found before 7:00 are judged from their first actionable quote after 7:00.

**Hard gates vs score** — `strategy/gates.py`: window, tier, Grade-A/B fresh catalyst
(enum extraction, deterministic point maps — a 1–3 scale can never leak into 0–100),
credible source, fresh quotes, tier spread caps, quoted liquidity, dollar-volume,
sustained participation, rotation zones, extension, dilution engine, halt, defined
entry setup with invalidation + R multiple, data health. Score ranks gate-passers only.

**Price tiers** — $0.10–0.25 / 0.25–0.50 / 0.50–1 / 1–2.50 / 2.50–5; the penny tier
demands a Grade-A catalyst, tighter spreads, more liquidity.

**Paper trading** — frozen primary policy (partial at 1R → breakeven stop, 2R target,
time ladder to a 10:30 hard exit) + 14 shadow exit policies on identical signals.

**Backtester** (`backend/app/bt/`, runner `scripts/backtest_run.py`) — chronological
5-minute replay (1-min history is not in the FMP plan; premarket bars from 4:00 AM
verified), SEC-filing-timestamped discovery (no lookahead, no survivorship cherry-pick),
cached data layer, optimistic/baseline/pessimistic execution, AMBIGUOUS both-touch
handling, bounded staged optimization with predefined convergence + accuracy floors,
Wilson lower-bound win-rate ranking, walk-forward folds, one-look untouched holdout,
exit-strategy tournament + MFE decay, PBO estimate. Honest labels for current-only
fields (float ⇒ rotation is ESTIMATED_CURRENT_FLOAT).

**Nightly research** — after the close the droplet replays the completed day, updates
challenger stats, and audit-logs a promotion decision. Auto-promotion is hard-gated
(≥100 forward paper trades among predefined requirements); no strategy changes during
market hours; rollback audit preserved.

## Architecture

```
frontend/  Next.js 15 + TypeScript  — dark trading-terminal UI, SSE live updates
backend/   FastAPI + SQLAlchemy 2   — scanner, scoring, signals, SSE, REST
           providers/fmp.py         — Financial Modeling Prep adapter (all FMP knowledge)
           providers/sec.py         — SEC EDGAR adapter (official, no key, fair-access limits)
           providers/openai_client.py — strict-JSON catalyst analysis (budget-capped)
           scanner/                 — funnel, feature math, self-accumulated minute bars
           scoring/engine.py        — versioned deterministic score + gates (v1.0.0)
           signals/service.py       — immutable BUY accounting + append-only audit
           migrations/              — Alembic
deploy/    DigitalOcean droplet instructions + one-command deploy script
```

### Scanner schedule (America/New_York, DST-safe)

| ET window | Behavior |
|---|---|
| 3:45–4:00 | Prep: health checks, calendar check, cache warmup |
| 4:00–9:30 | Premarket discovery + enrichment + BUY transitions |
| 9:30–16:00 | Track signals (checkpoints 5/15/30/60 min), periodic discovery |
| 16:00–20:00 | Finalize day stats (close checkpoint), light tracking |
| Overnight/weekend/holiday | Idle; next scan time shown in the UI |

### Scoring (strategy v1.0.0)

Components (max): momentum/volume 30 · catalyst quality 25 · SEC filing 15 ·
liquidity/execution 10 · price confirmation 10 · company quality 10.
Penalties: dilution −10..−30 (hard block when severe) · going concern −15 ·
reverse split −10 · no catalyst −15 · recycled news −10 · extreme extension −10 ·
low-dollar-volume risk −5 · conflicting catalysts −5.
Hard blocks (BUY impossible): spread > max, stale quote, unresolved halt,
incomplete live volume, critical data disagreement, severe actionable dilution.

Default BUY gate: score ≥ 75 · verified material catalyst (source link + confidence)
· RVOL ≥ 3.0× · volume + $-volume gates · fresh quote · acceptable spread ·
price confirmation. Signals are created only on the not-qualified → qualified
transition, with idempotency on (symbol, strategy version, trading date,
catalyst fingerprint). Settings changes apply **prospectively only**.

### Immutable signal accounting

`buy_signal_price`, `initiated_at`, score & evidence snapshots are written once in a
single transaction and never modified. Live tracking (current price, day high/low,
since-signal high/low, checkpoints at 5/15/30/60 min and close) updates separate
columns. The `signal_events` table is append-only; corrections are new events.

## Provider capability matrix (probed live against the current plan)

| Endpoint | Status | Use |
|---|---|---|
| biggest-gainers / most-actives | ✅ | discovery pool |
| quote (single symbol per call) | ✅ | prices, freshness, RTH volume counter |
| aftermarket-quote | ✅ | live premarket bid/ask + extended-session volume counter |
| profile / shares-float / splits | ✅ | market cap, float, outstanding, reverse-split flag |
| news/stock-latest | ✅ | catalyst news |
| SEC EDGAR (tickers, submissions) | ✅ | filings, 8-K items, dilution forms |
| OpenAI (gpt-4o-mini default) | ✅ | catalyst classification (strict JSON, cached, budgeted) |
| historical-chart/1min | ❌ 402 | **not in plan** → app accumulates its own minute bars |
| batch-exchange-quote | ❌ 402 | not in plan → movers-list discovery only |
| news/press-releases-latest | ❌ 402 | not in plan → stock news only |

**Consequences (labeled in the UI, never hidden):**
- Minute-bar history builds from live observations while the scanner runs. Time-adjusted
  per-symbol RVOL becomes *measured* once ≥5 prior sessions of premarket bars exist for a
  symbol; until then an **EST**-labeled estimate (premarket volume vs. the symbol's average
  daily volume over a documented curve) may satisfy the RVOL gate only at a stricter
  threshold (default 1.5× multiplier; toggle in Settings).
- Premarket volume comes from the extended-hours counter in `aftermarket-quote`
  (verified live: resets each session). It is labeled "observed since first poll".
- No fresh trade print → quote is stale → **BUY is impossible** (hard block), by design.
- FMP's stock WebSocket operates 8 a.m.–5 p.m. ET, so REST polling is the 4–8 a.m. path.

## Settings (dashboard → Settings)

Universe filters with **blank = no limit**: price range (default **$0–$5**), market cap
min/max (presets for nano/micro caps), float min/max, shares outstanding min/max.
Liquidity gates: min premarket volume (50k), min premarket $-volume (default **$100k**),
max spread 5%. BUY gates: min score, min RVOL, catalyst confidence, extension cap,
estimated-RVOL policy. Scanner: interval, enrichment breadth, OpenAI monthly budget,
pause/resume. Every change is persisted, versioned against signals, and prospective-only.

## Tests

```bash
# backend (45 tests: feature math, calendar, scoring, gates, immutability, idempotency)
cd backend && ../.venv/bin/python -m pytest -q
# frontend
cd frontend && npx vitest run
```

CI (GitHub Actions): secret-pattern scan, backend tests + migration check, frontend
tests + production build. CI uses mocks; no live keys.

## Operations

- Logs: `logs/backend.log`, `logs/frontend.log` (Docker: `docker compose logs -f api`).
  Structured, secret-redacted; API keys never logged.
- Backup / restore: `scripts/backup_db.sh` / `scripts/restore_db.sh <file>`.
- Pause/resume scanning: Settings page, or `POST /api/scanner/pause|resume`.
- System Health page: env status (configured/not, never values), FMP entitlements,
  endpoint freshness/latency, scanner runs, events.
- Restart safety: active signals rehydrate from the DB; idempotency prevents duplicates.

## Secret handling

- Secrets live only in the local server-side `.env` (gitignored). `.env.example` has
  placeholders only. Frontend receives no secrets (only `NEXT_PUBLIC_API_BASE`, a URL).
- The keys shared in the original chat must be treated as exposed — rotate them at
  FMP and OpenAI, then update `.env`.
- Before every push, run the secret-leak gate (also enforced in CI):
  `git grep -nIE 'sk-[A-Za-z0-9_-]{20,}|apikey=[A-Za-z0-9]{16,}|BEGIN .*PRIVATE KEY' -- . ':!*.lock'`

## DigitalOcean (deployed)

**Current production layout (since 2026-09-01):** backend + PostgreSQL run on the
droplet at `104.248.127.54` (`/opt/premarket`, docker compose services `db` + `api`,
sharing the server with SwoopSeo on port 8787 — untouched). The API requires
`X-API-Key` (`API_ACCESS_KEY` in the droplet's `/opt/premarket/.env`; a copy for the
frontend sits in `/root/premarket_frontend_key.txt`). The local frontend
(`launchd`, http://localhost:3002) points at it via `frontend/.env.local`
(`NEXT_PUBLIC_API_BASE` + `NEXT_PUBLIC_API_KEY`). A 2G swapfile was added to protect
the 2GB droplet from memory pressure.

Update the droplet after pushing to GitHub:

```bash
ssh root@104.248.127.54 'cd /opt/premarket && git pull && docker compose up -d --build api'
```

Switch the dashboard back to a local backend anytime:
`launchctl load ~/Library/LaunchAgents/com.premarkethunter.backend.plist`, set
`frontend/.env.local` back to `http://localhost:8000` (no key), rebuild, restart.

See `deploy/digitalocean.md` (droplet setup, firewall, split frontend/backend) and
`deploy/deploy_digitalocean.sh` (one-command rsync + compose deploy). The supplied
`eddsa-key-20260824` value is a **public** key for droplet access; no private key is
ever requested or stored.

## Known limitations & validation checklist

- Movers-list discovery (top gainers/actives) instead of a full exchange sweep
  (batch quotes not in plan) — very early low-float movers may appear only once they
  reach the movers lists.
- Estimated RVOL (labeled EST) until per-symbol premarket history accumulates.
- Reverse-split detection uses the splits endpoint (90-day window).
- Before real-money reliance, compare at least five liquid symbols against a broker
  premarket feed during 7–9 a.m. ET and log latency/volume differences (see
  `backend/scripts/diagnostics.py` for redacted endpoint probes).
