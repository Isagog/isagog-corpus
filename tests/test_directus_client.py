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
from corpus.query import ArticleQuery, EditionQuery
from corpus.testing.fixtures import ARTICLE_1_ID, DEFAULT_SEED, EDITION_1_ID, PDF_ASSET_ID
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
            (httpx.Response(403), CorpusAuthError, False),
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
