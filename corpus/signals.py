"""Inbound evidence that something changed in the archive.

Three port invariants, carried over from the corsie/verbi design and tested by
the contract suite:

1. no `priority`/`lane` field exists — evidence, never verdict;
2. every default is the most conservative value (UNKNOWN demotes);
3. parsing never raises: unknown vocabulary degrades to UNKNOWN, and a
   body-less request still yields a fully formed signal.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from corpus.normalize import parse_iso_date


class ChangeKind(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    PUBLISH = "publish"
    UNKNOWN = "unknown"


class ActorKind(StrEnum):
    EDITOR = "editor"
    BULK = "bulk"
    IMPORT = "import"
    MIGRATION = "migration"
    API = "api"
    UNKNOWN = "unknown"


class ChangeSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    article_id: str
    change: ChangeKind = ChangeKind.UNKNOWN
    actor: ActorKind = ActorKind.UNKNOWN
    publish_date: date | None = None
    status: str | None = None
    fingerprint: str | None = None
    received_at: datetime
    raw: Mapping[str, Any] = Field(default_factory=dict)  # verbatim, for audit


def coerce_change_kind(value: Any) -> ChangeKind:
    return _coerce_enum(value, ChangeKind, ChangeKind.UNKNOWN)


def coerce_actor_kind(value: Any) -> ActorKind:
    return _coerce_enum(value, ActorKind, ActorKind.UNKNOWN)


def coerce_signal_date(value: Any) -> date | None:
    """Tolerant date parsing: anything unrecognisable is simply absent."""
    if isinstance(value, (str, date, datetime)):
        return parse_iso_date(value)
    return None


def _coerce_enum[E: StrEnum](value: Any, enum: type[E], fallback: E) -> E:
    if isinstance(value, enum):
        return value
    if not isinstance(value, str):
        return fallback
    try:
        return enum(value.strip().lower())
    except ValueError:
        return fallback
