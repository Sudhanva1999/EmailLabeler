import os

from google import genai
from google.genai import types

from .base import Classification, LLMProvider
from ._parse import parse_classification


class GeminiProvider(LLMProvider):
    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self._client = genai.Client(api_key=api_key)

    @property
    def name(self) -> str:
        return f"gemini:{self._model_name}"

    def classify(self, prompt: str) -> Classification:
        response = self._client.models.generate_content(
            model=self._model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        return parse_classification(response.text or "")
