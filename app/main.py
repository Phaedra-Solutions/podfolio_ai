import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # On startup: auto-resume any jobs that were running/interrupted
    # when the server previously went down
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text("""
                    UPDATE batch_jobs
                    SET status = 'queued',
                        error_message = NULL
                    WHERE status IN ('running', 'interrupted')
                    RETURNING id, batch_number
                """)
            )
            rows = result.fetchall()
            await db.commit()

        if rows:
            from app.services import job_runner
            for row in rows:
                job_id, batch_number = row[0], row[1]
                logger.info(
                    "🔄 Auto-resuming interrupted job %s (batch %s)",
                    job_id, batch_number,
                )
                asyncio.create_task(job_runner.start_job(job_id, resume=True))
        else:
            logger.info("✅ No interrupted jobs to resume on startup")

    except Exception:
        logger.exception("Failed to auto-resume jobs on startup")

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
