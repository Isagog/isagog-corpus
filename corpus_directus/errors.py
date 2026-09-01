"""Native failure → taxonomy. One place, used by the client and the notifier.

Nothing from `httpx` is chained onto the raised error (`from None`): the port's
promise is that only the taxonomy escapes, and a `__cause__` a consumer can
`isinstance`-check is still a dependency on the vendor's transport. The native
exception's type and message are folded into the message instead, so nothing
needed for debugging is lost.
"""

from __future__ import annotations

from typing import Literal

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


#: Whether the request asked for ONE document by id or for a collection. It is
#: the only thing that makes a Directus 403 interpretable — see `from_status`.
Scope = Literal["document", "collection"]


def from_status(
    status_code: int,
    context: str,
    retry_after: str | None = None,
    *,
    scope: Scope = "collection",
) -> CorpusError:
    """Native status → taxonomy.

    The 403 row is the subtle one. Directus hides existence on purpose: it
    answers `403 FORBIDDEN` for a document that does not exist, for one the
    token may not read, and for a whole collection the token may not read —
    all with the same body. Only `401 INVALID_CREDENTIALS` means the
    credentials themselves were rejected.

    So the request's shape decides. Asking for one document by id, "absent"
    and "hidden from you" call for the same thing at that call site, and
    `DocumentNotFound` is what every consumer's retry table already handles.
    A forbidden *listing* is never one missing row — it is a misconfigured
    permission or a wrong collection name — and degrading it to an empty
    result would let a pipeline process nothing while reporting success, so
    that stays an auth failure.
    """
    detail = f"{context}: HTTP {status_code}"
    if status_code == 401:
        return CorpusAuthError(detail)
    if status_code == 403:
        if scope == "document":
            return DocumentNotFound(detail, source="status")
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
