"""The ABC's default behaviour: the minimum a corpus must implement, and what
a backend that implements only that minimum does when asked for more."""

from collections.abc import AsyncIterator

import pytest
from corpus.base import Corpus
from corpus.capabilities import Capability, CorpusCapabilities
from corpus.errors import CapabilityNotSupported
from corpus.models import Article, ArticlePage, ArticleRef
from corpus.query import ArticleQuery, EditionQuery

MINIMAL = CorpusCapabilities(supported=frozenset({Capability.ARTICLES, Capability.ARTICLE_LISTING}))


class MinimalCorpus(Corpus):
    """A web-native archive: articles and listings, nothing else."""

    def __init__(self, pages: list[ArticlePage]) -> None:
        self._pages = pages

    @property
    def capabilities(self) -> CorpusCapabilities:
        return MINIMAL

    async def get_article(self, article_id: str) -> Article:
        return Article(
            id=article_id,
            slug="s",
            publish_date="2024-01-15",
            author="",
            headline="H",
            kicker="",
            body="B",
        )

    async def get_article_ref(self, article_id: str) -> ArticleRef:
        return ArticleRef(id=article_id)

    async def search_articles(self, query: ArticleQuery, cursor: str | None = None) -> ArticlePage:
        index = int(cursor or 0)
        return self._pages[index]

    async def ping(self) -> None:
        return None


@pytest.fixture
def corpus() -> MinimalCorpus:
    return MinimalCorpus(
        [
            ArticlePage(items=(ArticleRef(id="a"),), next_cursor="1"),
            ArticlePage(items=(ArticleRef(id="b"),), next_cursor=None),
        ]
    )


@pytest.mark.unit
class TestOptionalSurface:
    async def test_editions_are_refused_by_default(self, corpus):
        with pytest.raises(CapabilityNotSupported):
            await corpus.get_edition("e1")
        with pytest.raises(CapabilityNotSupported):
            await corpus.list_editions(EditionQuery())

    async def test_edition_covers_are_refused_by_default(self, corpus):
        """A web-native archive has no front page. Asking for one names the
        gap instead of returning an empty cover."""
        with pytest.raises(CapabilityNotSupported):
            await corpus.get_edition_cover("e1")

    async def test_assets_are_refused_by_default(self, corpus):
        with pytest.raises(CapabilityNotSupported):
            corpus.stream_asset("f1")
        with pytest.raises(CapabilityNotSupported):
            await corpus.fetch_asset("f1", max_bytes=10)

    def test_change_signals_are_refused_by_default(self, corpus):
        with pytest.raises(CapabilityNotSupported):
            corpus.parse_change("a1", {})

    async def test_aclose_is_a_no_op(self, corpus):
        await corpus.aclose()


@pytest.mark.unit
class TestTemplateMethods:
    async def test_iter_articles_drains_every_page(self, corpus):
        assert [ref.id async for ref in corpus.iter_articles(ArticleQuery())] == ["a", "b"]

    async def test_fetch_asset_buffers_a_streamed_asset(self):
        class Streaming(MinimalCorpus):
            @property
            def capabilities(self) -> CorpusCapabilities:
                return CorpusCapabilities(supported=frozenset(Capability))

            async def stream_asset(self, asset_id: str) -> AsyncIterator[bytes]:
                for chunk in (b"abc", b"def"):
                    yield chunk

        corpus = Streaming([])
        assert await corpus.fetch_asset("f1", max_bytes=10) == b"abcdef"

    async def test_the_mandatory_surface_is_enough_to_instantiate(self, corpus):
        await corpus.ping()
        assert (await corpus.get_article("a")).id == "a"
        assert corpus.capabilities.supports(Capability.ARTICLES)
