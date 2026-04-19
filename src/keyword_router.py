import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .categorizer import EmailContent
from .llm import Classification


class RouteValidationError(ValueError):
    pass


@dataclass
class Route:
    name: str
    fields: list[str]
    keywords: list[str]
    mode: str
    category: str
    tags: list[str]
    confidence: float

    def matches(self, email: EmailContent) -> bool:
        values = [self._field_value(email, f) for f in self.fields]
        haystack = " ".join(v.lower() for v in values if v)
        if not haystack:
            return False
        needles = [k.lower() for k in self.keywords]
        if self.mode == "all":
            return all(n in haystack for n in needles)
        return any(n in haystack for n in needles)

    @staticmethod
    def _field_value(email: EmailContent, field: str) -> str:
        if field == "sender":
            return email.sender or ""
        if field == "subject":
            return email.subject or ""
        if field == "body":
            return (email.body or email.snippet or "")
        if field == "snippet":
            return email.snippet or ""
        return ""


VALID_FIELDS = {"sender", "subject", "body", "snippet"}
VALID_MODES = {"any", "all"}


class KeywordRouter:
    def __init__(
        self,
        routes_file: Path,
        valid_categories: list[str],
        valid_tags: list[str],
    ) -> None:
        self._path = Path(routes_file)
        self._valid_categories = set(valid_categories)
        self._valid_tags = set(valid_tags)
        self._routes: list[Route] = self._load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def routes(self) -> list[Route]:
        return list(self._routes)

    def _load(self) -> list[Route]:
        if not self._path.exists():
            return []
        with open(self._path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, list):
            raise RouteValidationError(f"{self._path}: top-level must be a JSON array")
        return [self._parse(i, item) for i, item in enumerate(raw)]

    def _parse(self, index: int, item: dict[str, Any]) -> Route:
        name = str(item.get("name") or f"rule_{index}")
        match = item.get("match") or {}
        result = item.get("result") or {}

        fields = match.get("fields") or ["sender", "subject"]
        if not isinstance(fields, list) or not fields:
            raise RouteValidationError(f"{name}: 'fields' must be a non-empty list")
        for f in fields:
            if f not in VALID_FIELDS:
                raise RouteValidationError(
                    f"{name}: invalid field {f!r}. Allowed: {sorted(VALID_FIELDS)}"
                )

        keywords = match.get("keywords") or []
        if not isinstance(keywords, list) or not keywords:
            raise RouteValidationError(f"{name}: 'keywords' must be a non-empty list")

        mode = match.get("mode", "any")
        if mode not in VALID_MODES:
            raise RouteValidationError(
                f"{name}: 'mode' must be one of {sorted(VALID_MODES)}"
            )

        category = result.get("category")
        if category not in self._valid_categories:
            raise RouteValidationError(
                f"{name}: category {category!r} not in categories.json"
            )

        tags = result.get("tags") or []
        if not isinstance(tags, list):
            raise RouteValidationError(f"{name}: 'tags' must be a list")
        bad = [t for t in tags if t not in self._valid_tags]
        if bad:
            raise RouteValidationError(
                f"{name}: unknown tags {bad}. Allowed: {sorted(self._valid_tags)}"
            )

        confidence = float(result.get("confidence", 1.0))

        return Route(
            name=name,
            fields=list(fields),
            keywords=list(keywords),
            mode=mode,
            category=category,
            tags=list(tags),
            confidence=confidence,
        )

    def route(self, email: EmailContent) -> tuple[Classification, Route] | None:
        for r in self._routes:
            if r.matches(email):
                classification = Classification(
                    category=r.category,
                    tags=list(r.tags),
                    confidence=r.confidence,
                    raw=f"keyword_route:{r.name}",
                )
                return classification, r
        return None

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "name": r.name,
                "match": {
                    "fields": r.fields,
                    "keywords": r.keywords,
                    "mode": r.mode,
                },
                "result": {
                    "category": r.category,
                    "tags": r.tags,
                    "confidence": r.confidence,
                },
            }
            for r in self._routes
        ]
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        tmp.replace(self._path)

    def add(self, route: Route) -> None:
        if any(r.name == route.name for r in self._routes):
            raise RouteValidationError(f"rule {route.name!r} already exists")
        if route.category not in self._valid_categories:
            raise RouteValidationError(f"category {route.category!r} not in categories.json")
        bad = [t for t in route.tags if t not in self._valid_tags]
        if bad:
            raise RouteValidationError(f"unknown tags {bad}")
        self._routes.append(route)
        self.save()

    def remove(self, name: str) -> bool:
        before = len(self._routes)
        self._routes = [r for r in self._routes if r.name != name]
        if len(self._routes) == before:
            return False
        self.save()
        return True
