from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class BatchProcessRequest(BaseModel):
    batch_number: UUID = Field(..., description="UUID of the batch to process")
    person_name: str = Field(..., description="Primary name of the person to identify")
    name_variations: str = Field(
        default="",
        description="Comma-separated name aliases e.g. 'Marc S, M. Salinas, Marco'",
    )
    youtube_api_key: str = Field(..., description="YouTube Data API v3 key")
    listen_notes_api_key: str = Field(..., description="Listen Notes API key")

    @field_validator("person_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("person_name cannot be empty")
        return v.strip()

    def parsed_name_variations(self) -> list[str]:
        if not self.name_variations:
            return []
        return [v.strip() for v in self.name_variations.split(",") if v.strip()]


class JobStartedResponse(BaseModel):
    job_id: UUID
    status: str = "queued"
    batch_number: UUID
    total_episodes: int
    message: str


class EpisodeResult(BaseModel):
    episode_id: str
    title: str | None
    source: str | None
    is_podcast: bool | None
    role: str | None = Field(None, description="guest | host | unknown | null")
    confidence_score: int | None = Field(None, ge=0, le=100)
    reason: str | None
    status: str = Field(..., description="processed | skipped | error")


class JobStatusResponse(BaseModel):
    job_id: UUID
    batch_number: UUID
    person_name: str
    status: str = Field(..., description="queued | running | completed | failed")
    total_episodes: int
    processed: int
    skipped: int
    errors: int
    progress_pct: float
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime | None
    error_message: str | None = None
    results: list[EpisodeResult] | None = None


# Kept for backward compatibility with the sync endpoint
class BatchProcessResponse(BaseModel):
    batch_number: str
    person_name: str
    name_variations: list[str]
    total_episodes: int
    processed: int
    skipped: int
    errors: int
    results: list[EpisodeResult]
