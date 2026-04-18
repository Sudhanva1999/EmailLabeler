import os

from .base import Classification, LLMProvider


def get_llm_provider() -> LLMProvider:
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider()
    if provider == "local":
        from .local import LocalLLMProvider
        return LocalLLMProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (expected 'gemini' or 'local')")


__all__ = ["Classification", "LLMProvider", "get_llm_provider"]
