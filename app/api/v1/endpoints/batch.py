import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.batch_job import BatchJob
from app.models.episode import Episode
from app.schemas.batch import (
    BatchProcessRequest,
    JobStartedResponse,
    JobStatusResponse,
)
from app.services import job_runner

router = APIRouter()


@router.post("/process-batch", response_model=JobStartedResponse, status_code=202)
async def start_batch_process(
    payload: BatchProcessRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Start a background job that processes all episodes in the batch.

    person_name and name_variations are resolved automatically from the
    profile linked to the episodes — no need to pass them manually.

    Returns immediately with a job_id. Use GET /batch-jobs/{job_id} to
    track progress.
    """
    # ── 1. Count episodes ───────────────────────────────────────────────────
    count_result = await db.execute(
        select(func.count()).select_from(Episode).where(
            Episode.batchNumber == payload.batch_number
        )
    )
    total = count_result.scalar() or 0

    if total == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No episodes found for batch_number {payload.batch_number}",
        )

    # ── 2. Resolve profile → person_name + nameVariations ──────────────────
    # Grab the profileId from the first episode in this batch
    ep_result = await db.execute(
        select(Episode.profileId).where(
            Episode.batchNumber == payload.batch_number
        ).limit(1)
    )
    profile_id = ep_result.scalar_one_or_none()

    person_name: str = ""
    name_variations: str = ""

    if profile_id:
        profile_result = await db.execute(
            text('SELECT name, "nameVariations" FROM profiles WHERE id = :pid'),
            {"pid": profile_id},
        )
        profile_row = profile_result.mappings().fetchone()
        if profile_row:
            person_name = profile_row["name"] or ""
            name_variations = profile_row["nameVariations"] or ""

    if not person_name:
        raise HTTPException(
            status_code=422,
            detail="Could not resolve person_name from the profile linked to this batch. "
                   "Ensure the episodes have a valid profileId with a name set.",
        )

    # ── 3. Create the job record ─────────────────────────────────────────────
    job = BatchJob(
        id=uuid.uuid4(),
        batch_number=payload.batch_number,
        person_name=person_name,
        name_variations=name_variations,
        youtube_api_key=payload.youtube_api_key,
        listen_notes_api_key=payload.listen_notes_api_key,
        status="queued",
        total_episodes=total,
        processed=0,
        skipped=0,
        errors=0,
        results=[],
        created_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.commit()

    # ── 4. Fire background task ──────────────────────────────────────────────
    asyncio.create_task(job_runner.start_job(job.id))

    return JobStartedResponse(
        job_id=job.id,
        status="queued",
        batch_number=payload.batch_number,
        total_episodes=total,
        message=(
            f"Job queued for '{person_name}'. "
            f"{total} episodes will be processed in the background. "
            f"Poll GET /api/v1/episodes/batch-jobs/{job.id} to track progress."
        ),
    )


@router.get("/batch-jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Get the current status and progress of a batch processing job.
    """
    result = await db.execute(select(BatchJob).where(BatchJob.id == job_id))
    job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    total = job.total_episodes or 0
    done = (job.processed or 0) + (job.skipped or 0) + (job.errors or 0)
    progress_pct = round((done / total) * 100, 1) if total > 0 else 0.0

    return JobStatusResponse(
        job_id=job.id,
        batch_number=job.batch_number,
        person_name=job.person_name,
        status=job.status,
        total_episodes=total,
        processed=job.processed or 0,
        skipped=job.skipped or 0,
        errors=job.errors or 0,
        progress_pct=progress_pct,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
        error_message=job.error_message,
        results=job.results,
    )
