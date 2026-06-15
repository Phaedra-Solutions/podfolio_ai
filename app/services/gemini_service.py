import asyncio
import json
import os
import re
import tempfile
from functools import partial

import httpx
from google import genai
from google.genai import types
from google.genai.errors import ClientError
from tenacity import RetryError, retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings

MODEL = "gemini-2.5-flash"
FALLBACK_MINUTES = 10  # analyse only the first N minutes when full video is too large


def _build_prompt(person_name: str, name_variations: list[str]) -> str:
    all_names = [person_name] + [v.strip() for v in name_variations if v.strip()]
    names_str = ", ".join(f'"{n}"' for n in all_names)

    return f"""Analyze this audio/video content and answer two questions:

1. Is this a PODCAST episode? A podcast is a recurring audio/video show that typically features:
   - A host who introduces and guides the conversation
   - One or more guests being interviewed or in discussion
   - A consistent show format with episode numbering or branding

2. If this IS a podcast, is the person known as {names_str} appearing as a GUEST or HOST?

Respond ONLY with a valid JSON object — no markdown, no extra text:
{{
  "is_podcast": true or false,
  "confidence_score": integer from 0 to 100,
  "role": "guest" or "host" or "unknown" or null,
  "reason": "one or two sentence explanation"
}}

Rules:
- Set "is_podcast" to false for: tutorials, vlogs, advertisements, music videos, solo presentations, product demos, or any non-podcast content
- Set "role" to null when "is_podcast" is false
- Set "role" to "unknown" when the person cannot be clearly identified in the content
- "confidence_score" reflects your overall confidence in the full assessment (0 = no confidence, 100 = certain)"""


def _is_token_limit_error(exc: ClientError) -> bool:
    msg = str(exc).lower()
    return "token" in msg and ("exceeds" in msg or "limit" in msg)


def _is_retryable(exc: Exception) -> bool:
    """Only retry on transient errors (rate limit / server error), not on 4xx."""
    if isinstance(exc, ClientError):
        # ClientError uses .code for the numeric HTTP status
        code = getattr(exc, "code", None)
        return code in (429, 500, 502, 503, 504)
    return True


def _extract_json(text: str) -> dict:
    text = text.strip()

    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?", "", text, flags=re.MULTILINE).strip()
    text = re.sub(r"```$", "", text, flags=re.MULTILINE).strip()

    # Find first JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "is_podcast": False,
            "confidence_score": 0,
            "role": None,
            "reason": f"Failed to parse Gemini response: {text[:300]}",
        }


class GeminiService:
    def __init__(self):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)

    async def analyze_youtube(
        self,
        video_url: str,
        person_name: str,
        name_variations: list[str],
    ) -> dict:
        """Analyse the first FALLBACK_MINUTES minutes of a YouTube video.
        Capping upfront avoids token-limit errors and keeps response times short —
        the opening segment of any podcast is sufficient for detection + role ID."""
        return await self._call_youtube(
            video_url, person_name, name_variations,
            end_offset=f"{FALLBACK_MINUTES * 60}s",
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable),
    )
    async def _call_youtube(
        self,
        video_url: str,
        person_name: str,
        name_variations: list[str],
        end_offset: str | None = None,
    ) -> dict:
        prompt = _build_prompt(person_name, name_variations)

        video_part = types.Part(
            file_data=types.FileData(file_uri=video_url, mime_type="video/*"),
            video_metadata=types.VideoMetadata(end_offset=end_offset) if end_offset else None,
        )

        response = await self.client.aio.models.generate_content(
            model=MODEL,
            contents=[video_part, types.Part.from_text(text=prompt)],
        )
        return _extract_json(response.text)

    async def analyze_text(
        self,
        title: str,
        channel: str,
        description: str,
        person_name: str,
        name_variations: list[str],
    ) -> dict:
        """Text-only analysis when audio/video is unavailable.
        Uses episode title, channel name, and description as context."""
        all_names = [person_name] + [v.strip() for v in name_variations if v.strip()]
        names_str = ", ".join(f'"{n}"' for n in all_names)

        prompt = f"""Based ONLY on the text metadata below (no audio/video available), answer:

1. Is this a PODCAST episode? A podcast is a recurring audio/video show with a host and guest format.
2. If yes, is the person known as {names_str} the GUEST or HOST?

Episode title: {title}
Channel / Show: {channel}
Description: {description[:1500] if description else "N/A"}

Respond ONLY with a valid JSON object — no markdown, no extra text:
{{
  "is_podcast": true or false,
  "confidence_score": integer from 0 to 100,
  "role": "guest" or "host" or "unknown" or null,
  "reason": "one or two sentence explanation"
}}

Note: since this is text-only analysis, keep confidence_score lower (max 75) to reflect uncertainty.
Rules:
- Set "is_podcast" to false for: tutorials, vlogs, ads, music videos, shorts, solo presentations
- Set "role" to null when "is_podcast" is false
- Set "role" to "unknown" when the person cannot be clearly identified from the text alone"""

        response = await self.client.aio.models.generate_content(
            model=MODEL,
            contents=[types.Part.from_text(text=prompt)],
        )
        return _extract_json(response.text)

    async def analyze_audio_url(
        self,
        audio_url: str,
        person_name: str,
        name_variations: list[str],
    ) -> dict:
        """Download audio, upload to Gemini File API, analyze.
        Falls back to first FALLBACK_MINUTES minutes if too large."""
        try:
            return await self._call_audio(audio_url, person_name, name_variations)
        except ClientError as exc:
            if _is_token_limit_error(exc):
                return await self._call_audio(
                    audio_url, person_name, name_variations,
                    max_bytes=FALLBACK_MINUTES * 60 * 16_000,  # ~10MB for typical podcast
                )
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable),
    )
    async def _call_audio(
        self,
        audio_url: str,
        person_name: str,
        name_variations: list[str],
        max_bytes: int | None = None,
    ) -> dict:
        """Download audio from URL, upload to Gemini File API, then analyze."""
        prompt = _build_prompt(person_name, name_variations)

        async with httpx.AsyncClient(timeout=120) as http:
            resp = await http.get(audio_url, follow_redirects=True)
            resp.raise_for_status()
            audio_bytes = resp.content

        if max_bytes:
            audio_bytes = audio_bytes[:max_bytes]

        content_type = resp.headers.get("content-type", "audio/mpeg").split(";")[0].strip()
        suffix = ".mp3" if "mpeg" in content_type else ".m4a"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            upload_fn = partial(self.client.files.upload, file=tmp_path)
            uploaded = await asyncio.get_running_loop().run_in_executor(None, upload_fn)

            response = await self.client.aio.models.generate_content(
                model=MODEL,
                contents=[
                    types.Part.from_uri(
                        file_uri=uploaded.uri,
                        mime_type=uploaded.mime_type or content_type,
                    ),
                    types.Part.from_text(text=prompt),
                ],
            )
            return _extract_json(response.text)
        finally:
            os.unlink(tmp_path)
