"""Backends declare what they support; consumers declare what they need.

The check runs once at startup and names the gap. A CMS without editions is a
first-class corpus — the consumer that needs editions learns so on day one,
as a printed list, not as a NoneType crash in week three.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from corpus.errors import CapabilityNotSupported


class Capability(StrEnum):
    ARTICLES = "articles"  # get_article — the only mandatory one
    ARTICLE_LISTING = "article_listing"  # search/iter + pagination
    ARTICLE_BY_SLUG = "article_by_slug"
    SECTIONS = "sections"
    EDITIONS = "editions"  # get_edition / list_editions
    EDITION_PDF = "edition_pdf"
    ASSETS = "assets"  # fetch binary
    ASSET_STREAMING = "asset_streaming"
    DATE_FILTER = "date_filter"
    CHANGE_SIGNALS = "change_signals"  # inbound save notifications parseable
    RESULT_WEBHOOK = "result_webhook"  # a Notifier is configured


class CorpusCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    supported: frozenset[Capability]
    max_page_size: int = 100
    max_ids_per_query: int = 100  # URL-length chunking surfaces here
    id_format: str = "opaque"  # "uuid" | "int" | "opaque" — documentation, not validation

    def supports(self, capability: Capability) -> bool:
        return capability in self.supported


class CorpusRequirements(BaseModel):
    """What one consumer needs. Checked once at startup."""

    model_config = ConfigDict(frozen=True)

    required: frozenset[Capability]

    def check(self, caps: CorpusCapabilities) -> None:
        missing = self.required - caps.supported
        if missing:
            raise CapabilityNotSupported(
                f"backend lacks required capabilities: {sorted(m.value for m in missing)}"
            )
