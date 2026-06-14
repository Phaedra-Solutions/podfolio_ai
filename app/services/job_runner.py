"""
Background job runner for batch episode processing.

Each job runs as an asyncio Task so the HTTP request returns immediately.
Progress is persisted to the batch_jobs table after every episode so the
status endpoint always reflects live progress.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text

from app.db.session import AsyncSessionLocal
from app.models.batch_job import BatchJob
from app.models.episode import Episode
from app.services.gemini_service import GeminiService

logger = logging.getLogger(__name__)


async def start_job(job_id: UUID) -> None:
    """Entry point called via asyncio.create_task()."""
    try:
        await _run(job_id)
    except Exception as exc:
        logger.exception("Unhandled error in batch job %s", job_id)
        await _mark_failed(job_id, str(exc))


async def _run(job_id: UUID) -> None:
    gemini = GeminiService()

    # ── 1. Load job record ──────────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(BatchJob).where(BatchJob.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            logger.error("Job %s not found", job_id)
            return

        batch_number = job.batch_number
        person_name = job.person_name
        name_variations = [v.strip() for v in (job.name_variations or "").split(",") if v.strip()]
        youtube_api_key = job.youtube_api_key
        listen_notes_api_key = job.listen_notes_api_key

    # ── 2. Fetch episodes ───────────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        ep_result = await db.execute(
            select(Episode).where(Episode.batchNumber == batch_number)
        )
        episodes = ep_result.scalars().all()
        records = [
            {
                "id": ep.id,
                "id_str": str(ep.id),
                "title": ep.title,
                "source": (ep.source or "YOUTUBE").upper(),
                "video_link": ep.videoLink,
                "audio_link": ep.audioLink,
                "meta": dict(ep.meta or {}),
            }
            for ep in episodes
        ]

    total = len(records)

    # ── 3. Mark running ─────────────────────────────────────────────────────
    await _update_job(job_id, status="running", total_episodes=total, started_at=datetime.now(timezone.utc))

    # ── 4. Process each episode ─────────────────────────────────────────────
    results: list[dict] = []
    processed = skipped = errors = 0

    for record in records:
        ep_result = await _process_record(gemini, record, person_name, name_variations)
        results.append(ep_result)

        match ep_result["status"]:
            case "processed":
                processed += 1
            case "skipped":
                skipped += 1
            case _:
                errors += 1

        # Persist progress after every episode
        await _update_job(
            job_id,
            processed=processed,
            skipped=skipped,
            errors=errors,
            results=results,
        )

    # ── 5. Mark completed ───────────────────────────────────────────────────
    await _update_job(
        job_id,
        status="completed",
        processed=processed,
        skipped=skipped,
        errors=errors,
        results=results,
        completed_at=datetime.now(timezone.utc),
    )
    logger.info("Job %s completed — %d processed, %d skipped, %d errors", job_id, processed, skipped, errors)


async def _process_record(
    gemini: GeminiService,
    record: dict,
    person_name: str,
    name_variations: list[str],
) -> dict:
    episode_id = record["id_str"]
    title = record["title"]
    source = record["source"]

    try:
        if source == "YOUTUBE":
            url = record["video_link"]
            if not url:
                return _skip(episode_id, title, source, "No videoLink available")
            analysis = await gemini.analyze_youtube(url, person_name, name_variations)
        else:
            url = record["audio_link"]
            if not url:
                return _skip(episode_id, title, source, f"No audioLink for source '{source}'")
            analysis = await gemini.analyze_audio_url(url, person_name, name_variations)
    except Exception as exc:
        from tenacity import RetryError
        cause = exc.__cause__ if isinstance(exc, RetryError) else exc
        return {
            "episode_id": episode_id, "title": title, "source": source,
            "is_podcast": None, "role": None, "confidence_score": None,
            "reason": str(cause), "status": "error",
        }

    await _update_episode(record, analysis)

    return {
        "episode_id": episode_id,
        "title": title,
        "source": source,
        "is_podcast": analysis.get("is_podcast"),
        "role": analysis.get("role"),
        "confidence_score": analysis.get("confidence_score"),
        "reason": analysis.get("reason"),
        "status": "processed" if analysis.get("is_podcast") else "skipped",
    }


async def _update_episode(record: dict, analysis: dict) -> None:
    now = datetime.now(timezone.utc)
    new_meta = {
        **record["meta"],
        "role": analysis.get("role"),
        "isPodcast": analysis.get("is_podcast"),
        "processedAt": now.isoformat(),
    }
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                UPDATE episodes SET
                    "isAIVerified"     = :is_verified,
                    "aiVerifiedReason" = :reason,
                    "confidenceScore"  = :score,
                    metadata           = :meta,
                    updated_at         = :updated_at
                WHERE id = :id
            """),
            {
                "is_verified": analysis.get("is_podcast", False),
                "reason": analysis.get("reason", ""),
                "score": analysis.get("confidence_score"),
                "meta": json.dumps(new_meta),
                "updated_at": now,
                "id": record["id"],
            },
        )
        await db.commit()


async def _update_job(job_id: UUID, **fields) -> None:
    """Partial update of the batch_jobs row."""
    if not fields:
        return
    set_clauses = ", ".join(f'"{k}" = :{k}' for k in fields)
    params = {"id": job_id}
    for k, v in fields.items():
        params[k] = json.dumps(v) if k == "results" else v

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(f'UPDATE batch_jobs SET {set_clauses} WHERE id = :id'),  # noqa: S608
            params,
        )
        await db.commit()


async def _mark_failed(job_id: UUID, message: str) -> None:
    await _update_job(
        job_id,
        status="failed",
        error_message=message,
        completed_at=datetime.now(timezone.utc),
    )


def _skip(episode_id, title, source, reason):
    return {
        "episode_id": episode_id, "title": title, "source": source,
        "is_podcast": None, "role": None, "confidence_score": None,
        "reason": reason, "status": "skipped",
    }
