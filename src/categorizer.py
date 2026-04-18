import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .llm import Classification, LLMProvider
from .normalizer import normalize_body, normalize_subject


class ClassificationError(Exception):
    """Raised when the LLM produces output that cannot be mapped to a valid
    category after all retries are exhausted."""


@dataclass
class EmailContent:
    subject: str
    sender: str
    snippet: str
    body: str = ""


class Categorizer:
    def __init__(
        self,
        categories_file: Path,
        llm: LLMProvider,
        body_char_limit: int | None = None,
        max_retries: int | None = None,
        retry_backoff: float = 0.5,
    ) -> None:
        self._categories_file = Path(categories_file)
        self._llm = llm
        self._body_limit = (
            body_char_limit
            if body_char_limit is not None
            else int(os.getenv("BODY_CHAR_LIMIT", "4000"))
        )
        self._max_retries = max(
            1,
            max_retries
            if max_retries is not None
            else int(os.getenv("MAX_CLASSIFY_RETRIES", "5")),
        )
        self._retry_backoff = retry_backoff
        self._categories, self._tags = self._load()
        self._system_block = self._build_system_block()

    def _load(self) -> tuple[dict[str, str], dict[str, str]]:
        with open(self._categories_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data["primary_categories"], data["tags"]

    @property
    def category_names(self) -> list[str]:
        return list(self._categories.keys())

    @property
    def tag_names(self) -> list[str]:
        return list(self._tags.keys())

    @property
    def max_retries(self) -> int:
        return self._max_retries

    def _build_system_block(self) -> str:
        cats = "\n".join(f"- {k}: {v}" for k, v in self._categories.items())
        tags = "\n".join(f"- {k}: {v}" for k, v in self._tags.items())
        valid_cats = ", ".join(self._categories.keys())
        valid_tags = ", ".join(self._tags.keys())
        return (
            "You are an email classifier.\n\n"
            f"PRIMARY CATEGORIES — output MUST be EXACTLY one of: {valid_cats}\n"
            f"{cats}\n\n"
            f"TAGS — each tag MUST be one of: {valid_tags}\n"
            f"{tags}\n\n"
            "Respond ONLY as a single JSON object with this exact shape:\n"
            '{"category": "<one of the categories>", "tags": ["<tag>", ...], '
            '"confidence": <float 0.0-1.0>}\n\n'
            "Rules:\n"
            "- 'category' is a single lowercase word from the allowed list. "
            "Do NOT invent new categories. If unsure, use 'other'.\n"
            "- 'tags' is an array of zero or more tags from the allowed tag list.\n"
            "- No prose, no markdown fences, no comments. JSON only."
        )

    def _build_prompt(self, subject: str, sender: str, body: str, attempt: int) -> str:
        prompt = (
            f"{self._system_block}\n\n"
            "EMAIL:\n"
            f"Subject: {subject}\n"
            f"From: {sender}\n"
            f"Body:\n{body}"
        )
        if attempt > 1:
            valid = ", ".join(self._categories.keys())
            prompt += (
                "\n\n=== RETRY ===\n"
                "Your previous response was rejected: it was not valid JSON or the "
                "'category' was not in the allowed list. Try again.\n"
                f"The category MUST be one of EXACTLY: {valid}\n"
                "Output ONLY the JSON object. No other text."
            )
        return prompt

    def classify(self, email: EmailContent) -> Classification:
        subject = normalize_subject(email.subject)
        sender = normalize_subject(email.sender)
        body = normalize_body(email.body or email.snippet or "", max_chars=self._body_limit)

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            prompt = self._build_prompt(subject, sender, body, attempt)
            try:
                result = self._llm.classify(prompt)
                self._validate(result)
                return result
            except Exception as exc:
                last_error = exc
                if attempt < self._max_retries:
                    time.sleep(self._retry_backoff * attempt)

        raise ClassificationError(
            f"Failed to produce a valid classification after {self._max_retries} attempts: {last_error}"
        ) from last_error

    def _validate(self, result: Classification) -> None:
        if not result.category or result.category not in self._categories:
            raise ClassificationError(
                f"Invalid category {result.category!r}; must be one of {list(self._categories)}"
            )
        result.tags = [t for t in result.tags if t in self._tags]
