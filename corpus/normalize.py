"""CMS-hygiene helpers shared by every adapter.

Hygiene is what *all* consumers want (HTML stripped, dates folded to a day,
absent optional text folded to ""). Pipeline policy — body length bounds and
such — is not here; it lives in `corpus.policy` and belongs to the consumer.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from corpus.errors import InvalidDocument

_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str | None) -> str:
    """Tolerant: None and empty both fold to ""."""
    if not text:
        return ""
    return _TAG.sub("", text).strip()


def require_text(text: str | None, field: str) -> str:
    """Strip HTML and insist something survives."""
    stripped = strip_html(text)
    if not stripped:
        raise InvalidDocument(f"{field!r} is empty after normalisation", kind="bad_value")
    return stripped


def normalize_date(value: str | date | datetime | None, field: str) -> str:
    """ISO-8601 (with or without time/zone) → YYYY-MM-DD."""
    parsed = parse_iso_date(value)
    if parsed is None:
        raise InvalidDocument(f"{field!r} is not an ISO date: {value!r}", kind="bad_value")
    return parsed.strftime("%Y-%m-%d")


def normalize_optional_date(value: str | date | datetime | None) -> str | None:
    """Absent stays absent; present but unparseable is still an error."""
    if value is None or value == "":
        return None
    return normalize_date(value, "date")


def parse_iso_date(value: str | date | datetime | None) -> date | None:
    """Best-effort ISO parse. Returns None instead of raising: callers decide
    whether an unparseable value is an error (`normalize_date`) or simply
    absent (`corpus.signals`)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None
