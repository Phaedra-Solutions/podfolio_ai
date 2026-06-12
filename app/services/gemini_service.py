import asyncio
import json
import os
import re
import tempfile
from functools import partial

import httpx
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings

MODEL = "gemini-2.0-flash"


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

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def analyze_youtube(
        self,
        video_url: str,
        person_name: str,
        name_variations: list[str],
    ) -> dict:
        """Pass YouTube URL directly to Gemini — no download needed."""
        prompt = _build_prompt(person_name, name_variations)

        response = await self.client.aio.models.generate_content(
            model=MODEL,
            contents=[
                types.Part.from_uri(file_uri=video_url, mime_type="video/*"),
                types.Part.from_text(text=prompt),
            ],
        )
        return _extract_json(response.text)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def analyze_audio_url(
        self,
        audio_url: str,
        person_name: str,
        name_variations: list[str],
    ) -> dict:
        """Download audio from URL, upload to Gemini File API, then analyze."""
        prompt = _build_prompt(person_name, name_variations)

        # Download audio bytes
        async with httpx.AsyncClient(timeout=120) as http:
            resp = await http.get(audio_url, follow_redirects=True)
            resp.raise_for_status()
            audio_bytes = resp.content

        # Detect mime type from Content-Type header or URL extension
        content_type = resp.headers.get("content-type", "audio/mpeg").split(";")[0].strip()
        suffix = ".mp3" if "mpeg" in content_type else ".m4a"

        # Write to temp file and upload via Gemini File API (sync call → thread pool)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        try:
            upload_fn = partial(self.client.files.upload, file=tmp_path)
            uploaded = await asyncio.get_event_loop().run_in_executor(None, upload_fn)

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
