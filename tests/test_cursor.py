"""Opaque pagination cursors (proposal §4.6: consumers must not decode them)."""

import pytest
from corpus.cursor import decode_cursor, encode_cursor
from corpus.errors import InvalidDocument


@pytest.mark.unit
class TestCursorCodec:
    def test_round_trips(self):
        payload = {"d": "2024-01-15", "i": "abc"}
        assert decode_cursor(encode_cursor(payload)) == payload

    def test_is_url_safe_and_unpadded(self):
        cursor = encode_cursor({"d": "2024-01-15", "i": "a" * 40})
        assert "=" not in cursor
        assert "/" not in cursor and "+" not in cursor

    def test_is_opaque(self):
        assert "2024-01-15" not in encode_cursor({"d": "2024-01-15"})

    @pytest.mark.parametrize("garbage", ["", "!!!!", "YWJj", "eyJhIjog"])
    def test_garbage_raises_bad_value(self, garbage):
        with pytest.raises(InvalidDocument) as excinfo:
            decode_cursor(garbage)
        assert excinfo.value.kind == "bad_value"
