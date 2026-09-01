"""The single error taxonomy (proposal §4.5)."""

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


@pytest.mark.unit
class TestTaxonomy:
    @pytest.mark.parametrize("cls", [CorpusConfigError, CorpusAuthError, CapabilityNotSupported])
    def test_non_retryable_by_default(self, cls):
        err = cls("boom")
        assert isinstance(err, CorpusError)
        assert err.retryable is False
        assert err.retry_after is None
        assert str(err) == "boom"

    def test_document_not_found_carries_source(self):
        assert DocumentNotFound("gone", source="status").source == "status"
        assert DocumentNotFound("null data", source="empty").source == "empty"
        assert DocumentNotFound("gone", source="status").retryable is False

    def test_invalid_document_carries_kind(self):
        assert InvalidDocument("no headline", kind="missing_field").kind == "missing_field"
        assert InvalidDocument("bad date", kind="bad_value").kind == "bad_value"

    @pytest.mark.parametrize("kind", ["timeout", "connect", "http"])
    def test_unavailable_is_retryable_by_default(self, kind):
        err = CorpusUnavailable("down", kind=kind)
        assert err.kind == kind
        assert err.retryable is True

    def test_unavailable_retryability_can_be_overridden(self):
        """A 4xx that is not auth/not-found is an `http` failure that must not retry."""
        err = CorpusUnavailable("bad request", kind="http", retryable=False)
        assert err.retryable is False

    def test_rate_limited_carries_retry_after(self):
        err = CorpusRateLimited("slow down", retry_after=12.5)
        assert err.retryable is True
        assert err.retry_after == 12.5

    def test_rate_limited_without_header(self):
        assert CorpusRateLimited("slow down").retry_after is None

    def test_every_member_descends_from_corpus_error(self):
        for cls in (
            CorpusConfigError,
            CorpusAuthError,
            CapabilityNotSupported,
            DocumentNotFound,
            InvalidDocument,
            CorpusUnavailable,
            CorpusRateLimited,
        ):
            assert issubclass(cls, CorpusError)
