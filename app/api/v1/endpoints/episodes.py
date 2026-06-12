from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.episode import Episode
from app.schemas.episode import EpisodeListResponse, EpisodeSchema

router = APIRouter()


@router.get("", response_model=EpisodeListResponse)
async def list_episodes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    profile_id: UUID | None = Query(None, description="Filter by profile ID"),
    source: str | None = Query(None, description="Filter by source (e.g. YOUTUBE)"),
    db: AsyncSession = Depends(get_db),
):
    query = select(Episode)
    count_query = select(func.count()).select_from(Episode)

    if profile_id:
        query = query.where(Episode.profileId == profile_id)
        count_query = count_query.where(Episode.profileId == profile_id)

    if source:
        query = query.where(Episode.source == source)
        count_query = count_query.where(Episode.source == source)

    query = query.order_by(Episode.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    total_result = await db.execute(count_query)
    total = total_result.scalar()

    result = await db.execute(query)
    episodes = result.scalars().all()

    return EpisodeListResponse(
        data=[EpisodeSchema.model_validate(e) for e in episodes],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{episode_id}", response_model=EpisodeSchema)
async def get_episode(
    episode_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Episode).where(Episode.id == episode_id))
    episode = result.scalar_one_or_none()

    if not episode:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Episode not found")

    return EpisodeSchema.model_validate(episode)
