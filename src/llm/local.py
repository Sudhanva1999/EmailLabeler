import os

import requests

from .base import Classification, LLMProvider
from ._parse import parse_classification


class LocalLLMProvider(LLMProvider):
    """OpenAI-compatible local LLM client (Ollama, LM Studio, etc.)."""

    def __init__(self) -> None:
        self._base_url = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
        self._model_name = os.getenv("LOCAL_LLM_MODEL", "llama3")
        self._api_key = os.getenv("LOCAL_LLM_API_KEY", "ollama")

    @property
    def name(self) -> str:
        return f"local:{self._model_name}"

    def classify(self, prompt: str) -> Classification:
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model_name,
            "messages": [
                {"role": "system", "content": "You are a strict JSON-only classifier."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return parse_classification(text)
