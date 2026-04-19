import itertools
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterator

from .categorizer import Categorizer, ClassificationError, EmailContent
from .dropped_log import DroppedEmailLog
from .email_providers import EmailMessage, EmailProvider
from .llm import Classification
from .metadata import Metadata


@dataclass
class ProcessResult:
    email: EmailMessage
    classification: Classification | None
    error: str | None = None
    dropped: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


ProgressCallback = Callable[[ProcessResult], None]


class BatchProcessor:
    def __init__(
        self,
        provider: EmailProvider,
        categorizer: Categorizer,
        metadata: Metadata,
        batch_size: int | None = None,
        dropped_log: DroppedEmailLog | None = None,
    ) -> None:
        self.provider = provider
        self.categorizer = categorizer
        self.metadata = metadata
        self.batch_size = batch_size or int(os.getenv("BATCH_SIZE", "10"))
        self.dropped_log = dropped_log

    def _classify_and_label(self, email: EmailMessage, apply: bool = True) -> ProcessResult:
        content = EmailContent(
            subject=email.subject,
            sender=email.sender,
            snippet=email.snippet,
            body=email.body,
        )
        try:
            classification = self.categorizer.classify(content)
        except ClassificationError as exc:
            if self.dropped_log:
                self.dropped_log.append(email, str(exc), self.categorizer.max_retries)
            return ProcessResult(email=email, classification=None, error=str(exc), dropped=True)

        try:
            if apply:
                self.provider.apply_labels(email.id, classification.category, classification.tags)
                self._cache_classification(email, classification)
            return ProcessResult(email=email, classification=classification)
        except Exception as exc:
            return ProcessResult(email=email, classification=classification, error=f"label apply failed: {exc}")

    def _cache_classification(self, email: EmailMessage, classification: Classification) -> None:
        cache = getattr(self.metadata, "cache", None)
        if cache is None:
            return
        cache.mark_processed(
            email_id=email.id,
            provider=self.provider.name,
            account=self.provider.account,
            category=classification.category,
            tags=list(classification.tags),
            confidence=classification.confidence,
            subject=email.subject,
            sender=email.sender,
            email_date=email.date,
            run_id=None,
        )

    def run_test(
        self,
        limit: int = 10,
        on_progress: ProgressCallback | None = None,
    ) -> list[ProcessResult]:
        """Dry run: fetch the latest `limit` emails, classify them, but do not
        apply labels or update metadata. Used to preview LLM behavior."""
        until = datetime.now(timezone.utc)
        results: list[ProcessResult] = []
        for email in itertools.islice(
            self.provider.fetch_emails(since=None, until=until, order="desc"), limit
        ):
            result = self._classify_and_label(email, apply=False)
            results.append(result)
            if on_progress:
                on_progress(result)
        return results

    def run_default(self, on_progress: ProgressCallback | None = None) -> list[ProcessResult]:
        since = self.metadata.last_run_at
        until = datetime.now(timezone.utc)
        results: list[ProcessResult] = []
        for email in self.provider.fetch_emails(since=since, until=until):
            result = self._classify_and_label(email)
            results.append(result)
            if on_progress:
                on_progress(result)
        self.metadata.record_run(
            provider=self.provider.name,
            account=self.provider.account,
            emails_processed=len(results),
            date_from=since,
            date_to=until,
            mode="default",
        )
        return results

    def run_range(
        self,
        date_from: datetime,
        date_to: datetime,
        on_progress: ProgressCallback | None = None,
    ) -> list[ProcessResult]:
        results: list[ProcessResult] = []
        for email in self.provider.fetch_emails(since=date_from, until=date_to):
            result = self._classify_and_label(email)
            results.append(result)
            if on_progress:
                on_progress(result)
        self.metadata.record_run(
            provider=self.provider.name,
            account=self.provider.account,
            emails_processed=len(results),
            date_from=date_from,
            date_to=date_to,
            mode="range",
        )
        return results

    def run_batch(
        self,
        on_progress: ProgressCallback | None = None,
        fresh: bool = False,
        max_batches: int | None = None,
    ) -> list[ProcessResult]:
        if fresh:
            self.metadata.reset_batch()
        self.metadata.begin_batch(self.provider.name, self.provider.account)
        completed = self.metadata.batch_completed_ids
        until = datetime.now(timezone.utc)

        results: list[ProcessResult] = []
        batches_run = 0
        chunk_ids: list[str] = []
        chunk_last_date: datetime | None = None

        def flush() -> None:
            nonlocal chunk_ids, chunk_last_date
            if chunk_ids:
                self.metadata.mark_batch_processed(chunk_ids, chunk_last_date)
                chunk_ids = []
                chunk_last_date = None

        for email in self.provider.fetch_emails(since=None, until=until):
            if email.id in completed:
                continue
            result = self._classify_and_label(email)
            results.append(result)
            if on_progress:
                on_progress(result)
            if result.ok:
                chunk_ids.append(email.id)
                chunk_last_date = email.date
            if len(chunk_ids) >= self.batch_size:
                flush()
                batches_run += 1
                if max_batches is not None and batches_run >= max_batches:
                    break

        flush()
        self.metadata.end_batch()
        self.metadata.record_run(
            provider=self.provider.name,
            account=self.provider.account,
            emails_processed=len(results),
            date_from=None,
            date_to=until,
            mode="batch",
        )
        return results
