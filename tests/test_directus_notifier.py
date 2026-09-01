"""The outbound Directus Flow notifier: its own auth, pool and envelope."""

import httpx
import pytest
import respx
from corpus.errors import CorpusAuthError, CorpusUnavailable
from corpus.notify import PublishResult, ResultNotifier
from corpus_directus.notifier import DirectusFlowNotifier

WEBHOOK = "http://flows.test/trigger/abc"


@pytest.fixture
async def notifier():
    instance = DirectusFlowNotifier(webhook_url=WEBHOOK, api_token="webhook-token")
    yield instance
    await instance.aclose()


@pytest.mark.integration
class TestPublish:
    async def test_posts_the_envelope_with_its_own_auth_header(self, notifier):
        with respx.mock:
            route = respx.post(WEBHOOK).mock(return_value=httpx.Response(200, json={"ok": True}))
            result = await notifier.publish("article", {"article_id": "a1", "mema": {}})

        request = route.calls.last.request
        assert request.headers["x-api-token"] == "webhook-token"
        assert request.headers["content-type"] == "application/json"
        import json

        assert json.loads(request.content) == {
            "type": "article",
            "content": {"article_id": "a1", "mema": {}},
        }
        assert isinstance(result, PublishResult)
        assert result.delivered is True
        assert result.status_code == 200
        assert result.response == {"ok": True}

    async def test_the_auth_header_name_is_configurable(self):
        notifier = DirectusFlowNotifier(
            webhook_url=WEBHOOK, api_token="t", api_key_header="x-customer-token"
        )
        with respx.mock:
            route = respx.post(WEBHOOK).mock(return_value=httpx.Response(204))
            await notifier.publish("edition", {})
        assert route.calls.last.request.headers["x-customer-token"] == "t"
        await notifier.aclose()

    async def test_an_empty_response_body_is_not_an_error(self, notifier):
        """Directus Flows answer 204 with no body; `.json()` on that raises."""
        with respx.mock:
            respx.post(WEBHOOK).mock(return_value=httpx.Response(204))
            result = await notifier.publish("edition", {})
        assert result.delivered is True
        assert result.response is None

    @pytest.mark.parametrize(
        ("status", "expected", "retryable"),
        [
            (401, CorpusAuthError, False),
            (403, CorpusAuthError, False),
            (400, CorpusUnavailable, False),
            (500, CorpusUnavailable, True),
            (503, CorpusUnavailable, True),
        ],
    )
    async def test_http_failures_map_to_the_taxonomy(self, notifier, status, expected, retryable):
        with respx.mock:
            respx.post(WEBHOOK).mock(return_value=httpx.Response(status))
            with pytest.raises(expected) as excinfo:
                await notifier.publish("article", {})
        assert excinfo.value.retryable is retryable

    async def test_timeout_and_connect_map_to_unavailable(self, notifier):
        for exc, kind in (
            (httpx.ConnectTimeout("slow"), "timeout"),
            (httpx.ConnectError("refused"), "connect"),
        ):
            with respx.mock:
                respx.post(WEBHOOK).mock(side_effect=exc)
                with pytest.raises(CorpusUnavailable) as excinfo:
                    await notifier.publish("article", {})
            assert excinfo.value.kind == kind
            assert excinfo.value.retryable is True

    async def test_no_native_exception_escapes(self, notifier):
        with respx.mock:
            respx.post(WEBHOOK).mock(side_effect=httpx.ReadError("boom"))
            with pytest.raises(CorpusUnavailable) as excinfo:
                await notifier.publish("article", {})
        assert excinfo.value.__cause__ is None
        assert "ReadError" in str(excinfo.value)


@pytest.mark.unit
class TestConfiguration:
    def test_is_a_result_notifier(self, notifier):
        assert isinstance(notifier, ResultNotifier)

    async def test_ping_validates_configuration(self, notifier):
        await notifier.ping()

    def test_a_missing_url_is_a_config_error(self):
        from corpus.errors import CorpusConfigError

        with pytest.raises(CorpusConfigError):
            DirectusFlowNotifier(webhook_url="", api_token="t")

    def test_a_missing_token_is_a_config_error(self):
        from corpus.errors import CorpusConfigError

        with pytest.raises(CorpusConfigError):
            DirectusFlowNotifier(webhook_url=WEBHOOK, api_token="")

    def test_settings_build_a_notifier(self):
        from corpus_directus.settings import DirectusNotifierSettings
        from pydantic import SecretStr

        notifier = DirectusFlowNotifier.from_settings(
            DirectusNotifierSettings(webhook_url=WEBHOOK, api_token=SecretStr("t"))
        )
        assert isinstance(notifier, DirectusFlowNotifier)

    async def test_a_non_object_json_body_is_wrapped(self):
        notifier = DirectusFlowNotifier(webhook_url=WEBHOOK, api_token="t")
        with respx.mock:
            respx.post(WEBHOOK).mock(return_value=httpx.Response(200, json=[1, 2]))
            result = await notifier.publish("article", {})
        assert result.response == {"data": [1, 2]}
        await notifier.aclose()

    async def test_a_non_json_body_is_tolerated(self):
        notifier = DirectusFlowNotifier(webhook_url=WEBHOOK, api_token="t")
        with respx.mock:
            respx.post(WEBHOOK).mock(return_value=httpx.Response(200, content=b"OK"))
            result = await notifier.publish("article", {})
        assert result.delivered is True and result.response is None
        await notifier.aclose()
