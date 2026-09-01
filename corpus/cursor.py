"""Opaque pagination cursors.

Consumers must not be able to read or forge a cursor's contents: an adapter
that pages by keyset today and by a vendor token tomorrow must be able to
change without touching a single call site.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

from corpus.errors import InvalidDocument


def encode_cursor(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except Exception as exc:
        raise InvalidDocument(f"malformed cursor: {cursor!r}", kind="bad_value") from exc
    if not isinstance(payload, dict):
        raise InvalidDocument(f"malformed cursor: {cursor!r}", kind="bad_value")
    return payload
