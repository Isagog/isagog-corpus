"""In-memory reference implementation.

Not a toy: `backend="fake"` gives every consumer a no-network demo/e2e mode,
and running the contract suite against it is what keeps the suite honest
before any HTTP exists.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any, final

from corpus.base import Corpus
from corpus.capabilities import Capability, CorpusCapabilities
from corpus.cursor import decode_cursor, encode_cursor
from corpus.errors import CorpusUnavailable, DocumentNotFound
from corpus.models import PUBLISHED, Article, ArticlePage, ArticleRef, Edition, EditionRef
from corpus.normalize import normalize_optional_date
from corpus.query import ArticleOrder, ArticleQuery, EditionQuery
from corpus.signals import (
    ChangeSignal,
    coerce_actor_kind,
    coerce_change_kind,
    coerce_signal_date,
)
from corpus.testing.fixtures import CorpusSeed, SeedArticle, SeedEdition

FULL_HOUSE = CorpusCapabilities(supported=frozenset(Capability), id_format="uuid")


@final
class FakeCorpus(Corpus):
    def __init__(
        self,
        *,
        seed: CorpusSeed,
        capabilities: CorpusCapabilities | None = None,
        healthy: bool = True,
        chunk_size: int = 64,
    ) -> None:
        self._seed = seed
        self._capabilities = capabilities or FULL_HOUSE
        self._healthy = healthy
        self._chunk_size = chunk_size
        self._by_id: Mapping[str, SeedArticle] = {s.article.id: s for s in seed.articles}
        self._editions: Mapping[str, SeedEdition] = {e.id: e for e in seed.editions}

    @classmethod
    def from_seed(
        cls,
        seed: CorpusSeed,
        *,
        capabilities: CorpusCapabilities | None = None,
        healthy: bool = True,
    ) -> FakeCorpus:
        return cls(seed=seed, capabilities=capabilities, healthy=healthy)

    # --- identity ---------------------------------------------------------
    @property
    def capabilities(self) -> CorpusCapabilities:
        return self._capabilities

    # --- articles ---------------------------------------------------------
    async def get_article(self, article_id: str) -> Article:
        self._require(Capability.ARTICLES)
        return self._seed_article(article_id).article

    async def get_article_ref(self, article_id: str) -> ArticleRef:
        self._require(Capability.ARTICLES)
        return _to_ref(self._seed_article(article_id))

    async def search_articles(self, query: ArticleQuery, cursor: str | None = None) -> ArticlePage:
        self._require(Capability.ARTICLE_LISTING)
        matches = [s for s in self._seed.articles if _matches(s, query)]
        descending = query.order is ArticleOrder.PUBLISH_DATE_DESC
        matches.sort(key=_sort_key, reverse=descending)

        if cursor is not None:
            position = decode_cursor(cursor)
            key = (str(position.get("d", "")), str(position.get("i", "")))
            matches = [s for s in matches if _is_after(_sort_key(s), key, descending)]

        page, remaining = matches[: query.page_size], matches[query.page_size :]
        next_cursor = None
        if remaining and page:
            last_date, last_id = _sort_key(page[-1])
            next_cursor = encode_cursor({"d": last_date, "i": last_id})
        return ArticlePage(items=tuple(_to_ref(s) for s in page), next_cursor=next_cursor)

    # --- editions ---------------------------------------------------------
    async def get_edition(self, edition_id: str) -> Edition:
        self._require(Capability.EDITIONS)
        edition = self._editions.get(edition_id)
        if edition is None:
            raise DocumentNotFound(f"no edition {edition_id}", source="empty")
        articles = tuple(
            s.article
            for s in sorted(self._seed.articles, key=_sort_key)
            if s.edition_id == edition_id and s.status == PUBLISHED
        )
        return Edition(
            id=edition.id,
            date=edition.date,
            slug=edition.slug,
            title=edition.title,
            articles=articles,
            pdf=edition.pdf,
        )

    async def list_editions(self, query: EditionQuery) -> tuple[EditionRef, ...]:
        self._require(Capability.EDITIONS)
        selected = [e for e in self._seed.editions if _edition_matches(e, query)]
        selected.sort(key=lambda e: e.date)
        return tuple(
            EditionRef(
                id=e.id,
                date=e.date,
                article_count=sum(1 for s in self._seed.articles if s.edition_id == e.id),
                pdf=e.pdf,
            )
            for e in selected
        )

    # --- assets -----------------------------------------------------------
    async def stream_asset(self, asset_id: str) -> AsyncIterator[bytes]:
        self._require(Capability.ASSETS)
        payload = self._seed.assets.get(asset_id)
        if payload is None:
            raise DocumentNotFound(f"no asset {asset_id}", source="empty")
        for start in range(0, len(payload), self._chunk_size):
            yield payload[start : start + self._chunk_size]

    # --- inbound ----------------------------------------------------------
    def parse_change(
        self,
        document_id: str,
        body: Mapping[str, Any] | None,
        *,
        received_at: datetime | None = None,
    ) -> ChangeSignal:
        self._require(Capability.CHANGE_SIGNALS)
        payload: Mapping[str, Any] = body if isinstance(body, Mapping) else {}
        return ChangeSignal(
            article_id=document_id,
            change=coerce_change_kind(payload.get("event") or payload.get("change")),
            actor=coerce_actor_kind(payload.get("source") or payload.get("actor")),
            publish_date=coerce_signal_date(payload.get("publish_date")),
            status=_optional_str(payload.get("status")),
            fingerprint=_optional_str(
                payload.get("content_fingerprint") or payload.get("fingerprint")
            ),
            received_at=received_at or datetime.now(UTC),
            raw=dict(payload),
        )

    # --- health -----------------------------------------------------------
    async def ping(self) -> None:
        if not self._healthy:
            raise CorpusUnavailable("fake corpus marked unhealthy", kind="connect")

    # --- internals --------------------------------------------------------
    def _seed_article(self, article_id: str) -> SeedArticle:
        seeded = self._by_id.get(article_id)
        if seeded is None:
            raise DocumentNotFound(f"no article {article_id}", source="empty")
        return seeded


def _to_ref(seeded: SeedArticle) -> ArticleRef:
    return ArticleRef(
        id=seeded.article.id,
        slug=seeded.article.slug,
        status=seeded.status,
        publish_date=seeded.article.publish_date,
        section=seeded.article.section,
    )


def _sort_key(seeded: SeedArticle) -> tuple[str, str]:
    return (seeded.article.publish_date, seeded.article.id)


def _is_after(key: tuple[str, str], cursor: tuple[str, str], descending: bool) -> bool:
    return key < cursor if descending else key > cursor


def _matches(seeded: SeedArticle, query: ArticleQuery) -> bool:
    article = seeded.article
    if query.ids and article.id not in query.ids:
        return False
    if query.slugs and article.slug not in query.slugs:
        return False
    if query.sections and article.section not in query.sections:
        return False
    if query.edition_id is not None and seeded.edition_id != query.edition_id:
        return False
    if query.status is not None and seeded.status != query.status:
        return False
    if query.require_publish_date and not article.publish_date:
        return False
    published = normalize_optional_date(article.publish_date)
    if query.published_from is not None and (
        published is None or published < query.published_from.isoformat()
    ):
        return False
    if query.published_to is not None and (
        published is None or published > query.published_to.isoformat()
    ):
        return False
    return True


def _edition_matches(edition: SeedEdition, query: EditionQuery) -> bool:
    if query.date_exact is not None and edition.date != query.date_exact.isoformat():
        return False
    if query.date_from is not None and edition.date < query.date_from.isoformat():
        return False
    if query.date_to is not None and edition.date > query.date_to.isoformat():
        return False
    if query.require_pdf and edition.pdf is None:
        return False
    return True


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
