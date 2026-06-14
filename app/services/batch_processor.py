import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.episode import Episode
from app.services.gemini_service import GeminiService


class BatchProcessor:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.gemini = GeminiService()

    async def process(
        self,
        batch_number: UUID,
        person_name: str,
        name_variations: list[str],
        youtube_api_key: str,
        listen_notes_api_key: str,
    ) -> dict:
        # Fetch episodes and immediately extract to plain dicts.
        # After this, self.db is no longer used — each update gets
        # its own short-lived session so we never hold a connection
        # open during long-running Gemini calls.
        result = await self.db.execute(
            select(Episode).where(Episode.batchNumber == batch_number)
        )
        episodes = result.scalars().all()

        if not episodes:
            return {
                "batch_number": str(batch_number),
                "person_name": person_name,
                "name_variations": name_variations,
                "total_episodes": 0,
                "processed": 0,
                "skipped": 0,
                "errors": 0,
                "results": [],
            }

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

        results = []
        processed = skipped = errors = 0

        for record in records:
            ep_result = await self._process_record(record, person_name, name_variations)
            results.append(ep_result)
            match ep_result["status"]:
                case "processed":
                    processed += 1
                case "skipped":
                    skipped += 1
                case "error":
                    errors += 1

        return {
            "batch_number": str(batch_number),
            "person_name": person_name,
            "name_variations": name_variations,
            "total_episodes": len(records),
            "processed": processed,
            "skipped": skipped,
            "errors": errors,
            "results": results,
        }

    async def _process_record(
        self,
        record: dict,
        person_name: str,
        name_variations: list[str],
    ) -> dict:
        episode_id = record["id_str"]
        title = record["title"]
        source = record["source"]

        if source == "YOUTUBE":
            url = record["video_link"]
            if not url:
                return self._skip(episode_id, title, source, "No videoLink available")
            try:
                analysis = await self.gemini.analyze_youtube(url, person_name, name_variations)
            except Exception as exc:
                return self._error(episode_id, title, source, exc)
        else:
            url = record["audio_link"]
            if not url:
                return self._skip(episode_id, title, source, f"No audioLink for source '{source}'")
            try:
                analysis = await self.gemini.analyze_audio_url(url, person_name, name_variations)
            except Exception as exc:
                return self._error(episode_id, title, source, exc)

        await self._update_db(record, analysis)

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

    async def _update_db(self, record: dict, analysis: dict) -> None:
        """Use a brand-new session for each update — never holds a connection
        open during Gemini calls."""
        now = datetime.now(timezone.utc)
        new_meta = {
            **record["meta"],
            "role": analysis.get("role"),
            "isPodcast": analysis.get("is_podcast"),
            "processedAt": now.isoformat(),
        }

        async with AsyncSessionLocal() as session:
            await session.execute(
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
            await session.commit()

    @staticmethod
    def _skip(episode_id: str, title: str | None, source: str | None, reason: str) -> dict:
        return {
            "episode_id": episode_id,
            "title": title,
            "source": source,
            "is_podcast": None,
            "role": None,
            "confidence_score": None,
            "reason": reason,
            "status": "skipped",
        }

    @staticmethod
    def _error(episode_id: str, title: str | None, source: str | None, exc: Exception) -> dict:
        from tenacity import RetryError
        cause = exc.__cause__ if isinstance(exc, RetryError) else exc
        return {
            "episode_id": episode_id,
            "title": title,
            "source": source,
            "is_podcast": None,
            "role": None,
            "confidence_score": None,
            "reason": str(cause),
            "status": "error",
        }
