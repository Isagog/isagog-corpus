"""The outbound port: telling the world a result is ready.

Deliberately *not* a method on `Corpus`. The webhook is a different service
with different auth, a different connection pool and a different SLA; sharing
the CMS client's pool is one of the defects this library exists to close.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict


class PublishResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    delivered: bool
    status_code: int | None = None
    response: Mapping[str, Any] | None = None


class ResultNotifier(ABC):
    """Publishes pipeline results back to whoever asked for them."""

    @abstractmethod
    async def publish(self, kind: str, content: Mapping[str, Any]) -> PublishResult: ...

    @abstractmethod
    async def ping(self) -> None: ...

    async def aclose(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Release transport resources. No-op by default."""


class NullNotifier(ResultNotifier):
    """Same surface, delivers nothing. The default for consumers that have no
    webhook configured, so no call site needs an `if notifier is not None`."""

    async def publish(self, kind: str, content: Mapping[str, Any]) -> PublishResult:
        return PublishResult(delivered=False)

    async def ping(self) -> None:
        return None
