from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Classification:
    category: str
    tags: list[str]
    confidence: float
    raw: str = ""


class LLMProvider(ABC):
    @abstractmethod
    def classify(self, prompt: str) -> Classification:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...
