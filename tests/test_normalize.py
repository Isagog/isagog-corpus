"""Hygiene helpers: the normalisation rules catalogued in the proposal §2.5."""

import pytest
from corpus.errors import InvalidDocument
from corpus.normalize import (
    normalize_date,
    normalize_optional_date,
    require_text,
    strip_html,
)


@pytest.mark.unit
class TestStripHtml:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("<p>Hello</p>", "Hello"),
            ("<b>Bold</b> and <i>italic</i>", "Bold and italic"),
            ("  spaced  ", "spaced"),
            ("no markup", "no markup"),
            ("", ""),
            (None, ""),
            ("<br/>", ""),
        ],
    )
    def test_folds_to_plain_text(self, raw, expected):
        assert strip_html(raw) == expected


@pytest.mark.unit
class TestRequireText:
    def test_returns_stripped_text(self):
        assert require_text("<h1>Headline</h1>", "headline") == "Headline"

    @pytest.mark.parametrize("raw", [None, "", "   ", "<p></p>"])
    def test_empty_raises_bad_value(self, raw):
        with pytest.raises(InvalidDocument) as excinfo:
            require_text(raw, "headline")
        assert excinfo.value.kind == "bad_value"
        assert "headline" in str(excinfo.value)
        assert excinfo.value.retryable is False


@pytest.mark.unit
class TestNormalizeDate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2024-01-15T10:00:00Z", "2024-01-15"),
            ("2024-01-15T10:00:00+02:00", "2024-01-15"),
            ("2024-01-15", "2024-01-15"),
            ("2024-01-15 10:00:00", "2024-01-15"),
        ],
    )
    def test_folds_iso_to_day(self, raw, expected):
        assert normalize_date(raw, "publish_date") == expected

    @pytest.mark.parametrize("raw", [None, "", "not-a-date", "15/01/2024"])
    def test_unparseable_raises_bad_value(self, raw):
        with pytest.raises(InvalidDocument) as excinfo:
            normalize_date(raw, "publish_date")
        assert excinfo.value.kind == "bad_value"

    def test_optional_tolerates_absence(self):
        assert normalize_optional_date(None) is None
        assert normalize_optional_date("") is None
        assert normalize_optional_date("2024-01-15T10:00:00Z") == "2024-01-15"

    def test_optional_still_rejects_garbage(self):
        with pytest.raises(InvalidDocument):
            normalize_optional_date("not-a-date")
