"""Outbound result port (proposal §4.8)."""

import pytest
from corpus.notify import NullNotifier, PublishResult, ResultNotifier
from pydantic import ValidationError


@pytest.mark.unit
class TestPublishResult:
    def test_is_frozen(self):
        result = PublishResult(delivered=True, status_code=200)
        with pytest.raises(ValidationError):
            result.delivered = False

    def test_defaults(self):
        result = PublishResult(delivered=False)
        assert result.status_code is None
        assert result.response is None


@pytest.mark.unit
class TestNullNotifier:
    async def test_publish_delivers_nothing(self):
        result = await NullNotifier().publish("article", {"article_id": "a1"})
        assert isinstance(result, PublishResult)
        assert result.delivered is False
        assert result.status_code is None

    async def test_ping_is_a_no_op(self):
        await NullNotifier().ping()

    async def test_aclose_is_a_no_op(self):
        await NullNotifier().aclose()

    def test_is_a_result_notifier(self):
        assert isinstance(NullNotifier(), ResultNotifier)


@pytest.mark.unit
def test_result_notifier_is_abstract():
    with pytest.raises(TypeError):
        ResultNotifier()  # type: ignore[abstract]
