"""The port itself.

An ABC rather than a Protocol, because the template methods carry real shared
behaviour: draining pages, buffering a stream under a size guard, and gating
optional surface behind declared capabilities.

The mandatory surface is deliberately tiny — `capabilities`, `get_article`,
`get_article_ref`, `search_articles`, `ping`. Editions and assets are opt-in:
a web-native archive with no print edition is a first-class corpus, not a pile
of NotImplementedError.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Any

from corpus.capabilities import Capability, CorpusCapabilities, CorpusRequirements
from corpus.errors import CapabilityNotSupported, InvalidDocument
from corpus.models import Article, ArticlePage, ArticleRef, Edition, EditionCover, EditionRef
from corpus.query import ArticleQuery, EditionQuery
from corpus.signals import ChangeSignal


class Corpus(ABC):
    """Read-side port over a newspaper/archive backend."""

    # --- identity ---------------------------------------------------------
    @property
    @abstractmethod
    def capabilities(self) -> CorpusCapabilities: ...

    def require(self, requirements: CorpusRequirements) -> None:
        """Fail fast at boot, naming the gap. Concrete and final by intent."""
        requirements.check(self.capabilities)

    def _require(self, capability: Capability) -> None:
        if not self.capabilities.supports(capability):
            raise CapabilityNotSupported(f"backend does not support {capability.value!r}")

    # --- articles ---------------------------------------------------------
    @abstractmethod
    async def get_article(self, article_id: str) -> Article: ...

    @abstractmethod
    async def get_article_ref(self, article_id: str) -> ArticleRef: ...

    @abstractmethod
    async def search_articles(
        self, query: ArticleQuery, cursor: str | None = None
    ) -> ArticlePage: ...

    async def iter_articles(self, query: ArticleQuery) -> AsyncIterator[ArticleRef]:
        """Concrete: drains search_articles pages. Adapters override only if
        they have a cheaper native scan."""
        cursor: str | None = None
        while True:
            page = await self.search_articles(query, cursor)
            for ref in page.items:
                yield ref
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    # --- editions (capability-gated: default raises) ----------------------
    async def get_edition(self, edition_id: str) -> Edition:
        raise CapabilityNotSupported("editions")

    async def list_editions(self, query: EditionQuery) -> tuple[EditionRef, ...]:
        raise CapabilityNotSupported("editions")

    async def get_edition_cover(self, edition_id: str) -> EditionCover:
        """The front page of one edition.

        Separate from EDITIONS: a CMS can hold dated bundles of articles
        without modelling a front page at all, and a web-native archive has no
        front page to model. `DocumentNotFound` means this edition has no
        cover; `CapabilityNotSupported` means this backend has no covers.
        """
        raise CapabilityNotSupported("edition_cover")

    # --- assets -----------------------------------------------------------
    def stream_asset(self, asset_id: str) -> AsyncIterator[bytes]:
        raise CapabilityNotSupported("assets")

    async def fetch_asset(self, asset_id: str, *, max_bytes: int) -> bytes:
        """Concrete: buffers stream_asset with a hard size guard.

        `max_bytes` is keyword-only and has no default on purpose — the
        unguarded whole-PDF buffer is a defect no caller can now reproduce.
        """
        chunks: list[bytes] = []
        total = 0
        async for chunk in self.stream_asset(asset_id):
            total += len(chunk)
            if total > max_bytes:
                raise InvalidDocument(
                    f"asset {asset_id} exceeds {max_bytes} bytes", kind="bad_value"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    # --- inbound evidence -------------------------------------------------
    def parse_change(
        self,
        document_id: str,
        body: Mapping[str, Any] | None,
        *,
        received_at: datetime | None = None,
    ) -> ChangeSignal:
        """Translate a backend's save notification into vendor-neutral evidence.

        Adapters that declare CHANGE_SIGNALS override this. Implementations
        must never raise: unknown vocabulary degrades to UNKNOWN.
        """
        raise CapabilityNotSupported("change_signals")

    # --- health -----------------------------------------------------------
    @abstractmethod
    async def ping(self) -> None: ...

    async def aclose(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Release transport resources. No-op by default."""
