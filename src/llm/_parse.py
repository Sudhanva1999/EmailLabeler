import json
import re

from .base import Classification

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_classification(text: str) -> Classification:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()

    match = _JSON_BLOCK.search(cleaned)
    if not match:
        raise ValueError(f"No JSON object found in LLM output: {text!r}")

    data = json.loads(match.group(0))
    category = str(data.get("category", "other")).strip().lower()
    raw_tags = data.get("tags", []) or []
    tags = [str(t).strip().lower() for t in raw_tags if str(t).strip()]
    confidence = float(data.get("confidence", 0.0))

    return Classification(category=category, tags=tags, confidence=confidence, raw=text)
