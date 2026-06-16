import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup: mark any jobs that were "running" as interrupted
    # (they were killed by a previous server restart)
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text("""
                    UPDATE batch_jobs
                    SET status = 'interrupted',
                        error_message = 'Server restarted while job was running. Re-submit to resume.'
                    WHERE status = 'running'
                    RETURNING id, batch_number
                """)
            )
            rows = result.fetchall()
            await db.commit()
            if rows:
                for row in rows:
                    logger.warning(
                        "Marked stale job %s (batch %s) as interrupted on startup",
                        row[0], row[1],
                    )
    except Exception:
        logger.exception("Failed to clean up stale jobs on startup")

    yield
    await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"message": f"Welcome to {settings.APP_NAME}", "docs": "/docs"}
