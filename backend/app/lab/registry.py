"""Discovery of lab strategy modules.

Every file at app/lab/strategies/s<NN>_<slug>.py that exports ``META`` (a
StrategyMeta) and ``signal(ctx, cfg)`` is a lab strategy. Discovery is by
directory listing, not a hand-kept list, so a module is live the moment it is
saved; one that fails to import or breaks the contract is reported and skipped
rather than taking the others down with it (``strict=True`` raises instead, for
the harness and CI). ``load_all()`` is the single entry point the harness, the
API and the live worker share: it returns a ``StrategyIndex`` -- a list sorted
by id for ``for s in load_all()``, and a mapping keyed by strategy id whose
values unpack as ``meta, signal``, so ``{m.family for m, _ in load_all().values()}``
and ``load_all()["s07_rsi2_trend_filter"]`` both read naturally. Nothing is
silently lost: the index carries ``.skipped`` (module stem -> reason).
"""
from __future__ import annotations

import importlib
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .base import FAMILIES, SignalFn, StrategyMeta

log = logging.getLogger("lab.registry")

PACKAGE = "app.lab.strategies"
STRATEGIES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategies")
MARKETS = ("stocks", "etf", "crypto", "index")          # options is not on the data plan
TIMEFRAMES = ("5min", "15min", "30min", "1hour", "4hour", "1day")
HOLDS = ("scalp", "intraday", "swing")
_MODULE_RE = re.compile(r"^s\d{2}_[a-z0-9_]+$")


@dataclass
class LoadedStrategy:
    meta: StrategyMeta
    signal: SignalFn
    module: str

    @property
    def id(self) -> str:
        return self.meta.id

    def __iter__(self) -> Iterator[Any]:
        """Unpack as the ``(META, signal)`` pair the contract is written in:
        ``meta, fn = loaded``."""
        yield self.meta
        yield self.signal


class StrategyIndex(List[LoadedStrategy]):
    """The loaded strategies, sorted by id. Iterates like a list of
    ``LoadedStrategy`` (what the live worker and the harness use) and reads
    like a mapping keyed by strategy id: ``idx["s01_..."]``, ``idx.get(id)``,
    ``.keys()`` / ``.values()`` / ``.items()``. Each value unpacks as
    ``meta, signal``. ``skipped`` maps module stems that were left out to the
    plain-English reason, so a broken module is visible, never silent."""

    def __init__(self, items: Sequence[LoadedStrategy] = (),
                 skipped: Optional[Dict[str, str]] = None) -> None:
        super().__init__(items)
        self.skipped: Dict[str, str] = dict(skipped or {})

    def keys(self) -> List[str]:
        return [s.id for s in self]

    def values(self) -> List[LoadedStrategy]:
        return list(self)

    def items(self) -> List[Tuple[str, LoadedStrategy]]:
        return [(s.id, s) for s in self]

    def get(self, strategy_id: str, default: Any = None) -> Any:
        for s in self:
            if s.id == strategy_id:
                return s
        return default

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            s = self.get(key)
            if s is None:
                raise KeyError(key)
            return s
        return super().__getitem__(key)

    def __contains__(self, key: Any) -> bool:
        if isinstance(key, str):
            return self.get(key) is not None
        return super().__contains__(key)


def iter_module_names() -> List[str]:
    """Strategy module stems on disk, sorted, without importing anything."""
    try:
        names = os.listdir(STRATEGIES_DIR)
    except OSError:
        return []
    out = []
    for fn in names:
        stem, ext = os.path.splitext(fn)
        if ext == ".py" and _MODULE_RE.match(stem):
            out.append(stem)
    return sorted(out)


def contract_errors(meta: Any, fn: Any, stem: str) -> List[str]:
    """Plain-English contract violations for one module; empty means valid."""
    if not isinstance(meta, StrategyMeta):
        return [f"{stem}: META is missing or not a StrategyMeta"]
    errs = []
    if meta.id != stem:
        errs.append(f"{stem}: META.id {meta.id!r} does not equal the filename stem")
    if meta.family not in FAMILIES:
        errs.append(f"{stem}: family {meta.family!r} is not in FAMILIES")
    if not meta.markets or any(m not in MARKETS for m in meta.markets):
        errs.append(f"{stem}: markets {list(meta.markets)!r} must be a non-empty subset of {list(MARKETS)}")
    if not meta.timeframes or any(t not in TIMEFRAMES for t in meta.timeframes):
        errs.append(f"{stem}: timeframes {list(meta.timeframes)!r} must be a non-empty subset of {list(TIMEFRAMES)}")
    if meta.hold not in HOLDS:
        errs.append(f"{stem}: hold {meta.hold!r} must be one of {list(HOLDS)}")
    if not callable(fn):
        errs.append(f"{stem}: signal is missing or not callable")
    return errs


def load_report(strict: bool = False) -> Tuple[List[LoadedStrategy], Dict[str, str]]:
    """Import every strategy module. Returns (loaded, skipped) where skipped
    maps a module stem to the reason it was left out."""
    loaded: List[LoadedStrategy] = []
    skipped: Dict[str, str] = {}
    seen = set()
    for stem in iter_module_names():
        try:
            mod = importlib.import_module(f"{PACKAGE}.{stem}")
        except Exception as e:                        # a broken sibling must not sink the lab
            if strict:
                raise
            skipped[stem] = f"import failed: {type(e).__name__}: {e}"
            log.warning("lab strategy %s skipped: %s", stem, skipped[stem])
            continue
        meta, fn = getattr(mod, "META", None), getattr(mod, "signal", None)
        errs = contract_errors(meta, fn, stem)
        if errs:
            if strict:
                raise ValueError("; ".join(errs))
            skipped[stem] = "; ".join(errs)
            for e in errs:
                log.warning("lab strategy skipped: %s", e)
            continue
        if meta.id in seen:
            skipped[stem] = f"duplicate strategy id {meta.id!r}"
            continue
        seen.add(meta.id)
        loaded.append(LoadedStrategy(meta=meta, signal=fn, module=mod.__name__))
    return loaded, skipped


def load_all(strict: bool = False) -> StrategyIndex:
    """All valid lab strategies as a ``StrategyIndex`` (list sorted by id,
    mapping by strategy id, ``.skipped`` for the ones left out). ``strict``
    raises on the first broken module instead of skipping it (for the harness
    and CI)."""
    loaded, skipped = load_report(strict=strict)
    return StrategyIndex(loaded, skipped)


def by_id(strict: bool = False) -> Dict[str, LoadedStrategy]:
    return dict(load_all(strict=strict).items())


def load(strategy_id: str) -> Optional[LoadedStrategy]:
    return by_id().get(strategy_id)
