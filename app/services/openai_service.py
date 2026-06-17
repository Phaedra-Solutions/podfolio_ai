"""
OpenAI fallback service — used when Gemini quota is exhausted (429).
Uses GPT-4o-mini for text-only analysis (title + description + channel).
"""

import json
import logging
import re

from app.core.config import settings

logger = logging.getLogger(__name__)


def _build_prompt(
    title: str,
    channel: str,
    description: str,
    person_name: str,
    name_variations: list[str],
) -> str:
    all_names = [person_name] + [v.strip() for v in name_variations if v.strip()]
    names_str = ", ".join(f'"{n}"' for n in all_names)

    return f"""Based ONLY on the text metadata below, answer:

1. Is this a PODCAST episode? A podcast is a recurring audio/video show with a host and guest format.
2. If yes, is the person known as {names_str} the GUEST or HOST?

Episode title: {title}
Channel / Show: {channel}
Description: {description[:2000] if description else "N/A"}

Respond ONLY with a valid JSON object — no markdown, no extra text:
{{
  "is_podcast": true or false,
  "confidence_score": integer from 0 to 100,
  "role": "guest" or "host" or "unknown" or null,
  "reason": "one or two sentence explanation"
}}

Rules:
- Set "is_podcast" to false for: tutorials, vlogs, ads, music videos, shorts, solo presentations
- Set "role" to null when "is_podcast" is false
- Set "role" to "unknown" when the person cannot be clearly identified from text alone
- Keep confidence_score max 75 (text-only analysis has inherent uncertainty)"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text, flags=re.MULTILINE).strip()
    text = re.sub(r"```$", "", text, flags=re.MULTILINE).strip()
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
            "reason": f"Failed to parse OpenAI response: {text[:200]}",
        }


class OpenAIService:
    def __init__(self):
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not set")
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    async def analyze_text(
        self,
        title: str,
        channel: str,
        description: str,
        person_name: str,
        name_variations: list[str],
    ) -> dict:
        prompt = _build_prompt(title, channel, description, person_name, name_variations)

        response = await self._client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
        )

        result = _extract_json(response.choices[0].message.content or "")
        logger.info(
            "OpenAI analysis for '%s': is_podcast=%s role=%s confidence=%s",
            title[:50], result.get("is_podcast"), result.get("role"), result.get("confidence_score"),
        )
        return result
