"""One exception-handler table, derived from the taxonomy (proposal §6)."""

import pytest
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
from corpus.http_status import http_status_for


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (DocumentNotFound("gone", source="status"), 404),
        (DocumentNotFound("null", source="empty"), 404),
        (InvalidDocument("bad", kind="bad_value"), 422),
        (CorpusAuthError("401"), 502),
        (CorpusConfigError("no base url"), 502),
        (CorpusUnavailable("timeout", kind="timeout"), 503),
        (CorpusRateLimited("429", retry_after=3.0), 503),
        (CapabilityNotSupported("editions"), 501),
        (CorpusError("something else"), 500),
    ],
)
def test_http_status_table(error, expected):
    assert http_status_for(error) == expected
