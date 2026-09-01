"""Directus save notifications → `ChangeSignal`.

Tolerant by construction: this runs on a request from outside, so it must
never raise, and every unrecognised value degrades to the conservative
`UNKNOWN`. It reports what happened; whether that earns a fast lane is the
consumer's policy, above the port.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from corpus.signals import (
    ChangeSignal,
    coerce_actor_kind,
    coerce_change_kind,
    coerce_signal_date,
)

#: Directus Flow event names ("items.create") carry a collection prefix.
_EVENT_PREFIX = "items."
_EVENT_KEYS = ("event", "change", "action")
_ACTOR_KEYS = ("source", "actor", "trigger")
_FINGERPRINT_KEYS = ("content_fingerprint", "fingerprint")


def parse_change(
    document_id: str,
    body: Mapping[str, Any] | None,
    *,
    received_at: datetime | None = None,
) -> ChangeSignal:
    payload: Mapping[str, Any] = body if isinstance(body, Mapping) else {}
    return ChangeSignal(
        article_id=document_id,
        change=coerce_change_kind(_strip_prefix(_first(payload, _EVENT_KEYS))),
        actor=coerce_actor_kind(_first(payload, _ACTOR_KEYS)),
        publish_date=coerce_signal_date(
            payload.get("publish_date") or payload.get("datePublished")
        ),
        status=_text(payload.get("status")),
        fingerprint=_text(_first(payload, _FINGERPRINT_KEYS)),
        received_at=received_at or datetime.now(UTC),
        raw=dict(payload),
    )


def _first(payload: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _strip_prefix(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_EVENT_PREFIX):
        return value[len(_EVENT_PREFIX) :]
    return value


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
