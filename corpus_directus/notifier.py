"""Outbound results, posted to a Directus Flow.

A separate object with its own client on purpose: the webhook has different
auth, a different pool and a different SLA from the CMS read path. Sharing the
corpus client's pool is one of the defects this library exists to close.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, final

import httpx
from corpus.errors import CorpusConfigError
from corpus.notify import PublishResult, ResultNotifier

from corpus_directus.errors import from_status, from_transport_error
from corpus_directus.settings import DirectusNotifierSettings

DEFAULT_AUTH_HEADER = "x-api-token"


@final
class DirectusFlowNotifier(ResultNotifier):
    def __init__(
        self,
        *,
        webhook_url: str,
        api_token: str,
        api_key_header: str = DEFAULT_AUTH_HEADER,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not webhook_url:
            raise CorpusConfigError("DirectusFlowNotifier requires a webhook URL")
        if not api_token:
            raise CorpusConfigError("DirectusFlowNotifier requires an API token")
        self._url = webhook_url
        self._client = client or httpx.AsyncClient(
            headers={
                api_key_header or DEFAULT_AUTH_HEADER: api_token,
                "Content-Type": "application/json",
            },
            timeout=timeout,
            transport=httpx.AsyncHTTPTransport(retries=0),
        )

    @classmethod
    def from_settings(cls, settings: DirectusNotifierSettings) -> DirectusFlowNotifier:
        return cls(
            webhook_url=settings.webhook_url,
            api_token=settings.api_token.get_secret_value(),
            api_key_header=settings.api_key_header,
            timeout=settings.timeout,
        )

    async def publish(self, kind: str, content: Mapping[str, Any]) -> PublishResult:
        """Posts the `{"type": …, "content": …}` envelope the Flow expects."""
        payload = {"type": kind, "content": dict(content)}
        try:
            response = await self._client.post(self._url, json=payload)
        except Exception as exc:
            raise from_transport_error(exc, "webhook delivery failed") from None

        if response.status_code >= 400:
            raise from_status(
                response.status_code,
                "webhook delivery failed",
                response.headers.get("Retry-After"),
            ) from None
        return PublishResult(
            delivered=True,
            status_code=response.status_code,
            response=_body(response),
        )

    async def ping(self) -> None:
        """Directus Flows expose no health endpoint; the check is that the
        notifier is configured at all, which the constructor already enforced."""
        return None

    async def aclose(self) -> None:
        await self._client.aclose()


def _body(response: httpx.Response) -> dict[str, Any] | None:
    """A Flow commonly answers 204 with no body — `.json()` on that raises."""
    if not response.content:
        return None
    try:
        parsed = response.json()
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else {"data": parsed}
