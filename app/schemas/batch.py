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


class EpisodeResult(BaseModel):
    episode_id: str
    title: str | None
    source: str | None
    is_podcast: bool | None
    role: str | None = Field(None, description="guest | host | unknown | null")
    confidence_score: int | None = Field(None, ge=0, le=100)
    reason: str | None
    status: str = Field(..., description="processed | skipped | error")
    error: str | None = None


class BatchProcessResponse(BaseModel):
    batch_number: str
    person_name: str
    name_variations: list[str]
    total_episodes: int
    processed: int
    skipped: int
    errors: int
    results: list[EpisodeResult]
