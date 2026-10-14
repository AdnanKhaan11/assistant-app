"""Extract a JSON object from potentially wrapped or noisy model output."""
from __future__ import annotations

import json
import re


_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(text: str) -> dict:
    """Parse a JSON object from plain, fenced, or lightly prefixed model output."""
    if not isinstance(text, str):
        raise ValueError("JSON input must be text")

    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        match = _OBJECT_PATTERN.search(candidate)
        if match is None:
            raise ValueError("No valid JSON object found") from None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as error:
            raise ValueError("No valid JSON object found") from error

    if not isinstance(parsed, dict):
        raise ValueError("JSON value must be an object")
    return parsed
