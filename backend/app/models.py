"""Persistence model. Append-only where the spec demands immutability."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Symbol(Base):
    __tablename__ = "symbols"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    exchange: Mapped[str] = mapped_column(String(32), default="")
    cik: Mapped[str] = mapped_column(String(16), default="", index=True)
    security_type: Mapped[str] = mapped_column(String(32), default="common")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SymbolReferenceVersion(Base):
    """Point-in-time snapshot of reference data (float, outstanding, cap...)."""
    __tablename__ = "symbol_reference_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    market_cap: Mapped[float] = mapped_column(Float, nullable=True)
    float_shares: Mapped[float] = mapped_column(Float, nullable=True)
    shares_outstanding: Mapped[float] = mapped_column(Float, nullable=True)
    avg_volume: Mapped[float] = mapped_column(Float, nullable=True)
    sector: Mapped[str] = mapped_column(String(128), default="")
    industry: Mapped[str] = mapped_column(String(128), default="")
    country: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class MarketBar(Base):
    """Own accumulated minute bars (provider or derived) for RVOL baselines."""
    __tablename__ = "market_bars"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16))
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session_date: Mapped[str] = mapped_column(String(10))  # ET trading date YYYY-MM-DD
    minute_of_day: Mapped[int] = mapped_column(Integer)    # minutes after 00:00 ET
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="fmp_1min")
    __table_args__ = (
        UniqueConstraint("symbol", "session_date", "minute_of_day", "source", name="uq_bar"),
        Index("ix_bars_symbol_date", "symbol", "session_date"),
    )


class LiveQuote(Base):
    __tablename__ = "live_quotes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    provider_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    price: Mapped[float] = mapped_column(Float, nullable=True)
    bid: Mapped[float] = mapped_column(Float, nullable=True)
    ask: Mapped[float] = mapped_column(Float, nullable=True)
    volume: Mapped[float] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(48), default="fmp_quote")


class ScannerRun(Base):
    __tablename__ = "scanner_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    phase: Mapped[str] = mapped_column(String(24), default="premarket")
    status: Mapped[str] = mapped_column(String(24), default="running")  # running|ok|error
    universe_size: Mapped[int] = mapped_column(Integer, default=0)
    shortlisted: Mapped[int] = mapped_column(Integer, default=0)
    enriched: Mapped[int] = mapped_column(Integer, default=0)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("scanner_runs.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    session_date: Mapped[str] = mapped_column(String(10), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    score: Mapped[float] = mapped_column(Float, default=0)
    qualified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="candidate")  # candidate|shortlist|blocked|buy
    block_reasons: Mapped[list] = mapped_column(JSON, default=list)


class CandidateFeatureSnapshot(Base):
    __tablename__ = "candidate_feature_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    score_detail: Mapped[dict] = mapped_column(JSON, default=dict)


class NewsItem(Base):
    __tablename__ = "news_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="news")  # news|press_release
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(128), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    headline: Mapped[str] = mapped_column(Text, default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    is_original: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("content_hash", name="uq_news_hash"),)


class Catalyst(Base):
    """AI classification of a news/filing bundle. Cached by content hash."""
    __tablename__ = "catalysts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    direction: Mapped[str] = mapped_column(String(12), default="neutral")
    materiality: Mapped[int] = mapped_column(Integer, default=0)
    novelty: Mapped[str] = mapped_column(String(12), default="unrelated")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    catalyst_type: Mapped[str] = mapped_column(String(64), default="")
    dilution_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    going_concern_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|pending|failed


class SecFiling(Base):
    __tablename__ = "sec_filings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    cik: Mapped[str] = mapped_column(String(16), index=True)
    accession: Mapped[str] = mapped_column(String(32), index=True)
    form_type: Mapped[str] = mapped_column(String(16), index=True)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    items: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    primary_doc_url: Mapped[str] = mapped_column(Text, default="")
    doc_hash: Mapped[str] = mapped_column(String(64), default="")
    __table_args__ = (UniqueConstraint("accession", "symbol", name="uq_filing"),)


class BuySignal(Base):
    """Immutable initiation facts; live-tracking columns updated separately.
    buy_signal_price / initiated_at / score & evidence snapshots are never rewritten."""
    __tablename__ = "buy_signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_uid: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    session_date: Mapped[str] = mapped_column(String(10), index=True)
    strategy_version: Mapped[str] = mapped_column(String(24))
    catalyst_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    initiated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    buy_signal_price: Mapped[float] = mapped_column(Float)
    price_source: Mapped[str] = mapped_column(String(96), default="")
    provider_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    score_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    # live-tracking (mutable) columns:
    current_live_price: Mapped[float] = mapped_column(Float, nullable=True)
    current_price_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    day_high: Mapped[float] = mapped_column(Float, nullable=True)
    day_low: Mapped[float] = mapped_column(Float, nullable=True)
    since_signal_high: Mapped[float] = mapped_column(Float, nullable=True)
    since_signal_low: Mapped[float] = mapped_column(Float, nullable=True)
    post_window_high: Mapped[float] = mapped_column(Float, nullable=True)  # extremes after the
    post_window_low: Mapped[float] = mapped_column(Float, nullable=True)   # 7:00 broker window opens
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|closed|invalidated
    signal_type: Mapped[str] = mapped_column(String(8), default="buy")  # buy|watch
    lifecycle: Mapped[str] = mapped_column(String(16), default="", index=True)  # v2 canonical status
    profile: Mapped[str] = mapped_column(String(24), default="primary", index=True)
    price_tier: Mapped[str] = mapped_column(String(12), default="")
    cohort: Mapped[str] = mapped_column(String(12), default="live_paper")
    executable: Mapped[bool] = mapped_column(Boolean, default=False)
    data_quality: Mapped[str] = mapped_column(String(12), default="complete")
    versions: Mapped[dict] = mapped_column(JSON, default=dict)
    sig_bid: Mapped[float] = mapped_column(Float, nullable=True)
    sig_ask: Mapped[float] = mapped_column(Float, nullable=True)
    sig_bid_size: Mapped[float] = mapped_column(Float, nullable=True)
    sig_ask_size: Mapped[float] = mapped_column(Float, nullable=True)
    sig_spread_pct: Mapped[float] = mapped_column(Float, nullable=True)
    proposed_entry: Mapped[float] = mapped_column(Float, nullable=True)
    sim_fill_price: Mapped[float] = mapped_column(Float, nullable=True)
    no_fill_reason: Mapped[str] = mapped_column(String(200), default="")
    catalyst_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    sec_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    first_pass_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    first_actionable_quote_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    first_actionable_ask: Mapped[float] = mapped_column(Float, nullable=True)
    mfe_pct: Mapped[float] = mapped_column(Float, nullable=True)
    mfe_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    mae_pct: Mapped[float] = mapped_column(Float, nullable=True)
    mae_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_v2: Mapped[str] = mapped_column(String(12), default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint("symbol", "strategy_version", "session_date", "catalyst_fingerprint",
                         name="uq_signal_idempotency"),
    )


class SignalEvent(Base):
    """Append-only audit trail. Corrections are new events, never edits."""
    __tablename__ = "signal_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("buy_signals.id"), index=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    event_type: Mapped[str] = mapped_column(String(32))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class SignalPriceCheckpoint(Base):
    __tablename__ = "signal_price_checkpoints"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("buy_signals.id"), index=True)
    label: Mapped[str] = mapped_column(String(16))  # 5m|15m|30m|60m|close
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    price: Mapped[float] = mapped_column(Float, nullable=True)
    pct_from_signal: Mapped[float] = mapped_column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("signal_id", "label", name="uq_checkpoint"),)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(24), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    thresholds: Mapped[dict] = mapped_column(JSON, default=dict)


class AppSetting(Base):
    """Single-row JSON settings blob; changes apply prospectively only."""
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class ProviderRequest(Base):
    __tablename__ = "provider_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    provider: Mapped[str] = mapped_column(String(24), index=True)  # fmp|sec|openai
    endpoint: Mapped[str] = mapped_column(String(128), index=True)
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    data_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(String(256), default="")


class HealthEvent(Base):
    __tablename__ = "health_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(String(12), default="info")
    component: Mapped[str] = mapped_column(String(48), default="")
    message: Mapped[str] = mapped_column(Text, default="")


class AiUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    month: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    model: Mapped[str] = mapped_column(String(48), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class RejectedCandidate(Base):
    """Shadow-tracked false-negative log: passed preliminary discovery, failed a
    hard gate. Never converted retroactively into a signal."""
    __tablename__ = "rejected_candidates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    session_date: Mapped[str] = mapped_column(String(10), index=True)
    rejected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lifecycle: Mapped[str] = mapped_column(String(16), default="REJECTED")
    profile: Mapped[str] = mapped_column(String(24), default="primary", index=True)
    rejection_reason: Mapped[str] = mapped_column(Text, default="")
    failed_gates: Mapped[list] = mapped_column(JSON, default=list)
    price_at_reject: Mapped[float] = mapped_column(Float, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    versions: Mapped[dict] = mapped_column(JSON, default=dict)
    shadow_high: Mapped[float] = mapped_column(Float, nullable=True)
    shadow_low: Mapped[float] = mapped_column(Float, nullable=True)
    shadow_last: Mapped[float] = mapped_column(Float, nullable=True)
    shadow_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index("ix_rej_sym_date", "symbol", "session_date"),)


class PaperPosition(Base):
    """Primary frozen paper policy execution for an ACTIONABLE_BUY signal."""
    __tablename__ = "paper_positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("buy_signals.id"), index=True, unique=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    profile: Mapped[str] = mapped_column(String(24), default="primary", index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    strategy_version: Mapped[str] = mapped_column(String(16), default="")
    entry_fill: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float, nullable=True)
    target1: Mapped[float] = mapped_column(Float, nullable=True)
    target2: Mapped[float] = mapped_column(Float, nullable=True)
    size_usd: Mapped[float] = mapped_column(Float, default=1000.0)
    remaining_frac: Mapped[float] = mapped_column(Float, default=1.0)
    realized_r: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="open")
    exit_reason: Mapped[str] = mapped_column(String(64), default="")
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_fill: Mapped[float] = mapped_column(Float, nullable=True)
    events: Mapped[list] = mapped_column(JSON, default=list)


class ShadowExit(Base):
    """Alternative exit policies evaluated on the same live signals (shadow only)."""
    __tablename__ = "shadow_exits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("buy_signals.id"), index=True)
    policy: Mapped[str] = mapped_column(String(40), index=True)
    exit_price: Mapped[float] = mapped_column(Float, nullable=True)
    exited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    r_multiple: Mapped[float] = mapped_column(Float, nullable=True)
    pct: Mapped[float] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    __table_args__ = (UniqueConstraint("signal_id", "policy", name="uq_shadow"),)


class BtJob(Base):
    """Durable, resumable backtest jobs. Never blocks the live scanner."""
    __tablename__ = "bt_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(24), default="replay")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class BtSession(Base):
    __tablename__ = "bt_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("bt_jobs.id"), index=True)
    session_date: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(20), default="done")
    universe_n: Mapped[int] = mapped_column(Integer, default=0)
    candidates_n: Mapped[int] = mapped_column(Integer, default=0)
    signals_n: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("job_id", "session_date", name="uq_bt_sess"),)


class BtTrade(Base):
    """A simulated trade from replay, with a full audit trail including the
    post-entry bar path (frozen basis for the exit tournament)."""
    __tablename__ = "bt_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("bt_jobs.id"), index=True)
    session_date: Mapped[str] = mapped_column(String(10), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    signal_time: Mapped[str] = mapped_column(String(20), default="")
    lifecycle: Mapped[str] = mapped_column(String(16), default="")
    entry_model: Mapped[str] = mapped_column(String(12), default="baseline")
    entry_price: Mapped[float] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(48), default="")
    r_multiple: Mapped[float] = mapped_column(Float, nullable=True)
    pct: Mapped[float] = mapped_column(Float, nullable=True)
    outcome: Mapped[str] = mapped_column(String(12), default="")
    size_usd: Mapped[float] = mapped_column(Float, default=1000.0)
    fill_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    audit: Mapped[dict] = mapped_column(JSON, default=dict)


class BtCache(Base):
    """Cached provider payloads for the backtester (bars, news, eod, docs)."""
    __tablename__ = "bt_cache"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class PaperAccount(Base):
    """Isolated $10,000 paper ledger per model per season. Equity derives from
    the ledger, never browser memory."""
    __tablename__ = "paper_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[str] = mapped_column(String(32), index=True)
    season: Mapped[int] = mapped_column(Integer, default=1)
    starting_cash: Mapped[float] = mapped_column(Float, default=10000.0)
    cash: Mapped[float] = mapped_column(Float, default=10000.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    equity: Mapped[float] = mapped_column(Float, default=10000.0)
    max_equity: Mapped[float] = mapped_column(Float, default=10000.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)
    trades_closed: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("model_id", "season", name="uq_account"),)


class LockedOutcome(Base):
    """Immutable call-accuracy results (e.g. PREMARKET_SCALPER_OUTCOME_V1).
    Corrections append revisions; the original class never mutates."""
    __tablename__ = "locked_outcomes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("buy_signals.id"), index=True)
    policy: Mapped[str] = mapped_column(String(48), index=True)
    outcome_class: Mapped[str] = mapped_column(String(24))  # WIN_10_TOUCH|WIN_NOON_GREEN|LOSS_NOON_RED|FLAT|INCOMPLETE
    call_price: Mapped[float] = mapped_column(Float)
    reference_price: Mapped[float] = mapped_column(Float, nullable=True)
    reference_quality: Mapped[str] = mapped_column(String(16), default="LIVE")  # LIVE|ESTIMATED
    touch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revision_of: Mapped[int] = mapped_column(Integer, nullable=True)
    revision_reason: Mapped[str] = mapped_column(String(200), default="")
    __table_args__ = (UniqueConstraint("signal_id", "policy", "revision_of",
                                       name="uq_lock"),)


class EquitySnapshot(Base):
    """Periodic per-model equity marks for sparklines and audit."""
    __tablename__ = "equity_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[str] = mapped_column(String(32), index=True)
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    equity: Mapped[float] = mapped_column(Float)


class JournalEntry(Base):
    """Trade journal: notes joined to signals/symbols with review tags."""
    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    symbol: Mapped[str] = mapped_column(String(16), default="", index=True)
    signal_uid: Mapped[str] = mapped_column(String(40), default="", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    rules_followed: Mapped[bool] = mapped_column(Boolean, default=True)
    review: Mapped[str] = mapped_column(Text, default="")


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(48), unique=True)
    symbols: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[dict] = mapped_column(JSON, default=dict)   # symbol -> note
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertRule(Base):
    """In-app price alerts checked by the tracking cycle; no SMS/email needed."""
    __tablename__ = "alert_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    condition: Mapped[str] = mapped_column(String(8), default="above")  # above|below
    price: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(String(200), default="")
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    fired_price: Mapped[float] = mapped_column(Float, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class MorningBrief(Base):
    """Auto-generated 9:25 AM premarket debrief + nightly summaries."""
    __tablename__ = "morning_briefs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_date: Mapped[str] = mapped_column(String(10), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="morning")  # morning|nightly
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("session_date", "kind", name="uq_brief"),)


class ReversionSignal(Base):
    """EXTREME_BB_RSI signals. Immutable after confirmation: entry, stop and
    targets are never rewritten. Management actions append to `events`."""
    __tablename__ = "reversion_signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_uid: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    strategy_id: Mapped[str] = mapped_column(String(48), default="extreme_reversion",
                                             index=True)
    strategy_version: Mapped[str] = mapped_column(String(16), default="1.0.0", index=True)
    variant: Mapped[str] = mapped_column(String(32), default="adaptive", index=True)
    dataset_run: Mapped[int] = mapped_column(Integer, default=1, index=True)
    cohort: Mapped[str] = mapped_column(String(16), default="paper", index=True)

    symbol: Mapped[str] = mapped_column(String(16), index=True)
    asset_class: Mapped[str] = mapped_column(String(16), default="stocks")
    timeframe: Mapped[str] = mapped_column(String(8), default="5min", index=True)
    direction: Mapped[str] = mapped_column(String(8), default="long")

    setup_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    bar_time: Mapped[int] = mapped_column(Integer, nullable=True)

    signal_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    score_band: Mapped[str] = mapped_column(String(32), default="")
    score_parts: Mapped[dict] = mapped_column(JSON, default=dict)

    entry_price: Mapped[float] = mapped_column(Float)
    entry_zone_low: Mapped[float] = mapped_column(Float, nullable=True)
    entry_zone_high: Mapped[float] = mapped_column(Float, nullable=True)
    no_chase_price: Mapped[float] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float] = mapped_column(Float)
    stop_basis: Mapped[str] = mapped_column(String(96), default="")
    target_1: Mapped[float] = mapped_column(Float, nullable=True)
    target_2: Mapped[float] = mapped_column(Float, nullable=True)
    target_3: Mapped[float] = mapped_column(Float, nullable=True)
    targets_json: Mapped[list] = mapped_column(JSON, default=list)

    parameters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    indicator_snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)
    market_regime: Mapped[str] = mapped_column(String(24), default="", index=True)
    adx: Mapped[float] = mapped_column(Float, nullable=True)
    atr: Mapped[float] = mapped_column(Float, nullable=True)
    rsi: Mapped[float] = mapped_column(Float, nullable=True)
    rsi_extreme: Mapped[float] = mapped_column(Float, nullable=True)
    bb_basis: Mapped[float] = mapped_column(Float, nullable=True)
    bb_upper: Mapped[float] = mapped_column(Float, nullable=True)
    bb_lower: Mapped[float] = mapped_column(Float, nullable=True)
    bb_width: Mapped[float] = mapped_column(Float, nullable=True)
    rvol: Mapped[float] = mapped_column(Float, nullable=True)
    vwap_distance_atr: Mapped[float] = mapped_column(Float, nullable=True)
    htf_trend: Mapped[str] = mapped_column(String(16), default="unknown")
    divergence: Mapped[str] = mapped_column(String(16), default="")
    session_bucket: Mapped[str] = mapped_column(String(24), default="", index=True)

    trade_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    roadmap: Mapped[dict] = mapped_column(JSON, default=dict)
    explain_lines: Mapped[list] = mapped_column(JSON, default=list)

    status: Mapped[str] = mapped_column(String(20), default="CONFIRMED", index=True)
    events: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    exit_price: Mapped[float] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(32), default="")
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    gross_return_pct: Mapped[float] = mapped_column(Float, nullable=True)
    net_return_pct: Mapped[float] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[float] = mapped_column(Float, nullable=True)
    mfe_r: Mapped[float] = mapped_column(Float, nullable=True)
    mae_r: Mapped[float] = mapped_column(Float, nullable=True)
    win_loss: Mapped[str] = mapped_column(String(12), default="", index=True)
    data_source: Mapped[str] = mapped_column(String(48), default="fmp")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("strategy_id", "variant", "symbol", "timeframe",
                         "direction", "bar_time", name="uq_reversion_signal"),
        Index("ix_rev_lookup", "strategy_id", "variant", "status"),
    )


class StrategyHeartbeat(Base):
    """One row per strategy worker. A stale heartbeat renders as OFFLINE rather
    than leaving old numbers looking live."""
    __tablename__ = "strategy_heartbeats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(96), default="")
    status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                        default=utcnow)
    last_scan_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_signal_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(256), default="")
    skip_reason: Mapped[str] = mapped_column(String(256), default="")
    symbols_scanned: Mapped[int] = mapped_column(Integer, default=0)
    symbols_with_data: Mapped[int] = mapped_column(Integer, default=0)
    signals_today: Mapped[int] = mapped_column(Integer, default=0)
    errors_today: Mapped[int] = mapped_column(Integer, default=0)
    day: Mapped[str] = mapped_column(String(12), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class DatasetRun(Base):
    """Performance datasets. Resetting archives the old run rather than deleting
    it, so a stats reset never destroys research."""
    __tablename__ = "dataset_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(48), index=True)
    run_number: Mapped[int] = mapped_column(Integer, default=1)
    scope: Mapped[str] = mapped_column(String(24), default="paper")
    label: Mapped[str] = mapped_column(String(96), default="")
    reason: Mapped[str] = mapped_column(String(256), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    stats_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("strategy_id", "run_number", "scope",
                                       name="uq_dataset_run"),)


class StrategyChangeLog(Base):
    """Append-only record of parameter/logic changes and what happened to the
    dataset as a result."""
    __tablename__ = "strategy_change_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(48), index=True)
    version: Mapped[str] = mapped_column(String(16), default="")
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    changes: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    old_parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    new_parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    dataset_action: Mapped[str] = mapped_column(String(32), default="continue")
    actor: Mapped[str] = mapped_column(String(48), default="system")


# ── Quant Lab ────────────────────────────────────────────────────────────────

class LabStrategy(Base):
    """One independently researched strategy and its lifecycle stage. The
    hypothesis is stored so a strategy can never be judged without the claim
    it was built on sitting next to its results."""
    __tablename__ = "lab_strategies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(96))
    family: Mapped[str] = mapped_column(String(32), index=True)
    category: Mapped[str] = mapped_column(String(48), default="")
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    markets: Mapped[list] = mapped_column(JSON, default=list)
    timeframes: Mapped[list] = mapped_column(JSON, default=list)
    hold: Mapped[str] = mapped_column(String(24), default="intraday")
    stop_method: Mapped[str] = mapped_column(String(48), default="atr")
    stage: Mapped[str] = mapped_column(String(24), default="RESEARCH", index=True)
    stage_reason: Mapped[str] = mapped_column(Text, default="")
    optimization_count: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    best_market: Mapped[str] = mapped_column(String(16), default="")
    best_timeframe: Mapped[str] = mapped_column(String(8), default="")
    best_regime: Mapped[str] = mapped_column(String(24), default="")
    worst_regime: Mapped[str] = mapped_column(String(24), default="")
    composite_score: Mapped[float] = mapped_column(Float, default=0.0)
    version: Mapped[str] = mapped_column(String(16), default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)


class LabRun(Base):
    """A backtest / walk-forward / Monte Carlo run for one strategy on one
    market and timeframe. Results are immutable once written; a new run is a
    new row, so optimisation history is visible."""
    __tablename__ = "lab_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(48), index=True)
    market: Mapped[str] = mapped_column(String(16), index=True)      # stocks|crypto|etf|options|index
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="backtest")  # backtest|walkforward|montecarlo|robustness
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    split: Mapped[str] = mapped_column(String(16), default="")         # train|validation|oos|all
    period_start: Mapped[str] = mapped_column(String(10), default="")
    period_end: Mapped[str] = mapped_column(String(10), default="")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    by_regime: Mapped[dict] = mapped_column(JSON, default=dict)
    by_session: Mapped[dict] = mapped_column(JSON, default=dict)
    by_symbol: Mapped[dict] = mapped_column(JSON, default=dict)
    equity_curve: Mapped[list] = mapped_column(JSON, default=list)
    monthly: Mapped[dict] = mapped_column(JSON, default=dict)
    costs: Mapped[dict] = mapped_column(JSON, default=dict)
    data_coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_labrun_lookup", "strategy_id", "market", "timeframe", "kind", "split"),)


class LabTrade(Base):
    """Permanent record of every lab signal, backtest or live. Never rewritten
    after the outcome is known; corrections are new rows."""
    __tablename__ = "lab_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(48), index=True)
    cohort: Mapped[str] = mapped_column(String(16), default="backtest", index=True)  # backtest|paper|live
    split: Mapped[str] = mapped_column(String(16), default="")
    market: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    direction: Mapped[str] = mapped_column(String(8), default="long")
    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float] = mapped_column(Float)
    target_1: Mapped[float] = mapped_column(Float, nullable=True)
    target_2: Mapped[float] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(32), default="")
    mfe_r: Mapped[float] = mapped_column(Float, nullable=True)
    mae_r: Mapped[float] = mapped_column(Float, nullable=True)
    return_pct: Mapped[float] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[float] = mapped_column(Float, nullable=True)
    pnl_usd: Mapped[float] = mapped_column(Float, nullable=True)
    result: Mapped[str] = mapped_column(String(12), default="", index=True)
    regime: Mapped[str] = mapped_column(String(24), default="", index=True)
    session_bucket: Mapped[str] = mapped_column(String(24), default="", index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    invalidation: Mapped[str] = mapped_column(Text, default="")
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_labtrade_lookup", "strategy_id", "cohort", "market", "timeframe"),)
