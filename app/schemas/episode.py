from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class EpisodeSchema(BaseModel):
    id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None
    profileId: UUID
    title: str | None = None
    channel: str | None = None
    date: datetime | None = None
    confidenceScore: Decimal | None = None
    videoLink: str | None = None
    thumbnail: str | None = None
    audioLink: str | None = None
    source: str
    meta: dict[str, Any] | None = None
    isAIVerified: bool | None = None
    aiVerifiedReason: str | None = None
    description: str | None = None
    viewCount: str | None = None
    likeCount: str | None = None
    favoriteCount: str | None = None
    commentCount: str | None = None
    batchNumber: UUID | None = None

    model_config = {"from_attributes": True}


class EpisodeListResponse(BaseModel):
    data: list[EpisodeSchema]
    total: int
    page: int
    page_size: int
