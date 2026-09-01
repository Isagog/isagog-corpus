"""Capability declaration and the boot-time requirement check (proposal §4.3)."""

import pytest
from corpus.capabilities import Capability, CorpusCapabilities, CorpusRequirements
from corpus.errors import CapabilityNotSupported
from pydantic import ValidationError

FULL = CorpusCapabilities(supported=frozenset(Capability))


@pytest.mark.unit
class TestCorpusCapabilities:
    def test_defaults(self):
        caps = CorpusCapabilities(supported=frozenset({Capability.ARTICLES}))
        assert caps.max_page_size == 100
        assert caps.max_ids_per_query == 100
        assert caps.id_format == "opaque"

    def test_is_frozen(self):
        with pytest.raises(ValidationError):
            FULL.max_page_size = 500

    def test_supports_helper(self):
        caps = CorpusCapabilities(supported=frozenset({Capability.ARTICLES}))
        assert caps.supports(Capability.ARTICLES) is True
        assert caps.supports(Capability.EDITIONS) is False


@pytest.mark.unit
class TestCorpusRequirements:
    def test_satisfied_requirements_pass_silently(self):
        CorpusRequirements(required=frozenset({Capability.ARTICLES})).check(FULL)

    def test_missing_capabilities_are_named_in_the_message(self):
        """The WordPress case from §5.3: the gap list is the migration estimate."""
        wordpress = CorpusCapabilities(
            supported=frozenset({Capability.ARTICLES, Capability.ARTICLE_LISTING})
        )
        memaflow = CorpusRequirements(
            required=frozenset(
                {
                    Capability.ARTICLES,
                    Capability.EDITIONS,
                    Capability.EDITION_PDF,
                    Capability.RESULT_WEBHOOK,
                }
            )
        )
        with pytest.raises(CapabilityNotSupported) as excinfo:
            memaflow.check(wordpress)
        message = str(excinfo.value)
        assert "editions" in message
        assert "edition_pdf" in message
        assert "result_webhook" in message
        assert "articles" not in message.replace("edition_pdf", "")

    def test_empty_requirements_always_pass(self):
        CorpusRequirements(required=frozenset()).check(CorpusCapabilities(supported=frozenset()))


@pytest.mark.unit
def test_capability_values_are_stable_strings():
    assert Capability.ARTICLES.value == "articles"
    assert Capability.ARTICLE_LISTING.value == "article_listing"
    assert Capability.ARTICLE_BY_SLUG.value == "article_by_slug"
    assert Capability.SECTIONS.value == "sections"
    assert Capability.EDITIONS.value == "editions"
    assert Capability.EDITION_PDF.value == "edition_pdf"
    assert Capability.ASSETS.value == "assets"
    assert Capability.ASSET_STREAMING.value == "asset_streaming"
    assert Capability.DATE_FILTER.value == "date_filter"
    assert Capability.CHANGE_SIGNALS.value == "change_signals"
    assert Capability.RESULT_WEBHOOK.value == "result_webhook"
