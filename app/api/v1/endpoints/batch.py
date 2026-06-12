from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.batch import BatchProcessRequest, BatchProcessResponse
from app.services.batch_processor import BatchProcessor

router = APIRouter()


@router.post("/process-batch", response_model=BatchProcessResponse)
async def process_batch(
    payload: BatchProcessRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch all episodes matching the given batch_number UUID, transcribe and
    analyze each one with Gemini, then update the following episode columns:

    - isAIVerified   → true if content is a podcast, false otherwise
    - aiVerifiedReason → Gemini's explanation
    - confidenceScore  → 0–100
    - metadata         → adds role, isPodcast, processedAt
    - updated_at       → timestamp of this run

    Episodes from source=YOUTUBE are sent to Gemini via direct YouTube URL.
    All other sources (LISTEN_NOTES, etc.) are processed via the audioLink.
    """
    if not payload.youtube_api_key.strip():
        raise HTTPException(status_code=422, detail="youtube_api_key is required")
    if not payload.listen_notes_api_key.strip():
        raise HTTPException(status_code=422, detail="listen_notes_api_key is required")

    processor = BatchProcessor(db=db)
    result = await processor.process(
        batch_number=payload.batch_number,
        person_name=payload.person_name,
        name_variations=payload.parsed_name_variations(),
        youtube_api_key=payload.youtube_api_key,
        listen_notes_api_key=payload.listen_notes_api_key,
    )
    return BatchProcessResponse(**result)
