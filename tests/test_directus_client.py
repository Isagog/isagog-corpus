"""DirectusCorpus over respx: the error table of §2.4 and the transport policy."""

import httpx
import pytest
import respx
from corpus.capabilities import Capability
from corpus.errors import (
    CorpusAuthError,
    CorpusConfigError,
    CorpusError,
    CorpusRateLimited,
    CorpusUnavailable,
    DocumentNotFound,
    InvalidDocument,
)
from corpus.models import Article
from corpus.query import ArticleQuery, EditionQuery
from corpus.testing.fixtures import (
    ARTICLE_1_ID,
    DEFAULT_SEED,
    EDITION_1_ID,
    PDF_ASSET_ID,
    CorpusSeed,
    SeedArticle,
)
from corpus_directus.client import DirectusCorpus
from corpus_directus.settings import Timeouts

from tests.directus_stub import DirectusStub

BASE_URL = "http://directus.test"
ARTICLE_URL = f"{BASE_URL}/items/articles/{ARTICLE_1_ID}"


@pytest.fixture
async def corpus():
    instance = DirectusCorpus(base_url=BASE_URL, api_key="test-key")
    yield instance
    await instance.aclose()


@pytest.fixture
def stub():
    return DirectusStub(DEFAULT_SEED)


@pytest.mark.unit
class TestConstruction:
    def test_requires_a_base_url_and_key(self):
        with pytest.raises(CorpusConfigError):
            DirectusCorpus(base_url="", api_key="k")
        with pytest.raises(CorpusConfigError):
            DirectusCorpus(base_url=BASE_URL, api_key="")

    def test_does_not_subclass_the_http_client(self):
        """Composition, not inheritance: the query DSL must not be reachable."""
        assert not issubclass(DirectusCorpus, httpx.AsyncClient)

    def test_is_final(self):
        assert getattr(DirectusCorpus, "__final__", False) is True

    def test_declares_the_full_house_except_the_webhook(self, corpus):
        supported = corpus.capabilities.supported
        assert Capability.RESULT_WEBHOOK not in supported
        assert supported == frozenset(Capability) - {Capability.RESULT_WEBHOOK}
        assert corpus.capabilities.id_format == "uuid"
        assert corpus.capabilities.max_ids_per_query == 100

    def test_declares_the_webhook_when_one_is_configured(self):
        corpus = DirectusCorpus(base_url=BASE_URL, api_key="k", result_webhook=True)
        assert corpus.capabilities.supports(Capability.RESULT_WEBHOOK)

    def test_the_caller_owns_retries(self):
        """Transport-level retries would double-count Temporal's attempts."""
        corpus = DirectusCorpus(base_url=BASE_URL, api_key="k")
        transport = corpus.http_client._transport
        assert isinstance(transport, httpx.AsyncHTTPTransport)
        assert transport._pool._retries == 0


@pytest.mark.integration
class TestRequests:
    async def test_get_article_sends_auth_and_an_explicit_projection(self, corpus, stub):
        with respx.mock(base_url=BASE_URL) as mock:
            mock.route().mock(side_effect=stub)
            await corpus.get_article(ARTICLE_1_ID)

        request = stub.requests[-1]
        assert request.headers["authorization"] == "Bearer test-key"
        assert "articleBody" in request.url.params["fields"]

    async def test_get_article_ref_never_asks_for_the_body(self, corpus, stub):
        with respx.mock(base_url=BASE_URL) as mock:
            mock.route().mock(side_effect=stub)
            ref = await corpus.get_article_ref(ARTICLE_1_ID)
        assert "articleBody" not in stub.requests[-1].url.params["fields"]
        assert ref.status == "published"

    async def test_list_editions_pages_until_exhausted(self, corpus, stub):
        with respx.mock(base_url=BASE_URL) as mock:
            mock.route().mock(side_effect=stub)
            editions = await corpus.list_editions(EditionQuery(), page_size=1)
        assert len(editions) == len(DEFAULT_SEED.editions)
        pages = [r.url.params.get("page") for r in stub.requests]
        assert pages.count("1") == 1 and "2" in pages

    async def test_id_sets_larger_than_the_limit_are_chunked(self, stub):
        corpus = DirectusCorpus(base_url=BASE_URL, api_key="k", max_ids_per_query=2)
        ids = tuple(s.article.id for s in DEFAULT_SEED.articles)
        with respx.mock(base_url=BASE_URL) as mock:
            mock.route().mock(side_effect=stub)
            seen = [ref.id async for ref in corpus.iter_articles(ArticleQuery(ids=ids))]
        await corpus.aclose()

        assert set(seen) == {s.article.id for s in DEFAULT_SEED.articles if s.status == "published"}
        assert len(seen) == len(set(seen))
        in_filters = [
            r.url.params["filter[id][_in]"]
            for r in stub.requests
            if "filter[id][_in]" in r.url.params
        ]
        assert all(len(f.split(",")) <= 2 for f in in_filters)

    async def test_fetch_asset_streams_under_the_asset_timeout(self, corpus, stub):
        with respx.mock(base_url=BASE_URL) as mock:
            mock.route().mock(side_effect=stub)
            payload = await corpus.fetch_asset(PDF_ASSET_ID, max_bytes=1_000_000)
        assert payload == DEFAULT_SEED.assets[PDF_ASSET_ID]

    async def test_asset_and_json_timeouts_are_separate(self):
        corpus = DirectusCorpus(
            base_url=BASE_URL, api_key="k", timeouts=Timeouts(json=3.0, asset=99.0)
        )
        assert corpus.http_client.timeout.read == 3.0
        await corpus.aclose()

    async def test_ping_probes_the_auth_endpoint(self, corpus, stub):
        with respx.mock(base_url=BASE_URL) as mock:
            mock.route().mock(side_effect=stub)
            await corpus.ping()
        assert stub.requests[-1].url.path == "/users/me"

    async def test_ping_surfaces_bad_credentials(self, corpus):
        with respx.mock(base_url=BASE_URL) as mock:
            mock.get("/users/me").mock(return_value=httpx.Response(401))
            with pytest.raises(CorpusAuthError):
                await corpus.ping()


@pytest.mark.integration
class TestErrorTable:
    """One respx scenario per row of the memaflow2 ladder (§2.4)."""

    @pytest.mark.parametrize(
        ("response", "expected", "retryable"),
        [
            (httpx.Response(200, json={"data": None}), DocumentNotFound, False),
            (httpx.Response(200, json={"data": []}), DocumentNotFound, False),
            (httpx.Response(404), DocumentNotFound, False),
            (httpx.Response(401), CorpusAuthError, False),
            # 403 on a single document is Directus hiding existence, not a
            # credentials failure — see TestForbiddenIsScopeDependent.
            (httpx.Response(403), DocumentNotFound, False),
            (httpx.Response(400), CorpusUnavailable, False),
            (httpx.Response(500), CorpusUnavailable, True),
            (httpx.Response(502), CorpusUnavailable, True),
        ],
    )
    async def test_status_codes(self, corpus, response, expected, retryable):
        with respx.mock:
            respx.get(ARTICLE_URL).mock(return_value=response)
            with pytest.raises(expected) as excinfo:
                await corpus.get_article(ARTICLE_1_ID)
        assert excinfo.value.retryable is retryable

    async def test_empty_response_is_distinguishable_from_a_404(self, corpus):
        with respx.mock:
            respx.get(ARTICLE_URL).mock(return_value=httpx.Response(200, json={"data": None}))
            with pytest.raises(DocumentNotFound) as empty:
                await corpus.get_article(ARTICLE_1_ID)
        assert empty.value.source == "empty"

        with respx.mock:
            respx.get(ARTICLE_URL).mock(return_value=httpx.Response(404))
            with pytest.raises(DocumentNotFound) as status:
                await corpus.get_article(ARTICLE_1_ID)
        assert status.value.source == "status"

    @pytest.mark.parametrize(
        ("exc", "kind"),
        [
            (httpx.ReadTimeout("slow"), "timeout"),
            (httpx.ConnectTimeout("slow"), "timeout"),
            (httpx.ConnectError("refused"), "connect"),
            (httpx.ReadError("reset"), "connect"),
        ],
    )
    async def test_transport_failures(self, corpus, exc, kind):
        with respx.mock:
            respx.get(ARTICLE_URL).mock(side_effect=exc)
            with pytest.raises(CorpusUnavailable) as excinfo:
                await corpus.get_article(ARTICLE_1_ID)
        assert excinfo.value.kind == kind
        assert excinfo.value.retryable is True

    async def test_rate_limiting_carries_retry_after(self, corpus):
        with respx.mock:
            respx.get(ARTICLE_URL).mock(
                return_value=httpx.Response(429, headers={"Retry-After": "12"})
            )
            with pytest.raises(CorpusRateLimited) as excinfo:
                await corpus.get_article(ARTICLE_1_ID)
        assert excinfo.value.retry_after == 12.0
        assert excinfo.value.retryable is True

    async def test_rate_limiting_without_a_usable_header(self, corpus):
        with respx.mock:
            respx.get(ARTICLE_URL).mock(
                return_value=httpx.Response(
                    429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
                )
            )
            with pytest.raises(CorpusRateLimited) as excinfo:
                await corpus.get_article(ARTICLE_1_ID)
        assert excinfo.value.retry_after is None

    async def test_a_malformed_row_is_an_invalid_document(self, corpus):
        with respx.mock:
            respx.get(ARTICLE_URL).mock(
                return_value=httpx.Response(200, json={"data": {"id": ARTICLE_1_ID}})
            )
            with pytest.raises(InvalidDocument) as excinfo:
                await corpus.get_article(ARTICLE_1_ID)
        assert excinfo.value.kind == "missing_field"

    async def test_a_non_json_body_is_an_invalid_document(self, corpus):
        with respx.mock:
            respx.get(ARTICLE_URL).mock(return_value=httpx.Response(200, content=b"<html>"))
            with pytest.raises(InvalidDocument):
                await corpus.get_article(ARTICLE_1_ID)

    async def test_an_oversized_asset_is_refused_mid_stream(self, corpus, stub):
        with respx.mock(base_url=BASE_URL) as mock:
            mock.route().mock(side_effect=stub)
            with pytest.raises(InvalidDocument) as excinfo:
                await corpus.fetch_asset(PDF_ASSET_ID, max_bytes=16)
        assert excinfo.value.kind == "bad_value"

    async def test_asset_failures_use_the_same_taxonomy(self, corpus):
        with respx.mock:
            respx.get(f"{BASE_URL}/assets/missing").mock(return_value=httpx.Response(404))
            with pytest.raises(DocumentNotFound):
                await corpus.fetch_asset("missing", max_bytes=100)

    async def test_no_native_exception_survives_in_the_chain(self, corpus):
        with respx.mock:
            respx.get(ARTICLE_URL).mock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(CorpusError) as excinfo:
                await corpus.get_article(ARTICLE_1_ID)
        assert excinfo.value.__cause__ is None
        assert "ConnectError" in str(excinfo.value)

    async def test_edition_failures_use_the_same_taxonomy(self, corpus):
        with respx.mock:
            respx.get(f"{BASE_URL}/items/editions/{EDITION_1_ID}").mock(
                return_value=httpx.Response(500)
            )
            with pytest.raises(CorpusUnavailable) as excinfo:
                await corpus.get_edition(EDITION_1_ID)
        assert excinfo.value.retryable is True


def _tied_seed(count: int) -> CorpusSeed:
    """Every article on the same day, so the stub stamps them all with the
    identical `datePublished`. This is not contrived: an edition's articles are
    written to this CMS within the same second."""
    return CorpusSeed(
        articles=tuple(
            SeedArticle(
                article=Article(
                    id=f"550e8400-e29b-41d4-a716-44665544{n:04d}",
                    slug=f"articolo-{n}",
                    publish_date="2024-02-01",
                    author="",
                    headline=f"Titolo {n}",
                    kicker="",
                    body="B" * 400,
                )
            )
            for n in range(1, count + 1)
        ),
        editions=(),
        assets={},
    )


@pytest.mark.integration
class TestKeysetPaginationOverTies:
    async def test_walks_a_whole_tie_group_without_skipping_or_repeating(self):
        """The regression guard for the uuid keyset defect.

        Ten articles at one instant, paged one at a time: the tiebreaker has to
        carry every id already served at that instant into the next request,
        because Directus cannot compare uuids.
        """
        seed = _tied_seed(10)
        instance = DirectusCorpus(base_url=BASE_URL, api_key="test-key")
        try:
            with respx.mock(base_url=BASE_URL) as mock:
                mock.route().mock(side_effect=DirectusStub(seed))
                walked = [ref.id async for ref in instance.iter_articles(ArticleQuery(page_size=1))]
        finally:
            await instance.aclose()

        assert len(walked) == len(set(walked)), "a row was served twice"
        assert set(walked) == {s.article.id for s in seed.articles}, "pagination skipped rows"

    async def test_the_tie_group_never_compares_uuids(self):
        """Not one request may carry an ordering operator on `id` — that is the
        400 this fix exists to remove."""
        stub = DirectusStub(_tied_seed(6))
        instance = DirectusCorpus(base_url=BASE_URL, api_key="test-key")
        try:
            with respx.mock(base_url=BASE_URL) as mock:
                mock.route().mock(side_effect=stub)
                _ = [ref.id async for ref in instance.iter_articles(ArticleQuery(page_size=2))]
        finally:
            await instance.aclose()

        offending = [
            key
            for request in stub.requests
            for key in request.url.params
            if "[id][" in key and any(op in key for op in ("_lt", "_lte", "_gt", "_gte"))
        ]
        assert not offending, offending

    async def test_the_tie_group_resets_when_the_instant_moves(self):
        """Otherwise the exclusion set would grow for the whole walk instead of
        staying as small as one timestamp's worth of articles."""
        stub = DirectusStub(DEFAULT_SEED)
        instance = DirectusCorpus(base_url=BASE_URL, api_key="test-key")
        try:
            with respx.mock(base_url=BASE_URL) as mock:
                mock.route().mock(side_effect=stub)
                _ = [ref.id async for ref in instance.iter_articles(ArticleQuery(page_size=1))]
        finally:
            await instance.aclose()

        widest = max(
            len(request.url.params.get("filter[_or][1][_and][1][id][_nin]", "").split(","))
            for request in stub.requests
        )
        assert widest <= 2, "the exclusion set outgrew the largest tie group in the seed"

    async def test_a_tie_group_wider_than_the_url_budget_fails_loudly(self):
        """Truncating it would silently skip or repeat rows — the precise defect
        keyset paging exists to prevent."""
        instance = DirectusCorpus(base_url=BASE_URL, api_key="test-key", max_ids_per_query=2)
        try:
            with respx.mock(base_url=BASE_URL) as mock:
                mock.route().mock(side_effect=DirectusStub(_tied_seed(6)))
                with pytest.raises(CorpusUnavailable) as excinfo:
                    _ = [ref.id async for ref in instance.iter_articles(ArticleQuery(page_size=1))]
        finally:
            await instance.aclose()
        assert excinfo.value.retryable is False
        assert "share the timestamp" in str(excinfo.value)


@pytest.mark.integration
class TestForbiddenIsScopeDependent:
    """Directus answers 403 FORBIDDEN for a document that does not exist, one
    the token may not read, and a collection it may not read at all — the same
    body every time. Only 401 means the credentials were rejected. The
    request's shape is the only thing that makes the 403 interpretable."""

    async def test_a_forbidden_document_is_not_found(self, corpus):
        with respx.mock:
            respx.get(ARTICLE_URL).mock(return_value=httpx.Response(403))
            with pytest.raises(DocumentNotFound) as excinfo:
                await corpus.get_article(ARTICLE_1_ID)
        assert excinfo.value.source == "status"

    async def test_a_forbidden_asset_is_not_found(self, corpus):
        with respx.mock:
            respx.get(f"{BASE_URL}/assets/{PDF_ASSET_ID}").mock(return_value=httpx.Response(403))
            with pytest.raises(DocumentNotFound):
                await corpus.fetch_asset(PDF_ASSET_ID, max_bytes=1_000)

    async def test_a_forbidden_listing_stays_an_auth_failure(self, corpus):
        """Degrading this to an empty page would let a pipeline process nothing
        and report success — the failure mode a misconfigured collection
        permission actually produces."""
        with respx.mock:
            respx.get(f"{BASE_URL}/items/articles").mock(return_value=httpx.Response(403))
            with pytest.raises(CorpusAuthError):
                await corpus.search_articles(ArticleQuery(page_size=5))

    async def test_a_forbidden_health_probe_stays_an_auth_failure(self, corpus):
        with respx.mock:
            respx.get(f"{BASE_URL}/users/me").mock(return_value=httpx.Response(403))
            with pytest.raises(CorpusAuthError):
                await corpus.ping()

    async def test_rejected_credentials_are_still_an_auth_failure(self, corpus):
        """401 is unambiguous, and must not be softened by the 403 rule."""
        with respx.mock:
            respx.get(ARTICLE_URL).mock(return_value=httpx.Response(401))
            with pytest.raises(CorpusAuthError):
                await corpus.get_article(ARTICLE_1_ID)


@pytest.mark.integration
class TestEditionListingIsBounded:
    async def test_a_walk_that_never_finishes_raises_instead_of_truncating(self, corpus, stub):
        """Returning the rows collected so far would be a short listing that
        looks complete — the defect the exhaustive walk exists to prevent."""
        with respx.mock(base_url=BASE_URL) as mock:
            mock.route().mock(side_effect=stub)
            with pytest.raises(CorpusUnavailable) as excinfo:
                await corpus.list_editions(EditionQuery(), page_size=1, max_pages=2)
        assert excinfo.value.retryable is False
        assert "date_from" in str(excinfo.value)

    async def test_a_walk_that_finishes_is_unaffected(self, corpus, stub):
        with respx.mock(base_url=BASE_URL) as mock:
            mock.route().mock(side_effect=stub)
            editions = await corpus.list_editions(EditionQuery(), page_size=1, max_pages=50)
        assert len(editions) == len(DEFAULT_SEED.editions)
