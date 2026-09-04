# TradeFinder UI Remake — Final Implementation Spec

Scope: Next.js app-router frontend at `/Users/blackbox/TradeFinder/frontend`. All existing routes stay reachable; no backend changes are required for any Simple-mode screen. Field names below are the ones the backend actually emits today (verified against `app/routes/*.py` and `lib/types.ts`); where the winning proposal used a different name it has been corrected (e.g. `status.next_scan_start`, `status.api_calls_per_min`, lowercase `phase` values `premarket | regular | open | afterhours | closed | prep`).

Global design rules (apply everywhere):

1. One primary question per screen; the heading is a plain sentence, the first section answers it.
2. Simple is the default; Advanced is a global toggle (§6). Any Simple section may expose a local "Show details" (`<Details>`) that reveals its Advanced content in place.
3. Plain English in Simple: no snake_case, enum strings, abbreviations or ALL-CAPS badges. `lib/vocab.ts` (§4.10) is the single mapping source; the enum is shown only in Advanced.
4. Every number carries a label, a population `n`, a source caption and an evidence chip — implemented once in `StatTile`, never hand-rolled.
5. Evidence classes never mix in one tile/table/leaderboard: **Backtest** (with split), **Paper** (simulated account), **Tracked** (signal outcomes — never called "Live"), **Live** (real money — only when `status.paper_mode === false`).
6. Nothing important lives only in a hover. All `title=` attributes on `<th>`, `.kv`, badges and rows are removed (76 today). Definitions come from `Term`/`Tip` popovers that are click/focus-activated.
7. Minimum readable size 12px; 11px only for uppercase eyebrows. 9–10.5px text is removed (29 CSS sites, 76 inline sites).
8. Column suppression rule: in Simple, any column empty on >80% of the passed rows is hidden and a one-line header note says so ("Stop / Target shown for buys only"). Implemented once in `DataTable`.
9. Drawdown basis rule: a drawdown is never rendered without its basis word — "of $10,000 account" or "sum of trade %". Basis is declared per endpoint (§7.5), never inferred from magnitude.
10. Sample rule: `n < 30` → amber "Too few to judge ({n} trades)"; `n < 10` → value dimmed and the warning becomes the headline; `n === 0` → "—" and "No trades yet". Thresholds live in `lib/evidence.ts`.

---

## 1 Navigation IA

### 1.1 Sidebar (`components/Nav.tsx`)

Grouped links; 4 groups in Simple (12 links), 5 groups in Advanced (18). Route paths are unchanged. Group eyebrows: `.eyebrow` (11px uppercase `--text-faint`). The per-model "Finders" list (21 items, polled every 120s) leaves the sidebar.

| Group | Label (Simple) | Route | Advanced-only | Notes |
|---|---|---|---|---|
| Today | Today | `/` | | was "Command Center" |
| | Picks | `/signals` | | was "Signal History" |
| | News & filings | `/feed` | | |
| | Calendar | `/calendar` | | |
| Stocks | Charts | `/chart` | | was "Chart Workstation" |
| | My watchlist & alerts | `/watchlists` | | |
| | My journal | `/journal` | | |
| Results | Scorecard | `/performance` | | was "Performance" |
| | Strategies (paper accounts) | `/competition` | | was "Competition"; cards link to `/models/[id]` |
| | Accuracy board | `/accuracy` | yes | |
| Research | Backtests | `/backtest` | yes | |
| | Exit lab | `/lab` | yes | |
| | Quant lab | `/quant` | yes | existing route, kept as-is (label change only) |
| | Extreme Reversion | `/reversion` | yes | always carries a red `StatusPill` "Failed test" |
| Setup | Risk & position size | `/risk` | | |
| | Settings | `/settings` | | |
| | System health | `/health` | yes | in Simple the TopBar scanner pill links here |

- Under *Strategies*: a collapsible **"All strategies (N)"** sub-list, collapsed by default in both modes, listing `/api/models` `models[].name` with the color dot; `experimental` renders as the word "experimental" in `--text-faint` (no `EXP` badge). `/api/models` is fetched only when the sub-list is opened.
- Export `NAV_GROUPS: { group: string; items: { href: string; label: string; advanced?: boolean; pill?: { label: string; tone: Tone } }[] }[]` from `components/Nav.tsx` so tests and the TopBar can reuse it.
- Brand block: "TRADEFINDER" only; the `MULTI-STRATEGY` sub-line is removed. `app/layout.tsx` metadata title becomes "TradeFinder" (fixes the "Premarket Hunter" mismatch). Sidebar footer disclaimer stays at 12px.
- ≤900px behaviour (icon-only rail) unchanged.

### 1.2 TopBar (`components/TopBar.tsx`)

One 44px line, never wraps (`flex-wrap: nowrap; overflow: hidden`). Single data source: `useStatus()` from `lib/status.tsx` (§4.11) — the page stops polling `/api/status` itself; the app has one poll and one EventSource.

Simple (left → right):

1. **Market phase pill** — `StatusPill` from `vocab.phase(status.phase)`: `premarket` → "Premarket" (early/cyan); `regular|open` → "Market open" (buy); `afterhours` → "After hours" (neutral); `closed` → "Market closed" (neutral); `prep` → "Getting ready" (neutral); unknown → neutral with raw in `<code>`.
2. **Clock** — `HH:MM ET` in Simple (no seconds); `HH:MM:SS ET` in Advanced. Source: local clock in `America/New_York`.
3. **Scanner pill** — `useScannerState()`: `err` → "Scanner unreachable" (risk); `scanner.paused` → "Scanner paused" (warn); `last_cycle_ok === false` → "Scanner problem" (risk); `last_cycle_ok == null` → "Scanner starting" (neutral); phase closed/afterhours → "Scanner sleeping" (neutral); else "Scanner running" (buy). Links to `/health`. Replaces dot + text + "Last cycle".
4. **Next scan** — "Next scan {fmtEtShort(status.next_scan_start)}" always rendered; when phase is premarket/open: "Scanning now · {status.scanner.candidates} candidates".
5. **ModeToggle** (`Simple | Advanced` segmented control) then the `?` glossary button which opens `GlossaryPanel` (the floating FAB is removed).

Advanced appends: "Tracked signals (all models) {status.active_signals}" (never labelled BUY); "Data use {api_calls_per_min}/300 per min" (warn tone if >240); "Throttled {api_throttles_1h} in last hour" only when >0 (risk); "AI cost this month ${ai_usage_month.est_cost_usd}"; "Paper accounts total ${sum(models[].account.equity)} ({models.length} × $10k)" from `/api/models` (60s, Advanced-only fetch); regime as `Term('regime')` "Market type: {vocab.regime(models.regime.state)}"; "engine v{canonical.versions.strategy_version}" from `/api/report/canonical` (no profile) — `status.strategy_version` is dropped from the UI; if it disagrees with the canonical value render "engine v2.0.0 · settings v1.1.0" so the mismatch is visible.

### 1.3 Onboarding (`components/Onboarding.tsx`)

Five steps, rewritten to describe what the user lands on. Step 5 has two buttons: "Start in Simple" (sets `tf_mode=simple`) and "I know this stuff — Advanced" (sets `tf_mode=advanced`). Copy:

1. "Today shows whether there is anything to buy right now — and if not, why not and when the next scan runs."
2. "A Buy pick passed every check. A Watch is a stock the scanner is tracking that might become one. 'What's missing' tells you the gap."
3. "Every pick has a plan: buy price, stop (where we admit we're wrong), and two targets."
4. "Everything here is paper — simulated money. Results with few trades are marked 'too few to judge'. Nothing here is advice."
5. "Simple mode hides the machinery. Flip to Advanced any time from the top bar."

---

## 2 Command Center → **Today** (`/`, `app/page.tsx`)

**Primary question:** "Is there anything the system says to buy right now — and if not, why not and when next?"

Fold budget (Simple, 1440×900, market closed): TopBar 44 → Status line + attention chips ~90 → Premarket row 28 → Buy picks card ~150 → Watching header ~48 → first table row at ≈ y 380. ≤ 12 numbers and ≤ 4 pills above the first table row. All model-scoped sections carry the scope in their source caption ("Primary model"); global sections say "All models".

Data hooks (all in `app/page.tsx`, passed down as props; components do not poll on their own except where stated):

| Source | Hook / poll | Used by |
|---|---|---|
| `/api/status` | `useStatus()` (15s + stream) | status line, chips, session strip, trust tiles |
| `/api/ops` | `useOps()` (30s) | status line, empty state, scanner panel, system detail |
| `/api/settings` | `useAppSettings()` (120s) | session strip, empty state, score thresholds, stale threshold |
| `/api/signals?active_only=true&profile={p}` | `usePollingState` 30s + stream `signals`, `buy_signal` | buy picks, watching, chips |
| `/api/candidates` | 30s + stream `candidates` | scanner board, radar |
| `/api/positions?profile={p}` | 20s | open paper trades, chips |
| `/api/alerts` | 60s | chips |
| `/api/report/canonical?profile={p}` | 30s | trust tile 1, pipeline, digest guard |
| `/api/outcomes/noon` | 60s | trust tile 3, chips, noon detail |
| `/api/digest`, `/api/brief` | Advanced only, 60s/120s | digest prose |
| `/api/rejected?profile={p}` | Advanced only, 60s | blocked today |

Loading rule: every section receives `loaded` and renders skeletons until its first response; "0", "none" or an EmptyState are never rendered before `loaded === true`.

### 2.0 Strategy scope (`components/StrategyScope.tsx`, replaces `ProfileTabs`)

- **Question:** Which strategy am I looking at?
- **API:** `/api/profiles` → `profiles[id].{name, enabled, color}`; state via `useProfile()` (`ph_profile`).
- **Simple:** one labelled `<select>`-styled pill: "Strategy: Primary ▾". **Advanced:** the current pill row plus the 12px caption "all enabled strategies evaluate every stock; this only changes the view".
- **Caption emitted for children:** `scopeLabel = "{name} model"` used in every model-scoped source caption.

### 2.1 Status line + attention chips (new `components/StatusLine.tsx`, `components/AttentionChips.tsx`; replace OpsPanel headline, DigestCard headline, TopBar duplication)

- **Question:** Is the machine on, what happens next, and is there anything that needs my attention?
- **API fields:** `status.phase`, scanner state (§1.2), `ops.upcoming[0].{event, at_et}` (fallback `status.next_scan_start`), `ops.quiet_reason`.
- **Line 1 (16px):** `{phase label} · {scanner label} · Next: {event} {fmtEtShort(at_et)}` → "Market closed · Scanner sleeping · Next: premarket scan Thu 4:00 AM ET". Built client-side from structured fields — the prose `digest.line` is never parsed.
- **Line 2 (13px dim):** `ops.quiet_reason` when present, else "" (line reserved, no chrome). Never returns null.
- **Attention chips (12px, max 4, the only element on the page allowed to glow `.glow`):** priority order; overflow shows "+N".
  1. Scanner problem/unreachable → risk → `/health`
  2. Scanner paused → warn → `/settings`
  3. Stale quotes — only when phase ∈ {premarket, regular/open} and ≥30% of watch rows have `current_ts` older than `settings.quote_freshness_sec` (fallback 180s) → warn "Quotes stale" → `#scanner`
  4. `{n} buy picks live` (n>0, buy tone, glows) → `#buys`
  5. `{n} open paper trades · stops set` (positions with `status==='open'`) → accent → `#positions`
  6. Alert fired in last 24h (`alerts.rows[].fired_at`) → accent "Alert: {symbol} {condition} {fmtPrice(price)}" → `/watchlists`
  7. After 12:00 ET with `noon.denominator > 0` → neutral "Noon check locked · {call_win_rate}% green" → `#trust`
- **Advanced adds** a third line: "Morning brief ({brief.session_date}): {brief.content.headline} · Most common block: {vocab.gateLabel(top_rejection_reasons[0][0])} — {count} stocks" and the digest prose, guarded: if `digest.today.buys !== canonical(all).lifecycle_counts.ACTIONABLE_BUY`, append "(digest counts include all models and early/watching signals)". In Simple the digest is not rendered at all (every number in it exists elsewhere with scope).
- **Removed here:** regime emoji sentence (→ §2.9).

### 2.2 Premarket clock (`components/SessionStrip.tsx`)

- **Question:** How far into premarket are we, and when can a pick become a Buy?
- **API:** `settings.buy_confirm_after_et` (not the hardcoded `'07:00'`), local ET clock.
- **Layout:** 28px row always reserved. Inside 3:30–10:00 ET: track with fill, "4:00 AM premarket start" / "9:30 AM market open" labels, marker labelled "Buys allowed from {buy_confirm_after_et}", plus a 12px caption "{pct}% through premarket · {N} min to open · buys from {buy_confirm_after_et} ET". Outside: one 12px `--text-faint` line "Premarket runs 4:00–9:30 AM ET · buys from {buy_confirm_after_et} ET". No `title` attributes.
- Simple = Advanced.

### 2.3 Buy picks right now (new `components/BuyPicks.tsx` + `components/PickCard.tsx`; replaces the 5 KPI cards) — `id="buys"`

- **Question:** Is there a stock to buy right now?
- **API:** signal rows filtered `signal_type === 'buy' && status === 'active'`. Count = filtered length — the only number on the page allowed to be called "Buy picks".
- **Header:** `SectionHeader` title "Buy picks right now — {count | 'none'}", question as above, caption "{scopeLabel} · passed every check · paper plan only", `evidence="TRACKED"`.
- **count > 0:** up to 3 `PickCard`s then "+N more" → `/signals?type=buy&status=active`. PickCard fields/labels: `symbol` + company name (`name` if on the row, else lazy `/api/candidates/{symbol}` `company.name`); `buy_price` "Buy price"; `current` "Now" with text freshness "as of {fmtEtClock(current_ts)}"; `change_pct` "Since pick"; `stop`/`target1`/`target2` "Stop / Target 1 / Target 2"; catalyst `vocab.catalystLabel(catalyst_type)`; `initiated_at` "Picked {fmtEtShort}". One button "See the plan" → `DetailDrawer`. Advanced adds `score` via `ScorePill` and `price_source`.
- **count === 0:** `EmptyState` headline "Nothing to buy right now"; `reason = ops.quiet_reason`; `next` = phase-dependent: closed/afterhours → "Buys can appear from {buy_confirm_after_et} ET during the 4:00–9:30 premarket scan (next scan {fmtEtShort(next_scan_start)})"; premarket before confirm time → "Buys are allowed from {buy_confirm_after_et} ET — early qualifiers show as Early watch below"; premarket after / open → "The scanner is running; a stock becomes a Buy pick the moment every check passes"; action "See what's being watched ↓" → `#watching`.
- **Advanced adds** `StatTile` "Best open Buy pick" computed only over buy rows (max `change_pct`), "—" when none.
- **Removed:** "Best Current Performer" (picked WATCH rows), "Active BUY Signals 133" (mixed), "Highest-Score Candidate", "Scanner Health" (→ TopBar pill), "Scanner Hit Rate" (→ §2.6).

### 2.4 Watching — could become buys (`SignalTable variant="watch"`) — `id="watching"`

- **Question:** Which stocks is the model watching, what is missing, and how are they doing since it spotted them?
- **API:** signal rows filtered `signal_type === 'watch' && status === 'active'`, sorted score desc.
- **Header:** "Watching — {count} stocks"; caption "Tracked · {scopeLabel} · qualified by the scanner but not yet buys · prices as of {latest current_ts}"; `evidence="TRACKED"`. Header note: "Score out of 100 · ≥ {settings.min_score_for_buy} needed to buy · Strong ≥ {min} · OK 55–{min−1} · Weak < 55".
- **Simple: capped at 8 rows** (`cap={8}`, footer "Show all {n}"). Columns (7): Stock (symbol + `.co-name`; cyan "Early watch" pill only when the row is early-window, otherwise no pill); Score (`ScorePill` "72 / 100 · Strong"); First seen (`buy_price` — never "Buy @"); Now (`current` + text freshness; amber dot only while market open and quote > `quote_freshness_sec` old); Since spotted (`change_pct`); **What's missing** (`WhatsMissing lazy symbol=…` — first failing `explain[]` item from `/api/candidates/{symbol}` fetched on row expand/hover-intent, rendered "{label}: {actual} (need {required})" + "+N more"; `hard_blocks` in red "Blocked: {gateLabel}"; "Not in today's scan" when absent); Picked (`fmtEtShort(initiated_at)`).
- Suppressed via rule 8 in Simple: Stop/T1/T2, Day Hi/Lo, Since Hi/Lo, Max gain, Max DD, Result, Status, catalyst.
- **Advanced:** all 14 current columns; "Result" renamed "Early pop?" with header `Term('early_pop')` and values via `vocab.OUTCOME` (Popped / Didn't / Flat / Pending); Max gain/Max DD colored only when nonzero.
- After hours: one 12px note above the table "Market is closed — prices are from the last session" replaces the amber dots (`marketClosed` prop).
- Row click → `DetailDrawer`.

### 2.5 Scanner — what it is looking at (`components/CandidateTable.tsx`) — `id="scanner"`

- **Question:** What stocks are being checked right now, and what is blocking them?
- **API:** `/api/candidates` `rows[]` (`CandidateRow`), `ops.quiet_reason` via `useOps()` (no second poll), `status.scanner.{candidates, last_cycle_at}`.
- **Visibility (Simple):** full table only when phase ∈ {premarket, regular/open}. Otherwise a one-line panel "Scanner runs 4:00–9:30 AM ET. Last run at {fmtEtShort(last_cycle_at)} found {rows.length} candidates." with a `Details` "Show last results".
- **Simple columns (7):** Stock; Score (`ScorePill`); Price (`price`; when `price_indicative` a gray chip "indicative"); Gap (`gap_pct`, header `Term('gap')`); Volume vs normal (`rvol` → "3.2× normal"; `rvol_estimated` → gray chip "estimate"); **What's missing** (`WhatsMissing explain={row.explain} hardBlocks={row.hard_blocks}`); Status (`vocab.candidateStatus(row)`: `hard_blocks.length` → "Blocked" neutral; `gates_failed.length` → "Not yet" neutral; `early` → "Early watch" early; `buy` → "Buy" buy; else "Watching" early).
- **Advanced:** the current 14 columns with headers via `Term`; `gate_why` raw.
- Filter input labelled "Find a symbol". Row glow on update unchanged. Empty state uses `EmptyState` with `ops.quiet_reason`.

### 2.6 How is this strategy doing? (new `components/TrustTiles.tsx`; replaces FunnelStrip WR, KPI Hit Rate, NoonCard headline) — `id="trust"`

- **Question:** Should I trust this model's picks?
- **Simple:** exactly three `StatTile`s:
  1. **Paper trades** — `canonical.actionable_buy_performance`: value "{win_rate×100}% won", `n = closed_trades`, unit "trades", source "Paper account · {scopeLabel} · Buy picks only", `evidence="PAPER"`, `sub` = "Not enough trades yet to trust this rate" when `calibration !== 'calibrated'` (text from `note` when present). Sample rule applies (n=1 → headline "Too few to judge (1 trade)", value dimmed). Advanced adds "Conservative floor {wilsonLower(wins, closed_trades)}%" as `Term('conservative_floor')`.
  2. **Early pops** — `status.outcomes`: value "{win_rate×100}% popped", `n = win + loss`, nLabel "of {n} decided picks · {neutral} flat not counted · {pending} pending", source "All models · watches and buys · judged in the first {settings.early_window_min} min", `evidence="TRACKED"`.
  3. **Noon check** — `/api/outcomes/noon`: value "{call_win_rate×100}% green at noon", `n = denominator`, nLabel "of {n} picks", source "All models · was the pick above its pick price at 12:00 ET", `evidence="TRACKED"`.
- **Advanced adds:** noon class breakdown as `StatusPill`s ("+10% touch", "green at noon", "red at noon", "flat", "incomplete") and the symbol chips; `policy` string never shown; `win_rate_lb` rendered as "Conservative floor".

### 2.7 Open paper trades (`components/PositionsTable.tsx`) — `id="positions"`

- **Question:** Does the paper account hold anything right now?
- **API:** `/api/positions?profile=` rows `{symbol, status, opened_at, entry_fill, stop, target1, target2, remaining_frac, realized_r, exit_reason, closed_at, size_usd}`.
- **Simple:** empty → one line "No open paper trades for {scopeLabel}." Else `DataTable` columns: Stock; Bought at (`entry_fill`); Now (live from signal rows by symbol, else "—"); Stop; Targets ("T1 / T2" one cell); Result so far (`fmtR(realized_r)`, header `Term('r_multiple')`); Status (`vocab.POSITION_STATUS`: open → "Open" buy, closed → "Closed" neutral). Caption "Paper account · {scopeLabel}", `evidence="PAPER"`.
- **Advanced:** adds Remaining (`remaining_frac`), Exit reason, Opened, engine version.

### 2.8 Pipeline (`components/FunnelStrip.tsx`) — **Advanced only**

- **Question:** Where are today's stocks in the pipeline?
- **API:** `canonical.lifecycle_counts`, `totals.rejected_candidates`, `reconciliation.equals_total`, `versions`.
- Render: "Found {DISCOVERED} → Early watch {EARLY_WATCH} → Watching {QUALIFIED_WATCH} → Buy picks {ACTIONABLE_BUY}" then gray "Blocked {REJECTED} · Dropped {INVALIDATED} · Expired {EXPIRED} · Closed {CLOSED}"; "Blocked but still tracked {rejected_candidates} (today)"; `StatusPill` "counts reconcile" (buy) / "counts don't reconcile — see System health" (risk); "engine v{strategy_version} · filters v{filter_version}". Watching uses `--early`, never `--warn`. `win_rate_lb` null → "—" (fixes NaN). Caption "{scopeLabel}".

### 2.9 System detail (`components/OpsPanel.tsx`) — **Advanced only**

- "Market type: {ops.regime_text}" with `Term('regime')`; lanes as `StatusPill` via `vocab.LANE_STATE` (unknown → neutral + raw `<code>`); `detail` as a 12px second line (not title); "Next up" times `fmtEtShort` with ET; `not_running[].why` inline 12px.

### 2.10 Blocked today (`components/RejectedTable.tsx`) — **Advanced only**, collapsed `Details`. `/api/rejected?profile=`; `failed_gates` via `vocab.gateLabel`; caption "Stocks the scanner looked at and blocked today — kept so we can check the filters aren't wrong"; `missed_move_pct` header "Move after block".

### 2.11 Radar (`components/RadarTable.tsx`) — **Advanced only**, hidden when empty. Headers via `Term`; `has_news` → "News" pill.

### 2.12 Disclaimer — unchanged text, 12px, `.disclaimer`.

---

## 3 Per-page plan

Component vocabulary is from §4. "Question" is rendered by `SectionHeader question=`.

### 3.1 `/competition` → **Strategies (paper accounts)** — Group C
- **Question:** "Which strategies are actually making paper money, with enough trades to mean anything?"
- **API:** `/api/competition` `{cards[], leaderboards{board: Card[]}, note}`; `/api/models` `{regime, research_only[]}`.
- **Keep:** cards, leaderboards, research-only table, regime chip, sparkline.
- **Rename:** h1 "Strategies — paper accounts"; status via `vocab.MODEL_STATUS` (`LIVE`→"Paper trading" unless `status.paper_mode===false`, `PAPER LIVE`/`PAPER_LIVE`→"Paper trading", `WAITING` with trades>0 →"Between scans", else "Waiting for a setup", `NO_DATA`→"No data", `OFFLINE`→"Offline", `ERROR`→"Error", `DISABLED`→"Off").
- **Split:** cards into "Trading" (`trades > 0`, sorted `return_pct` desc) and a collapsed `Details` "{n} strategies have not traded yet (show)" with one-line rows "name · waiting for first trade · {idle_reason}".
- **Card body:** `StatTile`-style: return %, "Account {fmtPrice(equity)} of $10,000", "{trades} trades · {win_rate}% won" (sample rule), status pill, "scanning {symbols_scanned} stocks this cycle", sparkline with min/max labels. Drawdown: "worst dip {max_drawdown_pct}% of account" (basis 'account', §7.5). Remove "marked Ns ago" and "season" from cards → Advanced section-header caption "all accounts marked {ago(last_marked_at)} · season {season}".
- **Leaderboards:** rank only `trades >= SAMPLE.rank` (5); below the ranked list, "Not ranked — {n} trades (min 5)" rows; never-traded excluded; every row shows "{value} · {trades} trades", amber "too few" mark when `trades < SAMPLE.judge`; caption "Ranked among strategies with at least 5 paper trades"; `evidence="PAPER"`; board names via `vocab.BOARD` (`win_rate`→"Most often right", `drawdown`→"Smallest worst dip", `return`→"Highest return").
- **Remove:** `EXP` badge (→ word "experimental"), `.st` enum pills, all `title=`.
- **Advanced-only:** raw status enum, `cash`, `realized_pnl`, season.

### 3.2 `/models/[id]` → **Strategy: {name}** — Group C
- **Question:** "What does this strategy look for, and how is its paper account doing?"
- **API:** `/api/models` `models[].{name,color,edge,universe,horizon,cadence,asset_classes,experimental,enabled,requires,data_notes,hypothesis,account{equity,return_pct,max_drawdown_pct,trades_closed,wins},signals{}}`; `/api/signals?profile={id}&limit=200&dedupe&sort`; `/api/positions?profile={id}`; `extreme_reversion` keeps the `/api/reversion/signals` mapping.
- **Keep:** tabs, sort, dedupe, positions table, drawer.
- **Change:** default tab **Picks** (was "overview"); tabs "Picks | Paper trades | About"; hero paragraph + disclaimer → About tab. Hero `StatTile`s: "Account {equity} of $10,000" (PAPER), "Return {return_pct}% · paper" (PAPER), "Trades {trades_closed}" with sample rule + "{wins} won", "Picks {sum(signals)} all time · {buy} buys · {watching} watching" where `signals` keys map via `vocab.LIFECYCLE`; the `legacy` bucket is folded into "other" with `Term('legacy_bucket')`. Drawdown "worst dip {x}% of account". `experimental` → word; `!enabled` → `StatusPill` "Off".
- **Picks tab:** `SignalTable variant="mixed" scope="{name} model"`; dedupe switch with visible state label; counter "{rows} stocks (one row per stock)".
- **Paper trades tab:** `DataTable` with the §2.7 Simple columns; Advanced adds Size, Exit reason.
- **Remove:** all `title=`; `.badge est`.

### 3.3 `/signals` → **Picks** — Group D
- **Question:** "What has this strategy picked, and how did each pick turn out?"
- **API:** `/api/signals?include_demo&limit=500&profile&dedupe&sort`; CSV `/api/signals/export.csv`.
- **Keep:** table, CSV export, drawer, `StrategyScope`, filters.
- **Change:** h1 "Picks — {profile name}"; subtitle "Every stock this strategy flagged. Buys passed all checks; Watches did not (yet)." Default status filter **All**, default sort **Newest first** (`sort=time`). Labelled controls: "Show: Buys | Watches | Both" (client filter on `signal_type`), "Status: Open | Closed | Dropped | All" (`vocab.SIGNAL_STATUS`), "One row per stock" real switch with state label, "Include demo rows" switch Advanced-only. Counter "{n} stocks (one row per stock)" or "{n} records". Reads `?type=buy&status=active` from the URL (deep link from §2.3).
- **Columns:** `SignalTable variant="mixed"` (Simple set + Buy/Watch pill + "Early pop?" words; Advanced = current 14).
- **Remove:** "immutable chronological record" meta; all `title=`.

### 3.4 `/feed` → **News & filings** — Group D
- **Question:** "What news or SEC filings just came out for stocks in play?"
- **API:** `/api/feed?form&symbol&sort&kind` rows `{ts, kind, form, items, symbol, title, url, source}`, `forms[]`; stream event `filing`.
- **Keep:** stream, live push, filters, sort, drawer.
- **Change:** form shown once (pill "8-K") + cleaned title (strip `^(\S+)\s+—\s+\1\s+—\s+` and `K - ` artefacts; empty → company name); `items` decoded via `vocab.ITEM_CODES` into gray chips (raw codes Advanced-only); per-row suffix chip "filed" / "published" from `kind`; counter "{n} most recent (cap 80)"; "News" toggle disabled with "no news yet today" when zero news rows; filter bar in two labelled rows ("Find a symbol / Form" and "Show / Sort"); LIVE pill moves to the `SectionHeader` right slot as `StatusPill` "Live · last filing {ago}" (buy tone — this is a data feed, not money; label reads "Live feed").
- **Advanced-only:** raw item codes, EDGAR accession link.
- **Remove:** `.st st-live`, `title=`, 10.5px text.

### 3.5 `/health` → **System health** — Group D
- **Question:** "Is anything broken?"
- **API:** `/api/health/detail` `{env_status, backup, entitlements, endpoints[], events[], runs[], scheduler}`; `/api/health/strategies` `{strategies[], counts, legend, stale_after_seconds, scheduler}`; `status.scanner.cycles`.
- **Change:** top = one `StatusPill` verdict "All systems normal" / "{n} problems" derived from: scanner ok, `backup.age_hours < 24`, any `strategies[].errors > 0`, any entitlement `!ok`; each failing check listed as a 13px line under the pill. Strategy table via `DataTable`: hide "Signals today" when all `symbols_scanned === 0` (rule 8) and header `Term('signals_today')`; status via `vocab.MODEL_STATUS`; legend panel keeps the raw enum in Advanced only. Entitlements collapse to "{ok} of {total} FMP endpoints available" with `Details`; de-duplicate `EARNINGS-CAL`. "cycle #N" → "scan #N since start" from `status.scanner.cycles` (single source). Event log numbers formatted with `fmtPrice`/`fmtNum`. Endpoint/Run tables get caption "A run = one full pass over the stock universe".
- **Advanced-only:** endpoint freshness table, runs table, raw legend.
- **Remove:** `.st` pills, `title=`.

### 3.6 `/accuracy` → **Accuracy board** — Group C
- **Question:** "For each strategy, do backtest, out-of-sample and paper results agree?"
- **API:** `/api/accuracy` rows `{id,name,color,version,paper_trades,backtest_win_rate,oos_win_rate,oos_sample,oos_expectancy_r,paper_win_rate,expectancy_r,profit_factor,max_drawdown_pct,avg_r,sample,risk_model}`, `sortable[]`, `note`.
- **Keep:** table, sort, how-to-read panel.
- **Change:** rows with all-null metrics collapse into "{n} strategies have no results yet" `Details`. Confidence column → per-row caption from `sample` via `vocab.SAMPLE_LABEL` (`INSUFFICIENT DATA`→"too few trades: {paper_trades} of 30 needed", `EARLY`→"early: {n} of 100", `MODERATE SAMPLE`→"moderate sample"); thresholds from `SAMPLE` constants (the backend uses 30/100). Default sort `paper_trades` desc. `profit_factor === 0 && paper_trades === 0` → "—". Three evidence columns get header `EvidenceTag`s: Backtest·Dev, Backtest·Holdout (OOS), Paper — never in one cell. Drawdown header "Worst dip (of $10k paper account)" (basis 'account' — it is the paper ledger). Color-dot legend line "color = strategy, same as elsewhere". `Tip` replaced by `Term`.
- **Remove:** `.badge neutral` sample enum, `title=`.

### 3.7 `/performance` → **Scorecard** — Group C (route exists; included for completeness)
- **Question:** "Overall, how often do picks work out?"
- **API:** `/api/performance` `{total_signals, outcomes{win,neutral,loss,pending,win_rate,by_type}, groups{score_band|catalyst|market_cap|strategy_version → {bucket: {n, win_rate, avg_change_pct, avg_max_gain_pct, avg_max_drawdown_pct}}}}`; `/api/report/canonical` (no profile) for the Paper tile; `/api/outcomes/noon`.
- **Change:** three headline `StatTile`s (Early pops TRACKED, Paper trades PAPER, Noon check TRACKED — definitions identical to §2.6 with source "All models"). One shared denominator line under the tiles: "{total_signals} recorded · {win+loss} decided · {neutral} flat · {pending} pending" with footnote "Today's page counts only the selected strategy; this page counts all models". Bucket "Win rate" → "Early pop rate (incl. flat)" with `Term('pop_rate_incl_flat')` unless the payload carries per-bucket win/neutral/loss, in which case recompute on the decided basis and add a "Decided n" column. Tables with <2 informative buckets auto-hidden, replaced by "{title}: no breakdown yet". `strategy_version` buckets → "engine v…". Header "Score band (out of 100)". Sign note under Avg max DD "negative = drop from pick price".
- **Advanced-only:** Avg max gain / Avg max DD columns, `by_type` table.

### 3.8 `/risk` → **Risk & position size** — Group D
- **Question:** "How much could I lose right now, and how big should a position be?"
- **API:** `/api/risk/settings` `{settings, defaults}`; `/api/risk/portfolio` `{portfolio{total_open_risk_pct, open_positions, total_open_risk}, limits{max_total_open_risk_pct}, headroom_pct, circuit_breaker{paused, blocks, warnings}, positions[]}`; POST `/api/risk/calculator`.
- **Keep:** everything (calculator, presets, groups, safety banner, portfolio meter).
- **Change:** four hero cards → `StatTile`s with `evidence="PAPER"`, `n = open_positions`, source "Paper positions · all strategies": "Open risk {x}% of {ceiling}% ceiling", "Open paper trades {n}", "Room for new trades {headroom}%", "Circuit breaker" as `StatusPill` ("Clear" buy / "Paused" risk). Calculator moves **above** the settings form in Simple (it is the beginner's tool); settings groups render in Simple as "Account" + "Risk per trade" only, the other three groups behind `Details "Show portfolio ceilings, circuit breakers and costs"`. Field labels from `FIELD_HELP` become visible `.hint` text (12px) instead of `Tip`; snake_case keys are humanised via `vocab.humanKey` ("max total open risk pct" → "Max total open risk (%)"). Results of the calculator keep `rm-kv` rows; "Limited by" value via `vocab.humanKey`.
- **Remove:** `Tip` on labels, `title=`.

### 3.9 `/reversion` → **Extreme Reversion (failed test)** — Group E
- **Question:** "What did the failed Extreme Reversion experiment show, and is it still producing paper signals?"
- **API:** `/api/reversion/signals?status&limit`, `/api/reversion/performance` `{paper{overall, by_*}, backtest{verdict, chosen, protocol, test_breakdowns, coverage}, separation_note}`, `/api/reversion/config`.
- **Keep:** four tabs, `TradeRoadmap` cards, MetricTables, backtest verdict panel, strategy lab.
- **Change:** h1 "Extreme Reversion" + permanent red `StatusPill` "Failed test — paper only" (the memory verdict: negative across 1,056 configs; never promoted); the `EXTREME_BB_RSI` badge → Advanced-only `<code>`. Tab order: **Backtest** (the verdict) first, then Live signals → renamed "Paper signals", Performance, Strategy lab. `Stat` → `StatTile` with `evidence` (Paper for paper stats; Backtest·Dev/Validation/Holdout for train/validation/holdout tiles via `split`). MetricTable → `DataTable` with `Sample` column via `vocab.SAMPLE_LABEL`, `profit_factor` null/0 with 0 trades → "—", `evidence` chip in the panel header. Empty-state copy kept (it is honest) but via `EmptyState`. Drawdowns in this page: `max_drawdown_pct` absent; if added, basis 'account'.
- **Remove:** `Tip` (→ `Term`), `.badge neutral` enums in Simple.

### 3.10 `/backtest` → **Backtests** — Group E
- **Question:** "What did the historical simulation of this strategy show, honestly?"
- **API:** `/api/backtest/report` `{available, note, config_hash, created_at, result{primary, coverage_notes, splits{dev,val,holdout}, search{dev_metrics,val_metrics,jitter_ok}, walk_forward{combined, folds?}, holdout{baseline,pessimistic}, configs_tested, rounds, converged_because, pbo, api_calls, cache_hits, tournament, mfe_decay}}`; `/api/backtest/reports` `{reports{fleet{result{cohort,sessions,date_range,trades_total,by_model,forward_only_models}}, nightly{result{replay, promotion{decision,reason}}}, primary?}}`.
- **Change:** each section renders only if its data object exists, else one gray line "Not in this report". Header "Report imported {fmtEtDate(created_at)} · config {config_hash}" ("config date" when `^\d{4}-\d{2}-\d{2}$`). Hard-coded "5 folds" → `walk_forward.folds?.length ?? '—'`; hard-coded coverage notes removed unless `coverage_notes` present. Splits table rows get `EvidenceTag split=` chips (Dev / Validation / Walk-forward / Holdout) instead of text; the metric cell `M` becomes "n={n} · {wr}% won · floor {wilsonLower or win_rate_lb}% · exp {x}% · PF {pf} · DD {dd}% sum of trade %". Fleet table: "WR (LB)" → two columns "Win rate" / "Conservative floor"; "Max DD" → "Sum of trade %" (basis 'trade_sum', §7.5); "Ambig." → "Unclear fills" (`Term('ambiguous')`); "H1 / H2 exp" → "First half / second half expectancy" (2 dp); date range "min → max". Nightly JSON → KV grid via `vocab.humanKey`; promotion line templated: "Paper sample {n}/{min} — {meets|below} the promotion minimum" computed client-side from `replay.paper_trades` and `promotion.min_trades ?? PROMOTION_MIN_TRADES_FALLBACK`; if `promotion.decision` contradicts that comparison, amber `StatusPill` "inconsistent — see System" and the raw `reason` in Advanced.
- `evidence="BACKTEST"` on every tile/table.

### 3.11 `/lab` → **Exit lab** — Group E
- **Question:** "Which exit rule kept the most of the gains in the backtest?"
- **API:** `/api/backtest/report` `result.tournament`, `result.mfe_decay`; `/api/backtest/reports` `reports.primary?.result.tournament` (same source as `/backtest`).
- **Change:** caption "From the imported backtest. For each way of exiting a trade, how much of the move it captured." Chart: y-axis with 3 gridlines and % labels, 12px legend text, x-axis title "Minutes after entry", colors via tokens. Table headers via `Term`: "Ambig." → "Unclear fills", "Ret/min" → "Return per minute held", "Pessimistic exp." → "Expectancy, worst-case fills", "WR (LB)" → "Win rate" + "Conservative floor", "Max DD" → "Sum of trade %". `EvidenceTag BACKTEST` in header. Empty state reads the same truth as `/backtest`: "The imported backtest ({config date}) has no exit tournament. Run the tournament to fill this page."

### 3.12 `/journal` → **My journal** — Group E
- **API:** `/api/journal` rows `{id, created_at, symbol, signal_uid, note, tags[], rules_followed, review}`; POST.
- **Change:** real `<label>`s ("Stock (optional)", "Tags", "Note"); "Rules followed" as a switch with helper "Did you follow your plan?"; entries with `signal_uid` show a chip "about {symbol} pick" opening `DetailDrawer`; "rules broken" pill → `StatusPill` risk "Plan not followed"; remove unused `apiGet` import; use `apiPostBody`.

### 3.13 `/watchlists` → **My watchlist & alerts** — Group E
- **API:** `/api/watchlists` rows `{id, name, symbols[]}`, PUT `/api/watchlists/{id}`; `/api/alerts` rows `{id, symbol, condition, price, active, fired_at, fired_price}`, POST/DELETE; `settings.scan_interval_sec`.
- **Change:** real labels ("Symbol", "Alert me when price…", "Price"); alerts `EmptyState` "No alerts yet — set one above"; fired alerts inline "Fired {fmtEtShort(fired_at)} at {fmtPrice(fired_price)}" (`StatusPill` buy); armed → neutral "Armed"; caption "checked every scan (about every {scan_interval_sec}s while the market is open)"; list selector shown when `rows.length > 1`; no fake "New list" button (PUT cannot create). Use `apiPut`/`apiPostBody` instead of raw fetch.

### 3.14 `/calendar` → **Calendar** — Group E
- **API:** `/api/calendar` `{earnings[{date,symbol,eps_est}], holidays[], half_days[], earnings_quality}`; `/api/candidates`, active signals, watchlist for highlighting.
- **Change:** earnings grouped by date ascending, dates via `fmtDateLabel` ("Thu Sep 4"); ≤8 chips per date prioritised to symbols present in candidates/signals/watchlist (cyan), "+N more" `Details`; OTC/ADR 5-letter tickers ending F/Y collapsed under "+N foreign/OTC"; chips open `DetailDrawer`. Closures: next 60 days by default with "Show full year"; a date that is both a holiday and an earnings date shows "(market closed)". `eps_est` shown as text "est. EPS {x}" in Advanced (was a title).

### 3.15 `/chart` → **Charts** — Group E (`app/chart/page.tsx`, `components/ChartPane.tsx`)
- **Question:** "What does this stock's price look like, and where are the important levels?"
- **Change:** Simple = 1-up only; controls in one row: Symbol, Timeframe (5m / 1h / 1d), one "Auto-mark levels" switch (S/R + patterns + markers together, labelled "Ceiling/Floor"), "Indicators ▾" menu holding the 6 checkboxes, "Tools ▾" menu holding drawings and Replay. Advanced = current 1/2/4-up and all controls; level labels "Ceiling — touched {n} times". Pattern badges append level and time. Quality badge → "Data: live" / "Data: unavailable". Default symbols from `/api/watchlists` rows[0].symbols when non-empty, else SPY. Chart hex colors → tokens read via `getComputedStyle` at mount.

### 3.16 `/settings` → **Settings** — Group E (`app/settings/page.tsx`, `components/ProfileEditor.tsx`)
- **Question:** "What rules is the scanner using, and can I pause it?"
- **API:** `/api/settings` `{settings, defaults, env_status, strategy_version}`, PUT; POST `/api/scanner/pause|resume`; `/api/profiles`.
- **Keep:** all fields, presets, toggles, ProfileEditor, env status.
- **Change (Simple):** top card = scanner state `StatusPill` + Pause/Resume button with the sentence "Pausing stops new scans; nothing is sold or bought." Then only the fields a beginner touches: "Buys allowed from (ET)" (`buy_confirm_after_et`), "Min score to buy" (`min_score_for_buy`), market-cap presets, "Include OTC". Everything else under `Details "Show all scanner rules"` (Universe, Liquidity, BUY gates, Scanner & budget, Modes, ProfileEditor). Group titles as questions ("Which stocks can it look at?", "How liquid must they be?", "When may it call a Buy?", "How often does it scan, and what may it spend?"). Add `early_window_min` and `early_win_gain_pct` to `AppSettings` (used here, missing from the type). "strategy {version}" meta → "engine v{strategy_version}". `env_status` → "Configured / Not configured" `StatusPill`s.
- **Remove:** `title=`, 13px `h2` overrides (use `SectionHeader`).

### 3.17 `/quant` → **Quant lab** — Group E (label only)
- Kept as-is behind Advanced; only change: nav label, and replace its local `L`/tip helper with `Term` where the text is not otherwise visible (optional, last).

---

## 4 Component vocabulary (Group A — `components/ui/*`, barrel `components/ui/index.ts`)

All exports below are named exports from `components/ui` (re-exported by the barrel). Shared types live in `lib/evidence.ts` and `lib/vocab.ts`.

```ts
// lib/evidence.ts
export type Evidence = 'BACKTEST' | 'PAPER' | 'TRACKED' | 'LIVE';
export type BacktestSplit = 'DEV' | 'VALIDATION' | 'WALK_FORWARD' | 'HOLDOUT';
export const SAMPLE = { dim: 10, judge: 30, rank: 5, calibrated: 50, early: 100 } as const;
export const PROMOTION_MIN_TRADES_FALLBACK = 100;
export type SampleClass = 'none' | 'tiny' | 'small' | 'ok';
export function sampleClass(n: number | null | undefined): SampleClass;          // 0/null→none, <10→tiny, <30→small
export function sampleNote(n: number | null | undefined, unit?: string): string | null; // "Too few to judge (7 trades)" | "No trades yet" | null
export function wilsonLower(wins: number, n: number, z?: number): number | null;  // 0..1, null when n===0
export function evidenceLabel(e: Evidence, split?: BacktestSplit, paperMode?: boolean): string; // "Backtest · Holdout", "Paper", "Tracked", "Live (real money)"
export type DrawdownBasis = 'account' | 'trade_sum';
export function drawdownLabel(basis: DrawdownBasis): string; // "of $10,000 account" | "sum of trade %"
```

### 4.1 `Tone`
`export type Tone = 'buy' | 'risk' | 'warn' | 'early' | 'accent' | 'neutral' | 'backtest'` (in `lib/vocab.ts`). Maps to tokens `--buy/--risk/--warn/--early/--accent/--text-dim/--backtest`.

### 4.2 `StatTile`
```ts
export interface StatTileProps {
  label: string;                       // plain English, sentence case
  value: React.ReactNode | null;       // null → "—"
  n: number | null | undefined;        // population; drives the sample rule
  unit?: string;                       // 'trades' (default) | 'picks' | 'stocks'
  nLabel?: React.ReactNode;            // overrides "{n} {unit}", e.g. "of 375 decided picks · 537 flat not counted"
  source: string;                      // "Paper account · Primary model · Buy picks only"
  evidence: Evidence;                  // required — renders EvidenceTag
  split?: BacktestSplit;
  tone?: Tone;
  sub?: React.ReactNode;               // one extra 12px line
  term?: string;                       // glossary key → label gets a Term popover
  loaded?: boolean;                    // default true; false → skeleton, never "0"
  size?: 'md' | 'lg';                  // 26px / 32px value
  href?: string;                       // whole tile is a link
  id?: string;
}
export function StatTile(props: StatTileProps): JSX.Element;
```
Behaviour: `n === 0 || n == null` → value "—", n-line "No {unit} yet"; `n < SAMPLE.dim` → value in `--text-dim`, headline replaced by `sampleNote`; `n < SAMPLE.judge` → amber `sampleNote` line under the value. DOM: `.stat > .stat-label .stat-value .stat-n .stat-src .stat-sub` + `EvidenceTag` top-right. Never uses `title`.

### 4.3 `StatusPill`
```ts
export interface StatusPillProps {
  label: string; tone: Tone;
  raw?: string;            // Advanced-only <code> suffix, also used when a mapping is unknown
  glow?: boolean;          // only AttentionChips may set true
  size?: 'sm' | 'md';      // 12px / 13px
  href?: string; onClick?: () => void;
  icon?: React.ReactNode;
}
export function StatusPill(props: StatusPillProps): JSX.Element;
export function pillFor(map: Record<string, { label: string; tone: Tone }>, raw: string | null | undefined): StatusPillProps; // unknown → { label: 'Unknown', tone: 'neutral', raw }
```
Replaces `.badge.*`, `.st.*`, `.phase-chip`, `.regime-chip` usages.

### 4.4 `SectionHeader`
```ts
export interface SectionHeaderProps {
  title: React.ReactNode;              // the heading text (h2 by default)
  question: string;                    // plain-English question, 13px --text-dim under the heading (collapsed in Advanced via CSS)
  count?: number | null;               // appended "— {count}" ("— none" when 0)
  caption?: React.ReactNode;           // 12px source line ("Tracked · Primary model · …")
  evidence?: Evidence; split?: BacktestSplit;
  note?: React.ReactNode;              // 12px header note (e.g. score thresholds, suppression note)
  right?: React.ReactNode;             // actions slot
  id?: string; level?: 1 | 2;
}
export function SectionHeader(props: SectionHeaderProps): JSX.Element;
```

### 4.5 `Drawer` and `DetailDrawer`
```ts
export interface DrawerProps {
  open: boolean; onClose: () => void;
  title: React.ReactNode; subtitle?: React.ReactNode;
  width?: number;                      // default 780
  footer?: React.ReactNode; children: React.ReactNode;
}
export function Drawer(props: DrawerProps): JSX.Element | null;
```
Esc closes, veil click closes, focus trap, body scroll lock, `role="dialog" aria-modal="true"`. `components/DetailDrawer.tsx` keeps its signature `{ symbol: string; onClose: () => void }` (Group A rewrites it on `Drawer`: sections "The plan" (buy/stop/targets), "Why it was picked" (`explain[]` as pass/fail list via `WhatsMissing full`), "Company", "News & filings", "Price history"; all `.kv .k` labels via `vocab`; Advanced adds features/score_detail).

### 4.6 `Tip`, `Term` and the Tip usage rule
```ts
export function Tip({ text, label }: { text: string; label?: string }): JSX.Element;  // ⓘ button; popover on click/focus/Esc; text NOT in DOM until open
export function Term({ k, children }: { k: string; children?: React.ReactNode }): JSX.Element; // dotted word; popover text = PLAIN[k] ?? TERMS[k] ?? GLOSSARY[k]; children default to the glossary key
```
**Rule:** `Tip`/`Term` are supplementary only. They may never be the sole carrier of a value, state, unit, denominator or warning. `Term` is for glossary words in headers/labels; `Tip` is for a one-off sentence next to a form label. Neither is allowed on a `StatusPill` or inside a table cell in Simple. `components/TradeRoadmap.tsx` re-exports `Tip` from `components/ui` (signature changes from `{term, children}` to `{text, label}`; Group A updates its two internal call sites).

### 4.7 `EmptyState`
```ts
export interface EmptyStateProps {
  headline: string;
  reason: string | null | undefined;   // null → "No reason reported by the scanner."
  next?: React.ReactNode;              // "Buys can appear from 07:00 ET …"
  action?: { label: string; href?: string; onClick?: () => void };
  tone?: 'neutral' | 'warn' | 'risk';
  loaded?: boolean;                    // false → skeleton instead
  compact?: boolean;
}
export function EmptyState(props: EmptyStateProps): JSX.Element;
```

### 4.8 `EvidenceTag`, `ScorePill`, `WhatsMissing`
```ts
export function EvidenceTag({ evidence, split, paperMode }: { evidence: Evidence; split?: BacktestSplit; paperMode?: boolean }): JSX.Element;
// LIVE renders "Live (real money)" only when paperMode === false; otherwise falls back to "Paper" (dev console.warn)
export function ScorePill({ value, minBuy, words }: { value: number | null | undefined; minBuy?: number; words?: boolean }): JSX.Element;
// "72 / 100 · Strong" (words default true in Simple, false in Advanced); bands: ≥minBuy (default 75) Strong, ≥55 OK, else Weak
export interface WhatsMissingProps {
  explain?: CandidateRow['explain']; hardBlocks?: string[];
  symbol?: string; lazy?: boolean;     // lazy: fetch /api/candidates/{symbol} on mount/expand and read live.explain
  full?: boolean;                      // render every item as a pass/fail list (drawer); default: first failing + "+N more"
}
export function WhatsMissing(props: WhatsMissingProps): JSX.Element;
// output: "{label}: {actual ?? '—'} (need {required})"; hard block → red "Blocked: {gateLabel}"; nothing failing → green "Passes every check"; no data → "Not in today's scan"
```

### 4.9 `DataTable`
```ts
export interface Column<T> {
  key: string; header: React.ReactNode; term?: string; align?: 'l' | 'r';
  simple?: boolean;                    // shown in Simple; default false (Advanced only)
  cell: (row: T) => React.ReactNode;
  isEmpty?: (row: T) => boolean;       // suppression test; default: cell returns null/undefined/''/'—'
  sortValue?: (row: T) => number | string | null;
  width?: number;
}
export interface DataTableProps<T> {
  rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  defaultSort?: { key: string; dir: 'asc' | 'desc' };
  cap?: number;                        // Simple-only row cap → footer "Show all {n}"
  suppressEmptyAbove?: number;         // default 0.8 — Simple only
  suppressedNote?: (hidden: Column<T>[]) => React.ReactNode; // default "{names} hidden — empty for most rows"
  note?: React.ReactNode; evidence?: Evidence; split?: BacktestSplit;
  empty?: React.ReactNode; loaded?: boolean; minWidth?: number; dense?: boolean;
  rowClassName?: (row: T) => string | undefined;
}
export function DataTable<T>(props: DataTableProps<T>): JSX.Element;
```
Suppression is computed over all `rows` (not the capped subset). Headers never carry `title`. Sorting by click stays.

### 4.10 `SignalTable`, `StrategyScope`, `Details`, `Advanced`, `ModeToggle`, `GlossaryPanel`
```ts
export interface SignalTableProps {
  rows: SignalRow[]; onSelect: (r: SignalRow) => void;
  variant: 'watch' | 'buy' | 'mixed'; scope: string; loaded: boolean;
  cap?: number; marketClosed?: boolean; minScoreForBuy?: number;
  showWhatsMissing?: boolean;          // default true for variant 'watch' in Simple
  emptyState?: React.ReactNode;
}
export function SignalTable(props: SignalTableProps): JSX.Element;   // components/SignalTable.tsx (default export kept)
export function StrategyScope(): JSX.Element;                        // components/StrategyScope.tsx, replaces ProfileTabs
export function Details({ summary, children, defaultOpen }: { summary: string; children: React.ReactNode; defaultOpen?: boolean }): JSX.Element;
export function Advanced({ children, fallback }: { children: React.ReactNode; fallback?: React.ReactNode }): JSX.Element | null;
export function SimpleOnly({ children }: { children: React.ReactNode }): JSX.Element | null;
export function ModeToggle(): JSX.Element;
export function GlossaryPanel({ open, onClose }: { open: boolean; onClose: () => void }): JSX.Element | null;
```

### 4.11 `lib/status.tsx`, `lib/mode.tsx`, `lib/api.ts` additions
```ts
export function StatusProvider({ children }): JSX.Element;   // mounted in app/layout.tsx
export function useStatus(): { status: StatusPayload | null; err: Error | null; loaded: boolean; reload: () => void };
export function useOps(): { ops: Ops | null; loaded: boolean };
export function useAppSettings(): { settings: AppSettings | null; loaded: boolean; reload: () => void };
export function useStream(handlers: Record<string, (d: any) => void>): boolean; // one shared EventSource, multiplexed
export function useMarketPhase(): { key: 'premarket' | 'open' | 'afterhours' | 'closed' | 'prep' | 'unknown'; label: string; tone: Tone; isOpen: boolean; isPremarket: boolean };
export function useScannerState(): { key: 'running' | 'sleeping' | 'paused' | 'problem' | 'starting' | 'unreachable'; label: string; tone: Tone };
// lib/mode.tsx
export type Mode = 'simple' | 'advanced';
export function ModeProvider({ children }): JSX.Element;
export function useMode(): { mode: Mode; setMode: (m: Mode) => void; advanced: boolean; mounted: boolean };
// lib/api.ts
export function usePollingState<T>(path: string, ms: number): { data: T | null; err: Error | null; loaded: boolean; reload: () => void };
```
`lib/types.ts` gains the interfaces currently inlined in components/pages: `Ops`, `Canonical`, `Noon`, `Digest`, `Brief`, `CompetitionCard`, `Competition`, `ModelInfo`, `Position`, `Rejected`, `RadarRow`, `FeedRow`, `AlertRow`, `WatchlistRow`, `JournalEntry`, `AccuracyRow`, `PerfPayload`, `HealthDetail`, `StrategyHealth`; `AppSettings` gains `early_window_min: number; early_win_gain_pct: number`.

### 4.12 `lib/vocab.ts` tables (Simple labels; the raw enum is Advanced-only)
| Export | Raw → Label |
|---|---|
| `PHASE` | premarket→Premarket (early); regular/open→Market open (buy); afterhours→After hours; closed→Market closed; prep→Getting ready |
| `SIGNAL_STATUS` | active→Open; closed→Closed; invalidated→Dropped |
| `OUTCOME` | win→Popped; loss→Didn't; neutral→Flat; pending→Pending |
| `LIFECYCLE` | DISCOVERED→Found; EARLY_WATCH→Early watch; QUALIFIED_WATCH→Watching; ACTIONABLE_BUY→Buy pick; REJECTED→Blocked; INVALIDATED→Dropped; EXPIRED→Expired; CLOSED→Closed; DATA_ERROR→Data error |
| `MODEL_STATUS` | LIVE→Paper trading (or "Live" iff `paper_mode===false`); PAPER LIVE / PAPER_LIVE→Paper trading; WAITING→Waiting for a setup; NO_DATA→No data (warn); OFFLINE→Offline (risk); ERROR→Error (risk); DISABLED→Off; UNKNOWN→Unknown |
| `LANE_STATE` | RUNNING→Running; OPEN→Market open; RUNNING 24/7→Running around the clock; DONE TODAY→Done for today; SCHEDULED→Scheduled; DAILY MODELS ONLY→Daily models only; IDLE (no session)→Idle until market; CLOSED→Market closed; HIGH_RISK→High risk (risk) |
| `REGIME` | trend→Trending; range→Range-bound; event→Event-driven; high_risk→High risk; uncertain→Uncertain |
| `NOON_CLASS` | WIN_10_TOUCH→+10% touch; WIN_NOON_GREEN→green at noon; LOSS_NOON_RED→red at noon; FLAT→flat; INCOMPLETE→incomplete |
| `SAMPLE_LABEL` | INSUFFICIENT DATA→too few trades ({n} of 30 needed); EARLY→early ({n} of 100); MODERATE SAMPLE→moderate sample |
| `GATE` (explain keys + gate names + hard blocks) | score→Score; rvol→Volume vs normal; pm_volume→Premarket volume; pm_dollar→Premarket $ volume; spread→Spread; fresh/stale_quote→Fresh price; catalyst→Verified news; confirm→Price confirmation; window→Broker premarket window; blocks→Hard blocks; spread_above_max→Spread too wide; incomplete_live_volume→Volume data incomplete; critical_data_disagreement→Data sources disagree; severe_actionable_dilution→Severe dilution; unresolved_halt→Trading halt; `*_gate` suffix stripped |
| `BOARD` | return→Highest return; win_rate→Most often right; drawdown→Smallest worst dip |
| `POSITION_STATUS` | open→Open (buy); closed→Closed |
| `ITEM_CODES` | 1.01 New agreement; 1.02 Ended agreement; 1.03 Bankruptcy; 2.01 Asset deal; 2.02 Results of operations; 2.03 New debt; 2.04 Debt trigger; 2.05 Restructuring; 2.06 Impairment; 3.01 Listing notice; 3.02 Unregistered sale; 3.03 Holder rights change; 4.01 Auditor change; 4.02 Restatement; 5.01 Control change; 5.02 Executive/board change; 5.03 Bylaw change; 5.07 Shareholder vote; 7.01 Regulation FD disclosure; 8.01 Other event; 9.01 Exhibits |
| functions | `phaseLabel`, `catalystLabel(t)` (known keys else Title Case of raw, ''/none→"No news found"), `gateLabel(k)`, `plainGate(why)` (regex rewrite of `gate_why` strings; fallback raw), `humanKey(k)` (snake → sentence case with unit suffixes), `scoreBand(v, minBuy)` |

---

## 5 Visual system deltas (`app/globals.css`)

Tokens to add on `:root`:
```
--backtest: #a78bfa; --backtest-soft: rgba(167,139,250,.14);   /* violet — backtest evidence */
--paper: var(--accent); --paper-soft: var(--accent-soft);
--tracked: var(--early); --tracked-soft: var(--early-soft);
--live: #f472b6; --live-soft: rgba(244,114,182,.14);           /* real money only */
--fs-eyebrow: 11px; --fs-note: 12px; --fs-body: 13px; --fs-lead: 14px; --fs-h2: 16px; --fs-h1: 20px; --fs-stat: 26px; --fs-hero: 32px;
--sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px; --sp-5: 24px; --sp-6: 32px;
--glow-buy: 0 0 0 1px rgba(52,211,153,.45), 0 0 18px rgba(52,211,153,.35);
--r-sm: 8px; --r-pill: 999px;
```
Type scale (only these sizes may appear): 11 eyebrow (uppercase only) · 12 note/caption/pill · 13 body/table · 14 lead · 16 h2 · 20 h1 · 26 stat · 32 hero. Table `td` 13px (was 12.8), `th` 11px uppercase (was 10.5), `.badge` 12px (was 10.5; `.est`/`.src` 12px, no 9/9.5), `.fresh` 12px, `.kv .k` 11px, `.disclaimer` 12px, `.side-foot` 12px, `.card h3` 11px, `.rm-now-lbl/.rm-sec-lbl/.rm-tf/.rm-lv > span` 11px, `.rm-lv small/.rm-score-lbl/.rm-tgt-alloc/.field .hint/.gate-why/.tl-time/.co-name` 12px, `.tipwrap .tip` 12.5px, `.st` 12px, `.phase-chip` 12px, `.brand small` deleted. Spacing: sections `margin: var(--sp-6) 0 var(--sp-3)`; cards `padding: var(--sp-4) var(--sp-5)`; tables `td/th padding: 10px 12px`.

New classes: `.eyebrow`, `.stat .stat-label .stat-value .stat-n .stat-src .stat-sub .stat--dim`, `.pill .pill--{tone} .pill--sm .pill--glow` (glow uses `--glow-buy`), `.evtag .evtag--{backtest|paper|tracked|live}`, `.sect-q`, `.tbl-note`, `.showall`, `.chips .chip`, `.empty-card .empty-h .empty-reason .empty-next`, `.details > summary`, `.mode-toggle .mode-toggle__opt.is-on`, `.popover` (shared by Tip/Term/Glossary), `.skel-tile`, `.adv-only`. Mode CSS: `html[data-mode="simple"] .adv-only { display: none !important }`, `html[data-mode="advanced"] .sect-q { display: none }`.

Semantic color rules: green (`--buy`) = Buy pick / positive change / pass; red (`--risk`) = loss / blocked / problem; amber (`--warn`) = caution, too-few-to-judge, paused, stale; cyan (`--early`) = watching/tracked/early; accent blue = paper/interactive; violet = backtest; pink = real money. `--warn` is never used for "Watching". Remove `cursor: help` and hover-only `.tipwrap:hover .tip` (popover is click/focus). Remove `.tbl th { cursor:pointer }` title affordance; keep sort.

---

## 6 Simple/Advanced mechanism

- **State:** `ModeProvider` in `app/layout.tsx`; `useMode()` returns `{ mode, setMode, advanced, mounted }`. Initial render is `simple` (SSR-safe); on mount read `localStorage.tf_mode`, then `?mode=simple|advanced` in the URL overrides and persists. Sets `document.documentElement.dataset.mode`. Cross-tab sync via `storage` event + `window` CustomEvent `tf-mode`.
- **Persistence:** `localStorage` key `tf_mode`; default `simple` when absent; Onboarding step 5 writes it; ModeToggle writes it.
- **Hiding:** prefer `<Advanced>` (returns null in Simple → no fetch, no DOM); use `.adv-only` only for static markup. `<Details>` reveals a section's Advanced content locally without flipping the global mode. `DataTable` reads `advanced` to pick columns (`simple: true` columns in Simple; all in Advanced) and to apply cap/suppression (Simple only). `ScorePill` shows words in Simple. `StatusPill` shows `raw` only in Advanced. `SectionHeader.question` hidden in Advanced.
- **What hides in Simple:** nav groups/links marked `advanced`; TopBar items 6–12; `/` sections 2.8–2.11, digest/brief prose, "Best open Buy pick", noon breakdown; all Advanced columns; raw enums, item codes, accession links, policy ids, version strings except "engine v…"; `include_demo` switch; 2/4-up charts and expanded controls; risk/settings deep groups (behind `Details`).
- **Never hidden:** the disclaimer, evidence chips, n-lines, sample warnings, drawdown basis words, the scanner state, the Failed-test pill.

---

## 7 Honesty rules (enforced structurally)

1. **n on every stat.** `StatTile.n` is required (nullable, but must be passed); leaderboard rows render "· {n} trades"; table headers with rates carry the denominator in `note`. Lint: grep for `.big` — none may remain outside `StatTile`.
2. **Cohort labels.** Every `StatTile`, `DataTable` with result columns, leaderboard and backtest split carries exactly one `EvidenceTag`. Backtest splits are chips (Backtest · Dev / Validation / Walk-forward / Holdout), not text suffixes. Source captions name the scope ("Primary model" / "All models") and the population ("Buy picks only" / "watches and buys").
3. **Small-sample warnings.** `SAMPLE.dim=10`, `SAMPLE.judge=30`, `SAMPLE.rank=5` from `lib/evidence.ts`; `calibration !== 'calibrated'` → sentence "Not enough trades yet to trust this rate"; ranking excludes `< 5` trades with the reason shown; never-traded accounts are never ranked or shown as "$10,000 · +0%".
4. **No mixed cohorts.** A tile/table/leaderboard has one `evidence`; "Buy picks" counts only `signal_type==='buy'`; "Tracked signals" is the word for `status.active_signals`; Early pops and Noon check state the excluded flats/pending; the digest prose is Advanced-only and guarded against `canonical` counts.
5. **Drawdown basis table** (`DrawdownBasis` per field): `/api/competition cards[].max_drawdown_pct`, `/api/models account.max_drawdown_pct`, `/api/accuracy max_drawdown_pct` → `account` ("of $10,000 account"); `/api/backtest/report` `*.max_drawdown_pct`, `reports.fleet.by_model[].max_drawdown_pct`, `tournament[].baseline.max_drawdown_pct` → `trade_sum` ("sum of trade %"). Any new field defaults to `trade_sum` and Advanced-only until the backend basis is confirmed.
6. **"Live" reserved.** The word Live appears only when `status.paper_mode === false` (EvidenceTag LIVE) or for a data feed explicitly written "Live feed". Signal outcomes are "Tracked".
7. **Conservative floor.** Wilson lower bound is labelled "Conservative floor", computed client-side with `wilsonLower(wins, n)` whenever wins and n are available (API `win_rate_lb` used only when they are not), Advanced-only except on `/backtest` and `/lab` where it is a column.
8. **Loading is not zero.** No component renders 0/none/EmptyState until `loaded`.
9. **Unknown enums are visible.** `pillFor` renders unknown values as neutral "Unknown" with the raw string in `<code>`; nothing is silently swallowed.
10. **Counts reconcile or say so.** `reconciliation.equals_total === false` shows a red pill on `/` (Advanced) and a problem line on `/health`.
11. **Settings are read, not hard-coded.** `buy_confirm_after_et`, `min_score_for_buy`, `quote_freshness_sec`, `early_window_min`, `scan_interval_sec` come from `useAppSettings()`.
12. **Nothing only in hover.** Zero `title=` attributes in `app/` and `components/` (CI grep).

---

## 8 Ordered implementation checklist (grouped by file; groups are disjoint)

### Group A — shared components, tokens, navigation (must land first; B–E code against the signatures in §4)
1. `lib/evidence.ts` — types, `SAMPLE`, `wilsonLower`, `sampleClass`, `sampleNote`, `evidenceLabel`, `DrawdownBasis`, `drawdownLabel`.
2. `lib/vocab.ts` — all tables and functions in §4.12; `Tone` type.
3. `lib/types.ts` — move/add interfaces listed in §4.11; extend `AppSettings`.
4. `lib/api.ts` — add `usePollingState`; keep existing exports.
5. `lib/format.ts` — add `fmtEtClock(iso, {seconds?})`, `fmtEtShort(iso)` ("Tue 7:12 AM ET"), `fmtDateLabel('YYYY-MM-DD')` ("Thu Sep 4"), `fmtR(v)` ("+0.4R"), `fmtMult(v)` ("3.2×"), `fmtAgo(iso)`, `fmtDrawdown(v, basis)`, `fmtMoney` (re-export of `money`).
6. `lib/mode.tsx` — `ModeProvider`, `useMode`.
7. `lib/status.tsx` — `StatusProvider`, `useStatus`, `useOps`, `useAppSettings`, `useStream`, `useMarketPhase`, `useScannerState`.
8. `lib/terms.ts` — add `PLAIN` map (keys: early_pop, noon_check, conservative_floor, r_multiple, paper, tracked, backtest, drawdown_account, drawdown_sum, gap, score_plain, pipeline, regime, ceiling_floor, whats_missing, ambiguous, legacy_bucket, pop_rate_incl_flat, signals_today).
9. `app/globals.css` — §5 tokens, sizes, new classes, mode CSS; remove sub-12px sizes and hover tooltip.
10. `components/ui/` — `StatTile`, `StatusPill` (+`pillFor`), `SectionHeader`, `Drawer`, `Tip`, `Term`, `EmptyState`, `EvidenceTag`, `ScorePill`, `WhatsMissing`, `DataTable`, `Details`, `Advanced`, `SimpleOnly`, `ModeToggle`, `GlossaryPanel`, `index.ts` barrel. Publish the barrel with typed stubs on day 1.
11. `components/Score.tsx` → re-export `ScorePill` (default export kept for compatibility). `components/TradeRoadmap.tsx` → re-export `Tip` from ui; update its two `Tip` call sites to `{text}`.
12. `components/SignalTable.tsx` — rewrite on `DataTable` per §4.10/§2.4; keep default export.
13. `components/StrategyScope.tsx` (new) — §2.0; `components/ProfileTabs.tsx` becomes a re-export.
14. `components/DetailDrawer.tsx` — rebuild on `Drawer`, `WhatsMissing full`, `Term`; remove `title=`.
15. `components/Freshness.tsx` — text output "as of 4:02 PM" + optional dot; props `{ ts, fresh?, marketOpen?: boolean, thresholdSec?: number }`; no `title`.
16. `components/Nav.tsx` — §1.1, export `NAV_GROUPS`.
17. `components/TopBar.tsx` — §1.2.
18. `components/GlossaryFab.tsx` → `GlossaryPanel` (keep file, remove fixed FAB).
19. `components/Onboarding.tsx` — §1.3.
20. `app/layout.tsx` — title "TradeFinder"; wrap in `ModeProvider` + `StatusProvider`; remove `GlossaryFab`.
21. CI grep gates: no `title=` in `app/`/`components/`; no `fontSize: (9|9.5|10|10.5|11.5)`; no `.big` outside `StatTile`.

### Group B — Command Center (`app/page.tsx` and its components)
1. `components/StatusLine.tsx` (new) — §2.1 lines 1–2 + Advanced brief/digest with guard.
2. `components/AttentionChips.tsx` (new) — §2.1 chip rules, max 4, only `glow` user.
3. `components/SessionStrip.tsx` — §2.2; props `{ confirmAt: string | null; loaded: boolean }`.
4. `components/PickCard.tsx`, `components/BuyPicks.tsx` (new) — §2.3.
5. `components/CandidateTable.tsx` — rewrite on `DataTable`, §2.5; props `{ rows, updatedSyms, onSelect, loaded, minScoreForBuy, phaseKey, quietReason, lastCycleAt }`; remove its own `/api/ops` poll.
6. `components/TrustTiles.tsx` (new) — §2.6; props `{ canonical, status, noon, scopeLabel, earlyWindowMin, loaded }`.
7. `components/PositionsTable.tsx` — `DataTable`, §2.7; props `{ rows, loaded, scopeLabel, onSelect, liveBySymbol }`.
8. `components/FunnelStrip.tsx` — §2.8 (Advanced).
9. `components/OpsPanel.tsx` — §2.9 (Advanced); reads `useOps()`.
10. `components/NoonCard.tsx` — becomes the Advanced noon detail under TrustTiles.
11. `components/DigestCard.tsx` — folded into StatusLine (delete after 1).
12. `components/RejectedTable.tsx`, `components/RadarTable.tsx` — `DataTable`, vocab, Advanced.
13. `app/page.tsx` — compose in §2 order with `id`s `buys`, `watching`, `scanner`, `trust`, `positions`; single data layer per the table in §2; skeletons until `loaded`; disclaimer.

### Group C — results pages
1. `app/competition/page.tsx` — §3.1.
2. `app/accuracy/page.tsx` — §3.6.
3. `app/models/[id]/page.tsx` — §3.2.
4. `app/performance/page.tsx` — §3.7.

### Group D — activity and setup pages
1. `app/signals/page.tsx` — §3.3 (URL params `type`, `status`).
2. `app/feed/page.tsx` — §3.4.
3. `app/health/page.tsx` — §3.5.
4. `app/risk/page.tsx` — §3.8.

### Group E — research, personal and configuration pages
1. `app/reversion/page.tsx` — §3.9.
2. `app/backtest/page.tsx` — §3.10.
3. `app/lab/page.tsx` — §3.11.
4. `app/journal/page.tsx` — §3.12.
5. `app/watchlists/page.tsx` — §3.13.
6. `app/calendar/page.tsx` — §3.14.
7. `app/chart/page.tsx`, `components/ChartPane.tsx` — §3.15.
8. `app/settings/page.tsx`, `components/ProfileEditor.tsx` — §3.16.
9. `app/quant/page.tsx` — label/Term only (§3.17), last.

### Acceptance artifact — relocation table (nothing deleted, only moved)
| Old element | New home |
|---|---|
| TopBar "Last cycle" | Scanner pill → `/health`; `/` Advanced System detail |
| TopBar "Candidates N" | TopBar "Scanning now · N candidates" (open) / Scanner panel line (closed) |
| TopBar "Active BUY 606" | Advanced TopBar "Tracked signals (all models)" |
| TopBar API/min, throttles, AI cost, Σ paper, regime, version | Advanced TopBar |
| Sidebar Finders list (21) | Strategies → "All strategies (N)" collapsible |
| Glossary FAB | TopBar `?` → `GlossaryPanel` |
| OpsPanel headline / lanes / next up / intentionally off | StatusLine (Simple) / System detail (Advanced) |
| DigestCard line + morning brief | StatusLine Advanced third line (guarded) |
| FunnelStrip counts, reconciliation, versions, WR/LB/UNCALIBRATED | Pipeline (Advanced); TrustTiles tile 1 + Conservative floor (Advanced) |
| KPI "Active BUY Signals" | Buy picks header count |
| KPI "Best Current Performer" | Advanced StatTile "Best open Buy pick" (buys only) |
| KPI "Highest-Score Candidate" | Scanner table default sort (score desc) |
| KPI "Scanner Hit Rate" W/N/L | TrustTiles tile 2 "Early pops" |
| KPI "Scanner Health" cycle # | TopBar scanner pill; `/health` "scan #N since start" |
| SignalTable Stop/T1/T2, Hi/Lo, Max gain/DD, Result, Status, Found @ | Advanced columns; "Result" → "Early pop?"; "Found @" → "First seen" |
| NoonCard | TrustTiles tile 3 (Simple) + noon detail (Advanced) |
| RejectedTable, RadarTable | Advanced, collapsed / hidden-when-empty |
| Competition "marked Ns ago", season, `EXP`, `.st` enum | Advanced section caption; word "experimental"; `MODEL_STATUS` pills |
| Accuracy Confidence badge | per-row sample caption |
| Backtest hard-coded coverage notes, "5 folds" | payload-driven or "Not in this report" |
| Fleet/Lab "WR (LB)" | "Win rate" + "Conservative floor" |
| Feed `items 2.02,9.01` | `ITEM_CODES` chips (raw in Advanced) |
| Settings deep groups, ProfileEditor | `Details "Show all scanner rules"` |
| Risk ceilings/breakers/costs groups | `Details` under the calculator |
| Chart 2/4-up, drawing tools, replay, per-indicator boxes | Advanced layout; "Indicators ▾" / "Tools ▾" menus |
| Reversion `EXTREME_BB_RSI` badge | Advanced `<code>`; page carries "Failed test — paper only" pill |
| All `title=` tooltips (76) | `Term`/`Tip` popovers, inline 12px text, or drawer |