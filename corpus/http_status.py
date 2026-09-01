"""HTTP semantics derived from the taxonomy — written once, correct for every
adapter. Kept free of any web-framework import so it stays testable and usable
from FastAPI, Starlette or a plain WSGI handler alike."""

from __future__ import annotations

from corpus.errors import (
    CapabilityNotSupported,
    CorpusAuthError,
    CorpusConfigError,
    CorpusError,
    CorpusRateLimited,
    CorpusUnavailable,
    DocumentNotFound,
    InvalidDocument,
)

_TABLE: tuple[tuple[type[CorpusError], int], ...] = (
    (DocumentNotFound, 404),
    (InvalidDocument, 422),
    (CapabilityNotSupported, 501),
    (CorpusAuthError, 502),
    (CorpusConfigError, 502),
    (CorpusRateLimited, 503),
    (CorpusUnavailable, 503),
)


def http_status_for(error: CorpusError) -> int:
    """Map a corpus error to its HTTP status. Unknown members are 500 —
    an unmapped failure is a server bug, not a client one."""
    for cls, status in _TABLE:
        if isinstance(error, cls):
            return status
    return 500
