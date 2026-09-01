"""Native failure → taxonomy. One place, used by the client and the notifier.

Nothing from `httpx` is chained onto the raised error (`from None`): the port's
promise is that only the taxonomy escapes, and a `__cause__` a consumer can
`isinstance`-check is still a dependency on the vendor's transport. The native
exception's type and message are folded into the message instead, so nothing
needed for debugging is lost.
"""

from __future__ import annotations

import httpx
from corpus.errors import (
    CorpusAuthError,
    CorpusError,
    CorpusRateLimited,
    CorpusUnavailable,
    DocumentNotFound,
)


def from_transport_error(exc: Exception, context: str) -> CorpusError:
    detail = f"{context}: {type(exc).__name__}: {exc}"
    if isinstance(exc, httpx.TimeoutException):
        return CorpusUnavailable(detail, kind="timeout")
    if isinstance(exc, httpx.TransportError):
        # ConnectError, ReadError, RemoteProtocolError, … — the connection did
        # not survive; all of them are worth another attempt.
        return CorpusUnavailable(detail, kind="connect")
    return CorpusUnavailable(detail, kind="http")


def from_status(status_code: int, context: str, retry_after: str | None = None) -> CorpusError:
    detail = f"{context}: HTTP {status_code}"
    if status_code in (401, 403):
        return CorpusAuthError(detail)
    if status_code == 404:
        return DocumentNotFound(detail, source="status")
    if status_code == 429:
        return CorpusRateLimited(detail, retry_after=parse_retry_after(retry_after))
    if 400 <= status_code < 500:
        # A malformed request fails identically on retry.
        return CorpusUnavailable(detail, kind="http", retryable=False)
    return CorpusUnavailable(detail, kind="http")


def parse_retry_after(value: str | None) -> float | None:
    """Only the delta-seconds form. An HTTP-date is left to the caller's policy
    rather than guessed against a clock we do not control."""
    if not value:
        return None
    try:
        return float(value.strip())
    except ValueError:
        return None
