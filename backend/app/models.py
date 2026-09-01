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
