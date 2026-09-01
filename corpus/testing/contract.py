"""The executable adapter specification.

Passing this suite IS the definition of "is a Corpus". Subclass it once per
adapter, provide a `corpus` seeded from `seed`, and the definition of done for
a new CMS becomes a checklist instead of a research project.

    class TestMyCorpusContract(CorpusContractSuite):
        @pytest.fixture
        def seed(self): return DEFAULT_SEED

        @pytest.fixture
        def corpus(self, seed): return MyCorpus(...)

Capability-gated tests skip themselves when the backend does not declare the
capability — a corpus without editions is not a failing corpus.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import date

import pytest

from corpus.base import Corpus
from corpus.capabilities import Capability, CorpusRequirements
from corpus.errors import CapabilityNotSupported, CorpusError, DocumentNotFound, InvalidDocument
from corpus.models import (
    PUBLISHED,
    Article,
    ArticlePage,
    ArticleRef,
    Edition,
    EditionCover,
    EditionRef,
)
from corpus.query import ArticleOrder, ArticleQuery, EditionQuery
from corpus.signals import ChangeSignal
from corpus.testing.fixtures import CorpusSeed

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: A raw transport or validation exception anywhere in the raised chain is a
#: contract failure: the taxonomy is the only vocabulary that may escape.
_FOREIGN_MODULES = frozenset({"httpx", "httpcore", "requests", "urllib3", "aiohttp", "pydantic"})

#: Capabilities that gate a whole method. The remaining ones (ARTICLE_BY_SLUG,
#: SECTIONS, DATE_FILTER, EDITION_PDF) declare query axes rather than methods,
#: so they are covered by the honesty test instead of the refusal test.
_METHOD_CAPABILITIES = (
    Capability.ARTICLES,
    Capability.ARTICLE_LISTING,
    Capability.EDITIONS,
    Capability.EDITION_COVER,
    Capability.ASSETS,
    Capability.CHANGE_SIGNALS,
)


def _published(seed: CorpusSeed):
    return [s for s in seed.articles if s.status == PUBLISHED]


def _edition_with_pdf(seed: CorpusSeed):
    return next(e for e in seed.editions if e.pdf is not None)


def _edition_with_cover(seed: CorpusSeed):
    return next(e for e in seed.editions if e.cover is not None)


def _edition_without_cover(seed: CorpusSeed):
    return next((e for e in seed.editions if e.cover is None), None)


def _chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


class CorpusContractSuite:
    """Subclass per adapter; provide `corpus` (seeded from `seed`) as fixtures."""

    # --- articles ---------------------------------------------------------
    async def test_get_article_returns_normalised_article(self, corpus: Corpus, seed):
        expected = _published(seed)[0].article
        article = await corpus.get_article(expected.id)

        assert isinstance(article, Article)
        assert article.id == expected.id
        assert article.slug == expected.slug
        assert _ISO_DAY.match(article.publish_date), "publish_date must be YYYY-MM-DD"
        assert "<" not in article.headline and "<" not in article.body
        assert isinstance(article.author, str) and isinstance(article.kicker, str)

    async def test_absent_optional_text_is_folded_to_empty_string(self, corpus: Corpus, seed):
        empties = [s for s in seed.articles if s.article.author == "" or s.article.kicker == ""]
        if not empties:
            pytest.skip("seed has no article with an absent author/kicker")
        article = await corpus.get_article(empties[0].article.id)
        assert article.author == empties[0].article.author
        assert article.kicker == empties[0].article.kicker

    async def test_get_article_unknown_id_raises_not_found(self, corpus: Corpus):
        with pytest.raises(DocumentNotFound):
            await corpus.get_article("00000000-0000-4000-8000-000000000000")

    async def test_get_article_ref_is_a_cheap_projection(self, corpus: Corpus, seed):
        seeded = _published(seed)[0]
        ref = await corpus.get_article_ref(seeded.article.id)
        assert isinstance(ref, ArticleRef)
        assert ref.id == seeded.article.id
        assert ref.status == seeded.status

    # --- listings ---------------------------------------------------------
    async def test_search_returns_published_refs_by_default(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.ARTICLE_LISTING)
        page = await corpus.search_articles(ArticleQuery(page_size=50))
        assert isinstance(page, ArticlePage)
        assert page.items
        assert {ref.status for ref in page.items} == {PUBLISHED}

    async def test_search_pagination_is_exhaustive_and_duplicate_free(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.ARTICLE_LISTING)
        collected: list[str] = []
        cursor: str | None = None
        for _ in range(50):  # guard against a cursor that never terminates
            page = await corpus.search_articles(ArticleQuery(page_size=1), cursor)
            collected.extend(ref.id for ref in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        else:
            pytest.fail("pagination did not terminate")

        assert len(collected) == len(set(collected)), "a row was served twice"
        expected = {s.article.id for s in _published(seed)}
        assert set(collected) == expected, "pagination skipped rows"

    async def test_pagination_preserves_the_requested_order(self, corpus: Corpus):
        self._skip_unless(corpus, Capability.ARTICLE_LISTING)
        for order, reverse in (
            (ArticleOrder.PUBLISH_DATE_DESC, True),
            (ArticleOrder.PUBLISH_DATE_ASC, False),
        ):
            dates = [
                ref.publish_date or ""
                async for ref in corpus.iter_articles(ArticleQuery(order=order, page_size=2))
            ]
            assert dates == sorted(dates, reverse=reverse)

    async def test_iter_articles_equals_drained_search(self, corpus: Corpus):
        self._skip_unless(corpus, Capability.ARTICLE_LISTING)
        streamed = [ref.id async for ref in corpus.iter_articles(ArticleQuery(page_size=1))]
        single = await corpus.search_articles(ArticleQuery(page_size=100))
        assert streamed == [ref.id for ref in single.items]

    async def test_a_garbage_cursor_is_rejected_as_invalid(self, corpus: Corpus):
        self._skip_unless(corpus, Capability.ARTICLE_LISTING)
        with pytest.raises(InvalidDocument):
            await corpus.search_articles(ArticleQuery(), "not-a-cursor")

    async def test_search_by_slug(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.ARTICLE_BY_SLUG)
        seeded = _published(seed)[0].article
        page = await corpus.search_articles(ArticleQuery(slugs=(seeded.slug,)))
        assert [ref.id for ref in page.items] == [seeded.id]

    async def test_search_by_section(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.SECTIONS)
        section = _published(seed)[0].article.section
        if section is None:
            pytest.skip("seed carries no section")
        page = await corpus.search_articles(ArticleQuery(sections=(section,)))
        assert page.items
        assert all(ref.section == section for ref in page.items)

    async def test_search_by_date_range(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.DATE_FILTER)
        day = _published(seed)[0].article.publish_date
        target = date.fromisoformat(day)
        page = await corpus.search_articles(
            ArticleQuery(published_from=target, published_to=target, page_size=50)
        )
        assert page.items
        assert {ref.publish_date for ref in page.items} == {day}

    async def test_search_by_ids_respects_the_declared_chunk_limit(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.ARTICLE_LISTING)
        wanted = tuple(s.article.id for s in _published(seed))
        page = await corpus.search_articles(ArticleQuery(ids=wanted, page_size=100))
        assert {ref.id for ref in page.items} == set(wanted)

    # --- editions ---------------------------------------------------------
    async def test_get_edition_hydrates_published_articles_only(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.EDITIONS)
        drafts = {s.article.id for s in seed.articles if s.status != PUBLISHED}
        for seeded in seed.editions:
            edition = await corpus.get_edition(seeded.id)
            assert isinstance(edition, Edition)
            assert edition.id == seeded.id
            assert _ISO_DAY.match(edition.date)
            assert all(isinstance(a, Article) for a in edition.articles)
            assert not (drafts & {a.id for a in edition.articles})

    async def test_get_edition_unknown_id_raises_not_found(self, corpus: Corpus):
        self._skip_unless(corpus, Capability.EDITIONS)
        with pytest.raises(DocumentNotFound):
            await corpus.get_edition("00000000-0000-4000-8000-00000000ffff")

    async def test_list_editions_filters_by_date(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.EDITIONS)
        target = date.fromisoformat(seed.editions[0].date)
        editions = await corpus.list_editions(EditionQuery(date_exact=target))
        assert editions
        assert all(isinstance(e, EditionRef) for e in editions)
        assert {e.date for e in editions} == {seed.editions[0].date}

    async def test_edition_pdf_is_exposed(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.EDITION_PDF)
        seeded = _edition_with_pdf(seed)
        edition = await corpus.get_edition(seeded.id)
        assert edition.pdf is not None
        assert edition.pdf.id == seeded.pdf.id  # type: ignore[union-attr]

    async def test_list_editions_can_require_a_pdf(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.EDITION_PDF)
        editions = await corpus.list_editions(EditionQuery(require_pdf=True))
        assert editions
        assert all(e.pdf is not None for e in editions)

    # --- edition covers ---------------------------------------------------
    async def test_get_edition_cover_returns_the_front_page(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.EDITION_COVER)
        for seeded in (e for e in seed.editions if e.cover is not None):
            cover = await corpus.get_edition_cover(seeded.id)
            expected = seeded.cover
            assert isinstance(cover, EditionCover)
            assert cover.headline == expected.headline  # type: ignore[union-attr]
            assert cover.kicker == expected.kicker  # type: ignore[union-attr]
            assert cover.article_id == expected.article_id  # type: ignore[union-attr]
            assert "<" not in cover.headline and "<" not in cover.kicker

    async def test_cover_headline_is_the_display_headline(self, corpus: Corpus, seed):
        """Not the cover story's own headline.

        The two differ on most real editions, so an adapter that maps the
        article headline onto the cover looks correct until it reaches
        production. The seed keeps them different on purpose."""
        self._skip_unless(corpus, Capability.EDITION_COVER)
        seeded = _edition_with_cover(seed)
        article_id = seeded.cover.article_id  # type: ignore[union-attr]
        if article_id is None:
            pytest.skip("seeded cover is not linked to an article")
        story = next(s for s in seed.articles if s.article.id == article_id).article
        assert seeded.cover.headline != story.headline, "seed no longer proves the distinction"  # type: ignore[union-attr]

        cover = await corpus.get_edition_cover(seeded.id)
        assert cover.headline == seeded.cover.headline  # type: ignore[union-attr]

    async def test_cover_image_is_a_populated_asset_ref(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.EDITION_COVER)
        withimage = [e for e in seed.editions if e.cover is not None and e.cover.image is not None]
        if not withimage:
            pytest.skip("seed carries no cover image")
        seeded = withimage[0]
        cover = await corpus.get_edition_cover(seeded.id)
        expected = seeded.cover.image  # type: ignore[union-attr]
        assert cover.image is not None
        assert cover.image.id == expected.id  # type: ignore[union-attr]
        # mime and size come free from the backend's own file record; a caller
        # deriving a file extension must not have to fetch the bytes first.
        assert cover.image.mime == expected.mime  # type: ignore[union-attr]
        assert cover.image.size == expected.size  # type: ignore[union-attr]

    async def test_a_cover_without_an_image_is_still_a_cover(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.EDITION_COVER)
        without = [e for e in seed.editions if e.cover is not None and e.cover.image is None]
        if not without:
            pytest.skip("seed carries no image-less cover")
        cover = await corpus.get_edition_cover(without[0].id)
        assert cover.image is None
        assert cover.headline

    async def test_the_cover_image_is_fetchable(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.EDITION_COVER)
        self._skip_unless(corpus, Capability.ASSETS)
        seeded = _edition_with_cover(seed)
        cover = await corpus.get_edition_cover(seeded.id)
        if cover.image is None:
            pytest.skip("seeded cover has no image")
        payload = await corpus.fetch_asset(cover.image.id, max_bytes=50_000_000)
        assert payload == seed.assets[cover.image.id]

    async def test_an_edition_without_a_cover_raises_not_found(self, corpus: Corpus, seed):
        """Distinct from CapabilityNotSupported: this backend does covers, this
        edition has none."""
        self._skip_unless(corpus, Capability.EDITION_COVER)
        seeded = _edition_without_cover(seed)
        if seeded is None:
            pytest.skip("every seeded edition has a cover")
        with pytest.raises(DocumentNotFound):
            await corpus.get_edition_cover(seeded.id)

    async def test_get_edition_cover_unknown_edition_raises_not_found(self, corpus: Corpus):
        self._skip_unless(corpus, Capability.EDITION_COVER)
        with pytest.raises(DocumentNotFound):
            await corpus.get_edition_cover("00000000-0000-4000-8000-00000000fffe")

    # --- assets -----------------------------------------------------------
    async def test_fetch_asset_returns_the_bytes(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.ASSETS)
        asset_id = _edition_with_pdf(seed).pdf.id  # type: ignore[union-attr]
        payload = await corpus.fetch_asset(asset_id, max_bytes=50_000_000)
        assert payload == seed.assets[asset_id]

    async def test_fetch_asset_enforces_max_bytes(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.ASSETS)
        asset_id = _edition_with_pdf(seed).pdf.id  # type: ignore[union-attr]
        with pytest.raises(InvalidDocument) as excinfo:
            await corpus.fetch_asset(asset_id, max_bytes=4)
        assert excinfo.value.kind == "bad_value"

    async def test_unknown_asset_raises_not_found(self, corpus: Corpus):
        self._skip_unless(corpus, Capability.ASSETS)
        with pytest.raises(DocumentNotFound):
            await corpus.fetch_asset("00000000-0000-4000-8000-0000000000aa", max_bytes=1_000)

    async def test_stream_asset_yields_chunks(self, corpus: Corpus, seed):
        self._skip_unless(corpus, Capability.ASSET_STREAMING)
        asset_id = _edition_with_pdf(seed).pdf.id  # type: ignore[union-attr]
        chunks = [chunk async for chunk in corpus.stream_asset(asset_id)]
        assert all(isinstance(chunk, bytes) for chunk in chunks)
        assert b"".join(chunks) == seed.assets[asset_id]

    # --- inbound evidence -------------------------------------------------
    async def test_change_parse_never_raises(self, corpus: Corpus):
        self._skip_unless(corpus, Capability.CHANGE_SIGNALS)
        bodies = [
            None,
            {},
            {"event": "create", "source": "editor", "status": "published"},
            {"event": "items.unheard-of", "source": "robot"},
            {"publish_date": "not-a-date"},
            {"unexpected": {"nested": ["shapes"]}},
            {"event": 17, "source": None, "status": []},
        ]
        for body in bodies:
            signal = corpus.parse_change("doc-1", body)  # type: ignore[arg-type]
            assert isinstance(signal, ChangeSignal)
            assert signal.article_id == "doc-1"

    async def test_change_signal_is_evidence_not_verdict(self, corpus: Corpus):
        self._skip_unless(corpus, Capability.CHANGE_SIGNALS)
        signal = corpus.parse_change("doc-1", {"event": "who-knows"})
        assert signal.change.value == "unknown"
        assert signal.actor.value == "unknown"
        assert not hasattr(signal, "priority") and not hasattr(signal, "lane")

    # --- taxonomy and capability honesty ----------------------------------
    async def test_native_errors_never_escape(self, corpus: Corpus):
        """Whatever we throw at the port, only the taxonomy comes back."""
        probes: list[Callable[[], Awaitable[object]]] = [
            lambda: corpus.get_article(""),
            lambda: corpus.get_article("not-a-uuid"),
            lambda: corpus.get_article_ref("../../etc/passwd"),
            lambda: corpus.search_articles(ArticleQuery(), "%%%"),
            lambda: corpus.get_edition("not-a-uuid"),
            lambda: corpus.get_edition_cover("not-a-uuid"),
            lambda: corpus.get_edition_cover(""),
            lambda: corpus.fetch_asset("not-a-uuid", max_bytes=10),
        ]
        for probe in probes:
            try:
                await probe()
            except CorpusError as err:
                foreign = [
                    type(e).__module__.split(".")[0]
                    for e in _chain(err)
                    if type(e).__module__.split(".")[0] in _FOREIGN_MODULES
                ]
                assert not foreign, f"native exception leaked through the port: {foreign}"
            except Exception as err:  # noqa: BLE001
                pytest.fail(f"non-corpus exception escaped: {type(err).__name__}: {err}")

    async def test_unsupported_capability_raises_capability_error(self, corpus: Corpus, seed):
        undeclared = [c for c in _METHOD_CAPABILITIES if not corpus.capabilities.supports(c)]
        if not undeclared:
            CorpusRequirements(required=frozenset(_METHOD_CAPABILITIES)).check(corpus.capabilities)
            pytest.skip("backend declares every method capability")
        for capability in undeclared:
            with pytest.raises(CapabilityNotSupported):
                await self._exercise(corpus, seed, capability)

    async def test_capabilities_are_honest(self, corpus: Corpus, seed):
        """Every declared capability actually works against the seeded data."""
        for capability in _METHOD_CAPABILITIES:
            if corpus.capabilities.supports(capability):
                await self._exercise(corpus, seed, capability)

    async def test_requirements_check_passes_for_declared_capabilities(self, corpus: Corpus):
        CorpusRequirements(required=corpus.capabilities.supported).check(corpus.capabilities)

    async def test_articles_capability_is_mandatory(self, corpus: Corpus):
        assert corpus.capabilities.supports(Capability.ARTICLES)

    async def test_ping_reports_health(self, corpus: Corpus):
        await corpus.ping()

    # --- helpers ----------------------------------------------------------
    @staticmethod
    def _skip_unless(corpus: Corpus, capability: Capability) -> None:
        if not corpus.capabilities.supports(capability):
            pytest.skip(f"backend does not declare {capability.value}")

    @staticmethod
    async def _exercise(corpus: Corpus, seed: CorpusSeed, capability: Capability) -> None:
        match capability:
            case Capability.ARTICLES:
                await corpus.get_article(_published(seed)[0].article.id)
            case Capability.ARTICLE_LISTING:
                await corpus.search_articles(ArticleQuery(page_size=2))
            case Capability.EDITIONS:
                await corpus.get_edition(seed.editions[0].id)
            case Capability.EDITION_COVER:
                await corpus.get_edition_cover(_edition_with_cover(seed).id)
            case Capability.ASSETS:
                asset_id = _edition_with_pdf(seed).pdf.id  # type: ignore[union-attr]
                await corpus.fetch_asset(asset_id, max_bytes=50_000_000)
            case Capability.CHANGE_SIGNALS:
                corpus.parse_change("doc-1", {})
            case _:  # pragma: no cover - query-axis capabilities have no method
                raise AssertionError(f"no method exercises {capability}")
