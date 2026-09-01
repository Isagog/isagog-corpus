"""FakeCorpus is the port's reference implementation — and the first adapter."""

from datetime import date, datetime

import pytest
from corpus.capabilities import Capability, CorpusCapabilities
from corpus.errors import (
    CapabilityNotSupported,
    CorpusUnavailable,
    DocumentNotFound,
    InvalidDocument,
)
from corpus.models import Article, ArticleRef, Edition, EditionRef
from corpus.query import ArticleOrder, ArticleQuery, EditionQuery
from corpus.signals import ChangeKind, ChangeSignal
from corpus.testing.fake import FakeCorpus
from corpus.testing.fixtures import (
    ARTICLE_1_ID,
    DEFAULT_SEED,
    EDITION_1_ID,
    EDITION_2_ID,
    EDITION_3_ID,
    PDF_ASSET_ID,
)


@pytest.fixture
def corpus() -> FakeCorpus:
    return FakeCorpus.from_seed(DEFAULT_SEED)


def _ids(refs) -> list[str]:
    return [ref.id for ref in refs]


@pytest.mark.unit
class TestGetArticle:
    async def test_returns_a_hydrated_article(self, corpus):
        seeded = DEFAULT_SEED.articles[0].article
        article = await corpus.get_article(seeded.id)
        assert isinstance(article, Article)
        assert article == seeded

    async def test_unknown_id_raises_document_not_found(self, corpus):
        with pytest.raises(DocumentNotFound):
            await corpus.get_article("no-such-article")

    async def test_ref_carries_the_status(self, corpus):
        ref = await corpus.get_article_ref(DEFAULT_SEED.articles[0].article.id)
        assert isinstance(ref, ArticleRef)
        assert ref.status == "published"

    async def test_ref_of_unknown_id_raises(self, corpus):
        with pytest.raises(DocumentNotFound):
            await corpus.get_article_ref("nope")


@pytest.mark.unit
class TestSearchArticles:
    async def test_defaults_to_published_only(self, corpus):
        page = await corpus.search_articles(ArticleQuery())
        statuses = {ref.status for ref in page.items}
        assert statuses == {"published"}

    async def test_status_none_returns_drafts_too(self, corpus):
        page = await corpus.search_articles(ArticleQuery(status=None))
        assert "draft" in {ref.status for ref in page.items}

    async def test_filters_by_ids(self, corpus):
        wanted = [seed.article.id for seed in DEFAULT_SEED.articles[:2]]
        page = await corpus.search_articles(ArticleQuery(ids=tuple(wanted), status=None))
        assert sorted(_ids(page.items)) == sorted(wanted)

    async def test_filters_by_slugs(self, corpus):
        slug = DEFAULT_SEED.articles[0].article.slug
        page = await corpus.search_articles(ArticleQuery(slugs=(slug,)))
        assert _ids(page.items) == [DEFAULT_SEED.articles[0].article.id]

    async def test_filters_by_section(self, corpus):
        page = await corpus.search_articles(ArticleQuery(sections=("Cultura",)))
        assert all(ref.section == "Cultura" for ref in page.items)
        assert page.items

    async def test_filters_by_date_range(self, corpus):
        page = await corpus.search_articles(
            ArticleQuery(published_from=date(2024, 1, 16), published_to=date(2024, 1, 16))
        )
        assert {ref.publish_date for ref in page.items} == {"2024-01-16"}

    async def test_filters_by_edition(self, corpus):
        page = await corpus.search_articles(ArticleQuery(edition_id=EDITION_1_ID))
        assert len(page.items) == 2

    async def test_orders_descending_by_default(self, corpus):
        page = await corpus.search_articles(ArticleQuery())
        dates = [ref.publish_date for ref in page.items]
        assert dates == sorted(dates, reverse=True)

    async def test_orders_ascending_on_request(self, corpus):
        page = await corpus.search_articles(ArticleQuery(order=ArticleOrder.PUBLISH_DATE_ASC))
        dates = [ref.publish_date for ref in page.items]
        assert dates == sorted(dates)

    async def test_pagination_is_exhaustive_and_ordered(self, corpus):
        collected: list[str] = []
        cursor = None
        pages = 0
        while True:
            page = await corpus.search_articles(ArticleQuery(page_size=1), cursor)
            collected.extend(_ids(page.items))
            pages += 1
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        assert pages > 1
        assert len(collected) == len(set(collected))
        full = await corpus.search_articles(ArticleQuery(page_size=100))
        assert collected == _ids(full.items)

    async def test_last_page_has_no_cursor(self, corpus):
        page = await corpus.search_articles(ArticleQuery(page_size=100))
        assert page.next_cursor is None

    async def test_garbage_cursor_is_rejected(self, corpus):
        with pytest.raises(InvalidDocument):
            await corpus.search_articles(ArticleQuery(), "not-a-cursor")

    async def test_iter_articles_drains_every_page(self, corpus):
        seen = [ref.id async for ref in corpus.iter_articles(ArticleQuery(page_size=1))]
        full = await corpus.search_articles(ArticleQuery(page_size=100))
        assert seen == _ids(full.items)


@pytest.mark.unit
class TestEditions:
    async def test_get_edition_returns_published_articles_only(self, corpus):
        edition = await corpus.get_edition(EDITION_3_ID)
        assert isinstance(edition, Edition)
        assert all(isinstance(a, Article) for a in edition.articles)
        assert len(edition.articles) == 1  # the draft is filtered out

    async def test_get_edition_exposes_the_pdf(self, corpus):
        edition = await corpus.get_edition(EDITION_1_ID)
        assert edition.pdf is not None
        assert edition.pdf.id == PDF_ASSET_ID

    async def test_unknown_edition_raises(self, corpus):
        with pytest.raises(DocumentNotFound):
            await corpus.get_edition("nope")

    async def test_list_editions_by_range(self, corpus):
        editions = await corpus.list_editions(
            EditionQuery(date_from=date(2024, 1, 15), date_to=date(2024, 1, 16))
        )
        assert [e.id for e in editions] == [EDITION_1_ID, EDITION_2_ID]
        assert all(isinstance(e, EditionRef) for e in editions)

    async def test_list_editions_by_exact_date(self, corpus):
        editions = await corpus.list_editions(EditionQuery(date_exact=date(2024, 1, 16)))
        assert [e.id for e in editions] == [EDITION_2_ID]

    async def test_list_editions_can_require_a_pdf(self, corpus):
        editions = await corpus.list_editions(EditionQuery(require_pdf=True))
        assert EDITION_2_ID not in [e.id for e in editions]
        assert all(e.pdf is not None for e in editions)

    async def test_edition_refs_carry_article_counts(self, corpus):
        editions = await corpus.list_editions(EditionQuery(date_exact=date(2024, 1, 15)))
        assert editions[0].article_count == 2


@pytest.mark.unit
class TestAssets:
    async def test_stream_asset_yields_the_bytes(self, corpus):
        chunks = [chunk async for chunk in corpus.stream_asset(PDF_ASSET_ID)]
        assert len(chunks) > 1
        assert b"".join(chunks) == DEFAULT_SEED.assets[PDF_ASSET_ID]

    async def test_fetch_asset_buffers_the_stream(self, corpus):
        payload = await corpus.fetch_asset(PDF_ASSET_ID, max_bytes=10_000_000)
        assert payload == DEFAULT_SEED.assets[PDF_ASSET_ID]

    async def test_fetch_asset_enforces_max_bytes(self, corpus):
        with pytest.raises(InvalidDocument) as excinfo:
            await corpus.fetch_asset(PDF_ASSET_ID, max_bytes=8)
        assert excinfo.value.kind == "bad_value"

    async def test_unknown_asset_raises(self, corpus):
        with pytest.raises(DocumentNotFound):
            await corpus.fetch_asset("no-file", max_bytes=1000)


@pytest.mark.unit
class TestCapabilityGating:
    def test_declares_the_full_house_by_default(self, corpus):
        assert corpus.capabilities.supported == frozenset(Capability)

    async def test_a_web_native_corpus_without_editions_says_so(self):
        articles_only = FakeCorpus.from_seed(
            DEFAULT_SEED,
            capabilities=CorpusCapabilities(
                supported=frozenset({Capability.ARTICLES, Capability.ARTICLE_LISTING})
            ),
        )
        with pytest.raises(CapabilityNotSupported):
            await articles_only.get_edition(EDITION_1_ID)
        with pytest.raises(CapabilityNotSupported):
            await articles_only.list_editions(EditionQuery())
        with pytest.raises(CapabilityNotSupported):
            await articles_only.fetch_asset(PDF_ASSET_ID, max_bytes=100)

    async def test_ping_and_close(self, corpus):
        await corpus.ping()
        await corpus.aclose()

    async def test_ping_can_be_made_to_fail(self):
        broken = FakeCorpus.from_seed(DEFAULT_SEED, healthy=False)
        with pytest.raises(CorpusUnavailable) as excinfo:
            await broken.ping()
        assert excinfo.value.retryable is True


@pytest.mark.unit
class TestChangeSignals:
    def test_parses_a_body(self, corpus):
        signal = corpus.parse_change("a1", {"event": "create", "source": "editor"})
        assert isinstance(signal, ChangeSignal)
        assert signal.change is ChangeKind.CREATE
        assert isinstance(signal.received_at, datetime)

    def test_a_body_less_request_still_parses(self, corpus):
        signal = corpus.parse_change("a1", None)
        assert signal.change is ChangeKind.UNKNOWN
        assert signal.article_id == "a1"


@pytest.mark.unit
class TestFakeEditionCover:
    async def test_returns_the_seeded_front_page(self, corpus):
        cover = await corpus.get_edition_cover(EDITION_1_ID)
        assert cover.headline == "Prima pagina del 15 gennaio"
        assert cover.article_id == ARTICLE_1_ID

    async def test_an_edition_without_a_cover_is_not_found(self, corpus):
        with pytest.raises(DocumentNotFound):
            await corpus.get_edition_cover(EDITION_2_ID)

    async def test_an_undeclared_capability_is_refused(self):
        corpus = FakeCorpus.from_seed(
            DEFAULT_SEED,
            capabilities=CorpusCapabilities(
                supported=frozenset(Capability) - {Capability.EDITION_COVER}
            ),
        )
        with pytest.raises(CapabilityNotSupported):
            await corpus.get_edition_cover(EDITION_1_ID)
