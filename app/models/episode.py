import uuid

from sqlalchemy import Boolean, Column, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from app.db.base import Base


class Episode(Base):
    __tablename__ = "episodes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(TIMESTAMP(timezone=True))
    updated_at = Column(TIMESTAMP(timezone=True))
    profileId = Column(UUID(as_uuid=True), nullable=False)
    title = Column(Text)
    channel = Column(Text)
    date = Column(TIMESTAMP(timezone=True))
    confidenceScore = Column(Numeric)
    videoLink = Column(Text)
    thumbnail = Column(Text)
    audioLink = Column(Text)
    source = Column(Text, nullable=False, default="YOUTUBE")
    meta = Column("metadata", JSONB)
    isAIVerified = Column(Boolean, default=False)
    aiVerifiedReason = Column(Text)
    description = Column(Text)
    viewCount = Column(Text)
    likeCount = Column(Text)
    favoriteCount = Column(Text)
    commentCount = Column(Text)
    batchNumber = Column(UUID(as_uuid=True))
    # pending | processing | verified | rejected | error
    processingStatus = Column(Text, nullable=False, default="pending")
