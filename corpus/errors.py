"""The single error taxonomy every backend maps its failures into.

`retryable` and `retry_after` are carried as *data* so that Temporal retry
policies, FastAPI exception handlers and plain scripts all derive their
behaviour from one truth table instead of re-deriving it per call site.
"""

from __future__ import annotations

from typing import Literal


class CorpusError(Exception):
    """Base of the taxonomy. Nothing else may escape a Corpus method."""

    retryable: bool = False
    retry_after: float | None = None

    def __init__(
        self,
        message: str,
        *,
        retryable: bool | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if retryable is not None:
            self.retryable = retryable
        if retry_after is not None:
            self.retry_after = retry_after


class CorpusConfigError(CorpusError):
    """Bad base URL, missing credentials, contradictory settings."""


class CorpusAuthError(CorpusError):
    """The backend rejected our credentials (401/403 and equivalents)."""


class CapabilityNotSupported(CorpusError):
    """Port misuse: the backend never claimed to support this operation."""


class DocumentNotFound(CorpusError):
    """No such document.

    `source` keeps the two ways a backend says "nothing here" distinguishable:
    a 404 status, or a 200 whose payload is null/empty. memaflow2 reports them
    as different `ApplicationError.type` strings, so the distinction is parity.
    """

    def __init__(self, message: str, *, source: Literal["status", "empty"]) -> None:
        super().__init__(message)
        self.source: Literal["status", "empty"] = source


class InvalidDocument(CorpusError):
    """A document exists but cannot be used.

    `kind` separates a field that was absent from the payload
    (`missing_field`) from one that was present but unusable (`bad_value`).
    """

    def __init__(self, message: str, *, kind: Literal["missing_field", "bad_value"]) -> None:
        super().__init__(message)
        self.kind: Literal["missing_field", "bad_value"] = kind


class CorpusUnavailable(CorpusError):
    """The backend could not answer. Retryable unless the caller says otherwise
    — a 4xx that is neither auth nor not-found is an `http` failure that will
    fail identically on retry."""

    retryable = True

    def __init__(
        self,
        message: str,
        *,
        kind: Literal["timeout", "connect", "http"],
        retryable: bool = True,
    ) -> None:
        super().__init__(message, retryable=retryable)
        self.kind: Literal["timeout", "connect", "http"] = kind


class CorpusRateLimited(CorpusError):
    """Throttled. `retry_after` carries the backend's own advice when it gave one."""

    retryable = True

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, retry_after=retry_after)
