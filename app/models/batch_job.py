import uuid

from sqlalchemy import Column, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from app.db.base import Base


class BatchJob(Base):
    __tablename__ = "batch_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_number = Column(UUID(as_uuid=True), nullable=False, index=True)
    person_name = Column(Text, nullable=False)
    name_variations = Column(Text, default="")
    youtube_api_key = Column(Text, nullable=False)
    listen_notes_api_key = Column(Text, nullable=False)

    # queued | running | completed | failed
    status = Column(Text, nullable=False, default="queued")

    total_episodes = Column(Integer, default=0)
    processed = Column(Integer, default=0)
    skipped = Column(Integer, default=0)
    errors = Column(Integer, default=0)

    results = Column(JSONB)       # list of per-episode result dicts
    error_message = Column(Text)  # set if the job itself crashes

    started_at = Column(TIMESTAMP(timezone=True))
    completed_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True))
