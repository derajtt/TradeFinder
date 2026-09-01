"""Premarket Hunter API entrypoint."""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_config
from .db import Base, engine
from .providers.fmp import FmpProvider
from .providers.openai_client import OpenAiProvider
from .providers.sec import SecProvider
from .routes.api import router
from .scanner.funnel import ScanContext
from .scheduler import Scheduler
from .util.timeutil import next_scan_start, now_et, session_phase

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    missing = cfg.missing_required()
    if missing:
        # precise error, no values
        log.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(f"Startup aborted. Set: {', '.join(missing)} (values are never printed)")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # supplements Alembic for dev/sqlite
    fmp, sec, oai = FmpProvider(), SecProvider(), OpenAiProvider()
    ctx = ScanContext(fmp, sec, oai)
    sched = Scheduler(ctx, scan_enabled=cfg.scan_enabled)
    app.state.shared = {"ctx": ctx, "scheduler": sched}
    sched.start()
    log.info("Premarket Hunter up. phase=%s next_scan=%s",
             session_phase(), next_scan_start().isoformat())
    try:
        yield
    finally:
        await sched.stop()
        await fmp.close()
        await sec.close()
        await oai.close()


app = FastAPI(title="Premarket Hunter", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware,
                   allow_origins=get_config().cors_origin_list(),
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok", "phase": session_phase(), "et": now_et().isoformat()}
