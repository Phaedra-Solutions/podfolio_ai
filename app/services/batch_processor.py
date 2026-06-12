from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

        results = []
        processed = skipped = errors = 0

        for episode in episodes:
            ep_result = await self._process_episode(
                episode, person_name, name_variations
            )
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
            "total_episodes": len(episodes),
            "processed": processed,
            "skipped": skipped,
            "errors": errors,
            "results": results,
        }

    async def _process_episode(
        self,
        episode: Episode,
        person_name: str,
        name_variations: list[str],
    ) -> dict:
        source = (episode.source or "YOUTUBE").upper()

        try:
            if source == "YOUTUBE":
                url = episode.videoLink
                if not url:
                    return self._skip(episode, "No videoLink available")
                analysis = await self.gemini.analyze_youtube(url, person_name, name_variations)

            else:
                # LISTEN_NOTES or any other source — use audioLink
                url = episode.audioLink
                if not url:
                    return self._skip(episode, f"No audioLink available for source '{source}'")
                analysis = await self.gemini.analyze_audio_url(url, person_name, name_variations)

        except Exception as exc:
            await self.db.rollback()
            return {
                "episode_id": str(episode.id),
                "title": episode.title,
                "source": source,
                "is_podcast": None,
                "role": None,
                "confidence_score": None,
                "reason": str(exc),
                "status": "error",
            }

        await self._update_episode(episode, analysis)

        return {
            "episode_id": str(episode.id),
            "title": episode.title,
            "source": source,
            "is_podcast": analysis["is_podcast"],
            "role": analysis.get("role"),
            "confidence_score": analysis.get("confidence_score"),
            "reason": analysis.get("reason"),
            "status": "processed" if analysis["is_podcast"] else "skipped",
        }

    async def _update_episode(self, episode: Episode, analysis: dict) -> None:
        now = datetime.now(timezone.utc)
        episode.isAIVerified = analysis.get("is_podcast", False)
        episode.aiVerifiedReason = analysis.get("reason", "")
        episode.confidenceScore = analysis.get("confidence_score")
        episode.meta = {
            **(episode.meta or {}),
            "role": analysis.get("role"),
            "isPodcast": analysis.get("is_podcast"),
            "processedAt": now.isoformat(),
        }
        episode.updated_at = now
        await self.db.commit()
        await self.db.refresh(episode)

    @staticmethod
    def _skip(episode: Episode, reason: str) -> dict:
        return {
            "episode_id": str(episode.id),
            "title": episode.title,
            "source": episode.source,
            "is_podcast": None,
            "role": None,
            "confidence_score": None,
            "reason": reason,
            "status": "skipped",
        }
