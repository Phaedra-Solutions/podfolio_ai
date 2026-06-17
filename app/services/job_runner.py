"""
Background job runner for batch episode processing.

Each job runs as an asyncio Task so the HTTP request returns immediately.
Progress is persisted to the batch_jobs table after every episode so the
status endpoint always reflects live progress.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from uuid import UUID

import httpx
from sqlalchemy import select, text

from app.db.session import AsyncSessionLocal
from app.models.batch_job import BatchJob
from app.models.episode import Episode
from app.services.gemini_service import GeminiService

logger = logging.getLogger(__name__)


async def start_job(job_id: UUID, resume: bool = False) -> None:
    """Entry point called via asyncio.create_task().
    
    resume=True: skip already verified/rejected episodes (used on auto-resume after restart)
    resume=False: process all episodes fresh (used on manual re-submit)
    """
    try:
        await _run(job_id, resume=resume)
    except Exception as exc:
        logger.exception("Unhandled error in batch job %s", job_id)
        await _mark_failed(job_id, str(exc))


async def _run(job_id: UUID, resume: bool = False) -> None:
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
        query = select(Episode).where(Episode.batchNumber == batch_number)

        if resume:
            # Skip already completed episodes — resume from checkpoint
            query = query.where(
                Episode.processingStatus.notin_(["verified", "rejected"])
            )
            logger.info("🔄 Resuming job %s — skipping already processed episodes", job_id)

        ep_result = await db.execute(query)
        episodes = ep_result.scalars().all()
        records = [
            {
                "id": ep.id,
                "id_str": str(ep.id),
                "title": ep.title or "",
                "channel": ep.channel or "",
                "description": ep.description or "",
                "source": (ep.source or "YOUTUBE").upper(),
                "video_link": ep.videoLink,
                "audio_link": ep.audioLink,
                "meta": dict(ep.meta or {}),
                "listen_notes_api_key": listen_notes_api_key,
            }
            for ep in episodes
        ]

    total = len(records)
    CONCURRENCY = 5  # parallel Gemini calls at once

    # ── 3. Mark running ─────────────────────────────────────────────────────
    await _update_job(job_id, status="running", total_episodes=total, started_at=datetime.now(timezone.utc))
    logger.info("🚀 Job %s started — person: %s | episodes: %d | concurrency: %d", job_id, person_name, total, CONCURRENCY)

    # ── 4. Process episodes in parallel ─────────────────────────────────────
    results: list[dict] = [None] * total
    processed = skipped = errors = done_count = 0
    sem = asyncio.Semaphore(CONCURRENCY)
    lock = asyncio.Lock()

    async def _handle(idx: int, record: dict) -> None:
        nonlocal processed, skipped, errors, done_count

        title_short = (record["title"] or "")[:60]
        async with sem:
            logger.info("[%d/%d] Processing: \"%s\" (%s)", idx + 1, total, title_short, record["source"])
            await _set_episode_status(record["id"], "processing")
            try:
                ep_result = await asyncio.wait_for(
                    _process_record(gemini, record, person_name, name_variations),
                    timeout=180,
                )
            except asyncio.TimeoutError:
                logger.warning("[%d/%d] ⏱️  Timeout — falling back to text: \"%s\"", idx + 1, total, title_short)
                analysis = await gemini.analyze_text(
                    title=record["title"],
                    channel=record["channel"],
                    description=record["description"],
                    person_name=person_name,
                    name_variations=name_variations,
                )
                await _update_episode(record, analysis)
                ep_result = {
                    "episode_id": record["id_str"],
                    "title": record["title"],
                    "source": record["source"],
                    "is_podcast": analysis.get("is_podcast"),
                    "role": analysis.get("role"),
                    "confidence_score": analysis.get("confidence_score"),
                    "reason": f"[timeout→text] {analysis.get('reason')}",
                    "status": "processed",
                }

        results[idx] = ep_result

        async with lock:
            match ep_result["status"]:
                case "processed":
                    processed += 1
                    logger.info(
                        "  ✅ [%d/%d] is_podcast=%s | role=%s | confidence=%s | \"%s\"",
                        idx + 1, total, ep_result.get("is_podcast"), ep_result.get("role"),
                        ep_result.get("confidence_score"), title_short,
                    )
                case "skipped":
                    skipped += 1
                    logger.info("  ⏭️  [%d/%d] Skipped: \"%s\"", idx + 1, total, title_short)
                case _:
                    errors += 1
                    logger.warning("  ❌ [%d/%d] Error on \"%s\": %s", idx + 1, total, title_short, ep_result.get("reason", ""))

            done_count += 1
            pct = round((done_count / total) * 100, 1) if total else 0
            logger.info(
                "  Progress: %d/%d (%.1f%%) — ✅ %d | ⏭️  %d | ❌ %d",
                done_count, total, pct, processed, skipped, errors,
            )
            completed_results = [r for r in results if r is not None]
            await _update_job(job_id, processed=processed, skipped=skipped, errors=errors, results=completed_results)

    await asyncio.gather(*[_handle(i, rec) for i, rec in enumerate(records)])

    # ── 5. Mark completed ───────────────────────────────────────────────────
    final_results = [r for r in results if r is not None]
    await _update_job(
        job_id,
        status="completed",
        processed=processed,
        skipped=skipped,
        errors=errors,
        results=final_results,
        completed_at=datetime.now(timezone.utc),
    )
    logger.info(
        "🎉 Job %s COMPLETED — %d processed | %d skipped | %d errors (total: %d)",
        job_id, processed, skipped, errors, total,
    )


_LN_PODCASTS_RE = re.compile(r"listennotes\.com/podcasts/[^/]+/[^/]+-([A-Za-z0-9_-]+)/?")
_LN_E_RE = re.compile(r"listennotes\.com/e/([A-Za-z0-9_-]+)/?")
_LN_API_BASE = "https://listen-api.listennotes.com/api/v2"


async def _resolve_audio_url(raw_url: str, ln_api_key: str) -> str:
    """
    If raw_url is a Listen Notes web-page URL:
      1. Extract the episode ID from the URL (supports both /podcasts/ and /e/ formats).
      2. Call the Listen Notes API to get the real audio URL.
      3. If the API is unreachable, fall back to yt-dlp.
    Otherwise return raw_url unchanged.
    """
    m = _LN_PODCASTS_RE.search(raw_url) or _LN_E_RE.search(raw_url)
    if not m:
        return raw_url  # Already a direct audio URL

    ep_id = m.group(1)
    api_url = f"{_LN_API_BASE}/episodes/{ep_id}"

    # ── Try Listen Notes API first ───────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(api_url, headers={"X-ListenAPI-Key": ln_api_key})
            resp.raise_for_status()
            data = resp.json()

        audio = data.get("audio") or data.get("audio_url")
        if audio:
            logger.info("Resolved Listen Notes episode %s via API → %s", ep_id, audio)
            return audio
        raise ValueError("Listen Notes API returned no audio field")

    except Exception as api_exc:
        logger.warning(
            "Listen Notes API failed for episode %s (%s) — falling back to yt-dlp",
            ep_id, api_exc,
        )

    # ── Fallback: yt-dlp ─────────────────────────────────────────────────────
    loop = asyncio.get_running_loop()

    def _extract():
        import yt_dlp  # noqa: PLC0415

        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(raw_url, download=False)
            if "url" in info:
                return info["url"]
            if "entries" in info:
                return info["entries"][0]["url"]
            raise ValueError("yt-dlp could not extract an audio URL")

    try:
        audio_url = await loop.run_in_executor(None, _extract)
        logger.info("Resolved Listen Notes episode %s via yt-dlp → %s", ep_id, audio_url)
        return audio_url
    except Exception as yt_exc:
        raise ValueError(
            f"Could not resolve audio for Listen Notes episode {ep_id}: "
            f"API unreachable and yt-dlp failed ({yt_exc})"
        ) from yt_exc


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
                await _set_episode_status(record["id"], "error")
                return _skip(episode_id, title, source, "No videoLink available")
            try:
                analysis = await gemini.analyze_youtube(url, person_name, name_variations)
            except Exception as yt_exc:
                from tenacity import RetryError
                cause = yt_exc.__cause__ if isinstance(yt_exc, RetryError) else yt_exc
                logger.warning(
                    "YouTube video unreachable for episode %s (%s) — falling back to text analysis",
                    episode_id, cause,
                )
                analysis = await gemini.analyze_text(
                    title=record["title"],
                    channel=record["channel"],
                    description=record["description"],
                    person_name=person_name,
                    name_variations=name_variations,
                )
                await _update_episode(record, analysis)
                return {
                    "episode_id": episode_id,
                    "title": title,
                    "source": source,
                    "is_podcast": analysis.get("is_podcast"),
                    "role": analysis.get("role"),
                    "confidence_score": analysis.get("confidence_score"),
                    "reason": f"[text-only] {analysis.get('reason')}",
                    "status": "processed",
                }
        else:
            raw_url = record["audio_link"]
            if not raw_url:
                await _set_episode_status(record["id"], "error")
                return _skip(episode_id, title, source, f"No audioLink for source '{source}'")
            try:
                url = await _resolve_audio_url(raw_url, record["listen_notes_api_key"])
            except ValueError:
                logger.warning(
                    "Audio unreachable for episode %s — falling back to text analysis",
                    episode_id,
                )
                analysis = await gemini.analyze_text(
                    title=record["title"],
                    channel=record["channel"],
                    description=record["description"],
                    person_name=person_name,
                    name_variations=name_variations,
                )
                await _update_episode(record, analysis)
                return {
                    "episode_id": episode_id,
                    "title": title,
                    "source": source,
                    "is_podcast": analysis.get("is_podcast"),
                    "role": analysis.get("role"),
                    "confidence_score": analysis.get("confidence_score"),
                    "reason": f"[text-only] {analysis.get('reason')}",
                    "status": "processed",
                }
            analysis = await gemini.analyze_audio_url(url, person_name, name_variations)
    except Exception as exc:
        from tenacity import RetryError
        cause = exc.__cause__ if isinstance(exc, RetryError) else exc
        await _set_episode_status(record["id"], "error")
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


async def _set_episode_status(episode_id, status: str) -> None:
    """Lightweight update — just flips processingStatus."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            text('UPDATE episodes SET "processingStatus" = :s WHERE id = :id'),
            {"s": status, "id": episode_id},
        )
        await db.commit()


async def _update_episode(record: dict, analysis: dict) -> None:
    now = datetime.now(timezone.utc)
    is_podcast = analysis.get("is_podcast", False)
    processing_status = "verified" if is_podcast else "rejected"

    new_meta = {
        **record["meta"],
        "role": analysis.get("role"),
        "isPodcast": is_podcast,
        "processedAt": now.isoformat(),
    }
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                UPDATE episodes SET
                    "isAIVerified"       = :is_verified,
                    "aiVerifiedReason"   = :reason,
                    "confidenceScore"    = :score,
                    metadata             = :meta,
                    "processingStatus"   = :processing_status,
                    updated_at           = :updated_at
                WHERE id = :id
            """),
            {
                "is_verified": is_podcast,
                "reason": analysis.get("reason", ""),
                "score": analysis.get("confidence_score"),
                "meta": json.dumps(new_meta),
                "processing_status": processing_status,
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
