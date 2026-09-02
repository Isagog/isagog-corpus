"""Pure-logic tests for `examples/duplicati.py`.

The script is not a package, so it is loaded from its path. Only the pure
layer is exercised here — the Directus calls are I/O and belong to the
staging suite, not to this one.
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "examples" / "duplicati.py"
_spec = importlib.util.spec_from_file_location("duplicati", _PATH)
assert _spec is not None and _spec.loader is not None
duplicati = importlib.util.module_from_spec(_spec)
# Registered before execution: pydantic resolves a model's annotations through
# `sys.modules`, and `ArticleRow` would otherwise never see `date`.
sys.modules["duplicati"] = duplicati
_spec.loader.exec_module(duplicati)

ArticleRow = duplicati.ArticleRow
build_clusters = duplicati.build_clusters
normalize_headline = duplicati.normalize_headline
row_from_item = duplicati.row_from_item
summarise = duplicati.summarise
pair_counts = duplicati.pair_counts


def row(source, day, headline, *, id_=None, status="published"):
    return ArticleRow(
        id=id_ or f"{source}-{headline}-{day}",
        source=source,
        day=day if isinstance(day, date) else date.fromisoformat(day),
        headline=headline,
        status=status,
        slug=None,
    )


@pytest.mark.unit
class TestNormalizeHeadline:
    def test_case_punctuation_and_spacing_all_fold_away(self):
        """The two imports quote differently: athena writes «Dialogo» where wp
        writes "Dialogo". Nothing that varies by importer may reach the key."""
        assert normalize_headline("«Dialogo» a Managua,  grazie ai vescovi") == normalize_headline(
            '"Dialogo" a Managua, grazie ai vescovi'
        )

    def test_accents_fold_to_their_base_letter(self):
        assert normalize_headline("Perché no") == normalize_headline("Perche no")

    def test_html_is_stripped_before_folding(self):
        assert normalize_headline("<em>Solo</em>: a Star Wars story") == "solo a star wars story"

    def test_absent_headlines_fold_to_empty(self):
        assert normalize_headline(None) == ""
        assert normalize_headline("   ") == ""


@pytest.mark.unit
class TestRowFromItem:
    def test_a_directus_item_becomes_a_row(self):
        item = {
            "id": "abc",
            "headline": "Nakba, la catastrofe infinita",
            "datePublished": "2018-05-15T21:59:16.000Z",
            "syncSource": "wp",
            "status": "published",
            "slug": "nakba",
        }
        assert row_from_item(item) == ArticleRow(
            id="abc",
            source="wp",
            day=date(2018, 5, 15),
            headline="Nakba, la catastrofe infinita",
            status="published",
            slug="nakba",
        )

    def test_an_unset_provenance_is_labelled_not_dropped(self):
        """Five rows carry no syncSource. They are part of the corpus and must
        show up in the tally rather than vanishing into a None bucket."""
        item = {"id": "a", "headline": "x", "datePublished": "2018-05-15T00:00:00Z"}
        assert row_from_item(item).source == duplicati.UNSET_SOURCE

    def test_a_row_without_a_usable_date_cannot_be_placed(self):
        item = {"id": "a", "headline": "x", "datePublished": None, "syncSource": "wp"}
        assert row_from_item(item) is None


@pytest.mark.unit
class TestBuildClusters:
    def test_the_same_headline_from_two_imports_on_one_day_is_one_cluster(self):
        rows = (
            row("wp", "2018-05-15", "Territori occupati e sciopero generale"),
            row("athena", "2018-05-15", "Territori occupati e sciopero generale"),
        )
        clusters = build_clusters(rows, tolerance_days=1, min_key_chars=12)
        assert len(clusters) == 1
        assert clusters[0].sources == ("athena", "wp")
        assert clusters[0].is_cross_source

    def test_midnight_utc_does_not_split_a_pair(self):
        """wp stamps ~21:59Z and athena 22:00Z, so the same piece can land on
        either side of a UTC midnight. One day of tolerance closes that."""
        rows = (
            row("wp", "2018-05-15", "Territori occupati e sciopero generale"),
            row("athena", "2018-05-16", "Territori occupati e sciopero generale"),
        )
        assert len(build_clusters(rows, tolerance_days=1, min_key_chars=12)) == 1

    def test_a_headline_reused_years_later_is_not_one_duplicate(self):
        """`Il punto della settimana` runs for decades. Without the date split
        every recurring column would read as a cross-import duplicate."""
        rows = (
            row("athena", "2003-05-15", "Il punto della settimana"),
            row("wp", "2018-05-15", "Il punto della settimana"),
        )
        clusters = build_clusters(rows, tolerance_days=1, min_key_chars=12)
        assert len(clusters) == 2
        assert not any(c.is_cross_source for c in clusters)

    def test_two_rows_of_one_import_cluster_but_are_not_cross_source(self):
        rows = (
            row("wp", "2018-05-15", "Territori occupati e sciopero", id_="a"),
            row("wp", "2018-05-15", "Territori occupati e sciopero", id_="b"),
        )
        clusters = build_clusters(rows, tolerance_days=1, min_key_chars=12)
        assert len(clusters) == 1
        assert not clusters[0].is_cross_source
        assert clusters[0].has_intra_source_repeat

    def test_headlines_too_short_to_identify_anything_are_held_back(self):
        """`Lettere` is a standing rubric, not a story. Keying on it would
        manufacture duplicates out of a masthead."""
        rows = (
            row("wp", "2018-05-15", "Lettere"),
            row("athena", "2018-05-15", "Lettere"),
        )
        assert build_clusters(rows, tolerance_days=1, min_key_chars=12) == ()

    def test_clusters_come_back_largest_first(self):
        rows = (
            row("wp", "2018-05-15", "Una storia sola qui"),
            row("wp", "2018-05-16", "Una storia condivisa qui"),
            row("athena", "2018-05-16", "Una storia condivisa qui"),
        )
        clusters = build_clusters(rows, tolerance_days=1, min_key_chars=12)
        assert [len(c.rows) for c in clusters] == [2, 1]


@pytest.mark.unit
class TestSummarise:
    def test_it_counts_what_the_wp_scope_decision_rests_on(self):
        rows = (
            row("wp", "2018-05-15", "Territori occupati e sciopero"),
            row("athena", "2018-05-15", "Territori occupati e sciopero"),
            row("wp", "2018-05-15", "Una storia solamente sua"),
            row("athena", "2018-05-15", "Lettere"),
        )
        clusters = build_clusters(rows, tolerance_days=1, min_key_chars=12)
        stats = summarise(rows, clusters)

        assert stats.rows_scanned == 4
        assert stats.rows_by_source == {"athena": 2, "wp": 2}
        assert stats.skipped_short == 1
        assert stats.clustered_rows == 3
        assert stats.clusters == 2
        assert stats.cross_source_clusters == 1
        # Two rows, one story: exactly one of them is redundant.
        assert stats.redundant_rows == 1
        assert stats.twinned_rows_by_source == {"athena": 1, "wp": 1}

    def test_an_empty_window_reports_zeroes_rather_than_dividing_by_them(self):
        stats = summarise((), ())
        assert stats.rows_scanned == 0
        assert stats.duplication_rate == 0.0


@pytest.mark.unit
class TestPairCounts:
    def test_every_unordered_pair_in_a_cluster_is_counted_once(self):
        rows = (
            row("wp", "2018-05-15", "Territori occupati e sciopero"),
            row("athena", "2018-05-15", "Territori occupati e sciopero"),
            row("athenaPre2002", "2018-05-15", "Territori occupati e sciopero"),
        )
        clusters = build_clusters(rows, tolerance_days=1, min_key_chars=12)
        assert pair_counts(clusters) == {
            ("athena", "athenaPre2002"): 1,
            ("athena", "wp"): 1,
            ("athenaPre2002", "wp"): 1,
        }

    def test_a_cluster_from_one_import_contributes_no_pair(self):
        rows = (
            row("wp", "2018-05-15", "Territori occupati e sciopero", id_="a"),
            row("wp", "2018-05-15", "Territori occupati e sciopero", id_="b"),
        )
        clusters = build_clusters(rows, tolerance_days=1, min_key_chars=12)
        assert pair_counts(clusters) == {}


@pytest.mark.unit
class TestMonthWindows:
    def test_a_range_is_cut_into_half_open_month_windows(self):
        windows = duplicati.month_windows(date(2018, 5, 10), date(2018, 7, 3))
        assert windows == (
            (date(2018, 5, 10), date(2018, 6, 1)),
            (date(2018, 6, 1), date(2018, 7, 1)),
            (date(2018, 7, 1), date(2018, 7, 4)),
        )

    def test_the_end_day_is_included(self):
        windows = duplicati.month_windows(date(2018, 5, 10), date(2018, 5, 10))
        assert windows == ((date(2018, 5, 10), date(2018, 5, 11)),)

    def test_an_inverted_range_yields_nothing(self):
        assert duplicati.month_windows(date(2018, 5, 10), date(2018, 5, 9)) == ()


@pytest.mark.unit
class TestOverlapWindow:
    SPANS = (
        duplicati.SourceSpan(
            source="wp", count=171764, first=date(1979, 2, 1), last=date(2026, 9, 5)
        ),
        duplicati.SourceSpan(
            source="athena", count=391578, first=date(2001, 2, 5), last=date(2023, 12, 30)
        ),
        duplicati.SourceSpan(
            source="mema", count=7340, first=date(1971, 4, 28), last=date(1976, 8, 13)
        ),
    )

    def test_it_ends_where_the_second_import_stopped_being_written(self):
        """Past 2023-12-30 only `wp` is live, so a window running to today
        would scan a year that cannot contain a cross-import duplicate."""
        assert duplicati.overlap_window(self.SPANS, 365) == (date(2022, 12, 31), date(2023, 12, 30))

    def test_it_never_starts_before_the_archive_had_two_imports(self):
        assert duplicati.overlap_window(self.SPANS, 100_000)[0] == date(1979, 2, 1)

    def test_a_stray_row_is_not_an_import(self):
        """Five rows carry no syncSource, one stamped 2025. Counting that as an
        import would aim the default scan at a year with no overlap at all."""
        strays = (
            *self.SPANS,
            duplicati.SourceSpan(
                source=duplicati.UNSET_SOURCE,
                count=5,
                first=date(1973, 4, 28),
                last=date(2025, 1, 9),
            ),
        )
        assert duplicati.overlap_window(strays, 365) == (date(2022, 12, 31), date(2023, 12, 30))

    def test_a_single_import_archive_has_no_overlap_to_sample(self):
        assert duplicati.overlap_window(self.SPANS[:1], 365) is None


@pytest.mark.unit
class TestSampleClusters:
    def test_it_spreads_across_the_list_instead_of_taking_the_head(self):
        """Clusters arrive largest-first, so the head is all three-row
        pathologies and none of the two-row pairs that are the real finding."""
        clusters = tuple(
            duplicati.Cluster(
                key=f"k{i}",
                rows=(
                    row("wp", "2018-05-15", f"Una storia numero {i}"),
                    row("athena", "2018-05-15", f"Una storia numero {i}"),
                ),
            )
            for i in range(10)
        )
        assert [c.key for c in duplicati.sample_clusters(clusters, 5)] == [
            "k0",
            "k2",
            "k4",
            "k6",
            "k8",
        ]

    def test_it_never_samples_a_cluster_from_a_single_import(self):
        clusters = (
            duplicati.Cluster(key="k", rows=(row("wp", "2018-05-15", "Una storia sola qui"),)),
        )
        assert duplicati.sample_clusters(clusters, 5) == ()

    def test_asking_for_more_than_exist_returns_all_of_them(self):
        clusters = duplicati.build_clusters(
            (
                row("wp", "2018-05-15", "Territori occupati e sciopero"),
                row("athena", "2018-05-15", "Territori occupati e sciopero"),
            ),
            tolerance_days=1,
            min_key_chars=12,
        )
        assert len(duplicati.sample_clusters(clusters, 50)) == 1


@pytest.mark.unit
class TestIsContested:
    def test_two_full_imports_contest_the_year(self):
        assert duplicati.is_contested({"athena": 12_061, "wp": 13_172})

    def test_a_single_stray_row_does_not(self):
        """2025 is `wp` plus one row with no syncSource. Calling that a
        contested year would bury 2023 in noise."""
        assert not duplicati.is_contested({"wp": 13_674, duplicati.UNSET_SOURCE: 1})

    def test_a_small_but_real_second_import_does(self):
        """athena holds only 33 rows in 2001 against 20 011 from athenaPre2002,
        but those 33 dates really do resolve two ways."""
        assert duplicati.is_contested({"athenaPre2002": 20_011, "athena": 300})

    def test_one_import_is_never_contested(self):
        assert not duplicati.is_contested({"wp": 13_674})
        assert not duplicati.is_contested({})
