"""
Generates a YouTube title, description, tags, and Instagram caption for a
video using the Groq API (free tier, OpenAI-compatible, simple bearer auth).

Swap providers here if you want a different one -- nothing else in the
pipeline needs to change as long as generate_metadata() returns the same shape.
"""

from __future__ import annotations

import json
import logging
import os

import requests
from dotenv import load_dotenv

from utils import retry_on_transient

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_MODEL = "llama-3.3-70b-versatile"  # strong, fast, free-tier friendly
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _system_prompt(content_description: str) -> str:
    return f"""You are a social media editor writing metadata for {content_description}, \
published on YouTube and Instagram Reels simultaneously. Given a source filename \
(and optional context), output ONLY valid JSON with this exact shape, no markdown \
fences, no preamble:

{{"title": "...", "description": "...", "tags": ["...", "..."], "ig_caption": "..."}}

Rules:
- title: under 70 characters, curiosity-driven but not misleading, for YouTube
- description: 2-4 sentences for YouTube, includes relevant keywords naturally, ends with 3-5 hashtags
- tags: 8-15 relevant single/short-phrase tags, no # symbol
- ig_caption: shorter and punchier than the YouTube description, 1-2 sentences, ends with 5-8 hashtags suited to Instagram Reels discovery
"""


_REQUIRED_KEYS = ("title", "description", "tags", "ig_caption")


@retry_on_transient(max_attempts=4, base_delay=2.0)
def _call_groq(system_prompt: str, user_prompt: str) -> str:
    """Fire a single Groq request and return the raw content string."""
    api_key = os.environ["GROQ_API_KEY"].strip()
    body = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }

    response = requests.post(
        GROQ_URL,
        json=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        timeout=30,
    )
    if not response.ok:
        logger.error("Groq API error %s: %s", response.status_code, response.text)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def generate_metadata(
    filename: str,
    content_description: str = "short-form video content",
    extra_context: str = "",
) -> dict:
    """Return {"title": str, "description": str, "tags": list[str], "ig_caption": str}.

    Args:
        filename: original video filename — used as the primary signal for
                  title / description generation.
        content_description: what this channel's videos are about — steers
                             titles/captions.
        extra_context: optional free-text context appended to the prompt
                       (e.g. episode number, guest name, show notes snippet).

    Raises:
        ValueError: if the Groq response is missing required keys.
        requests.HTTPError: on unrecoverable API errors (after retries).
        json.JSONDecodeError: if the model returns malformed JSON.
    """
    user_prompt = f"Filename: {filename}"
    if extra_context:
        user_prompt += f"\nContext: {extra_context}"

    raw = _call_groq(_system_prompt(content_description), user_prompt)
    # Strip accidental markdown fences the model might add despite instructions
    raw = raw.replace("```json", "").replace("```", "").strip()

    data = json.loads(raw)

    missing = [k for k in _REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(
            f"Groq response is missing required keys: {missing}. "
            f"Raw response: {raw!r}"
        )

    return data


if __name__ == "__main__":
    # quick manual test
    logging.basicConfig(level=logging.INFO)
    result = generate_metadata("ep12_clip_founder_burnout.mp4", "podcast clips")
    print(json.dumps(result, indent=2))
