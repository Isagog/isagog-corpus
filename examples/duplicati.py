"""Measure how much of a Directus archive is the same article imported twice.

pulse.ilmanifesto.it was filled by four independent imports — `mema`,
`athenaPre2002`, `athena` and `wp` — and each one carried the whole run of the
paper it covered. Where their date ranges overlap, the same piece exists more
than once under different `syncSource` values, with different ids, different
statuses (`wp` rows are `published`, `athena` rows are `archive`) and, often,
no slug on the older copy. Nothing in a row says which copy is authoritative.

That is why `MANIFESTO_WP_SCHEMA` exists: it narrows *editions* to the one
series still being written. This script measures the same phenomenon one level
down, on articles, so the scoping decision rests on numbers rather than on a
spot check.

What counts as a duplicate here: two rows whose headlines fold to the same key
(HTML stripped, accents and punctuation and case removed) and whose publication
days are within `--tolerance` days of each other. Headline equality is the only
join available — there is no shared identifier across imports, `syncSourceId`
being per-import — so the count is an estimate. It errs low: an importer that
retitled a piece is invisible to it. Short headlines are held back entirely
(`--min-key-chars`), because standing rubrics like `Lettere` would otherwise
manufacture duplicates out of a masthead.

Usage:
    cp examples/.env.example examples/.env   # then fill in a real API key
    uv run --extra examples python examples/duplicati.py
    uv run --extra examples python examples/duplicati.py --from 2018-01-01 --to 2018-12-31
    uv run --extra examples python examples/duplicati.py --days 30 --json
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
from corpus.normalize import parse_iso_date, strip_html
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_BASE_URL = "https://pulse.ilmanifesto.it"

#: Rows with no `syncSource` are a real (tiny) population on this instance.
#: They get a label rather than a None bucket so they stay visible in tallies.
UNSET_SOURCE = "(unset)"

#: The archive's own vocabulary. Named here rather than inlined so a differently
#: configured Directus instance is a flag, not an edit.
DEFAULT_COLLECTION = "articles"
DEFAULT_SOURCE_FIELD = "syncSource"
DEFAULT_DATE_FIELD = "datePublished"
DEFAULT_HEADLINE_FIELD = "headline"

#: One day of slack. `wp` stamps articles at ~21:59Z and `athena` at 22:00Z, so
#: the same piece routinely lands on either side of a UTC midnight.
DEFAULT_TOLERANCE_DAYS = 1

#: `Lettere`, `Il punto`, `Sport` — below this length a headline names a rubric,
#: not a story, and cannot identify anything.
DEFAULT_MIN_KEY_CHARS = 12

DEFAULT_WINDOW_DAYS = 365
DEFAULT_PAGE_SIZE = 500
DEFAULT_SAMPLES = 8

#: A runaway guard on the offset walk inside one month, not a result limit.
MAX_PAGES_PER_WINDOW = 200

#: Below this, a `syncSource` value is a stray row rather than an import, and
#: must not be allowed to steer the default scan window.
MIN_SPAN_ROWS = 100

#: A year's second import has to reach this share of the year before the year
#: counts as genuinely covered twice.
MARGINAL_SHARE = 0.01

_NON_KEY = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------
# Pure layer: everything below is a function of its arguments alone.
# --------------------------------------------------------------------------


class ArticleRow(BaseModel):
    """One archive row, reduced to what duplicate detection needs."""

    model_config = ConfigDict(frozen=True)

    id: str
    source: str
    day: date
    headline: str
    status: str | None = None
    slug: str | None = None


class Cluster(BaseModel):
    """Rows that fold to one headline key within the date tolerance."""

    model_config = ConfigDict(frozen=True)

    key: str
    rows: tuple[ArticleRow, ...]

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({row.source for row in self.rows}))

    @property
    def is_cross_source(self) -> bool:
        """The finding this script exists for: one story, two imports."""
        return len(self.sources) > 1

    @property
    def has_intra_source_repeat(self) -> bool:
        """One import holding the same headline twice — a different defect,
        and one the `wp` scoping decision does not fix."""
        return len(self.rows) > len(self.sources)

    @property
    def day_span(self) -> int:
        days = [row.day for row in self.rows]
        return (max(days) - min(days)).days


class WindowStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    rows_scanned: int
    rows_by_source: dict[str, int] = Field(default_factory=dict)
    rows_by_status: dict[str, int] = Field(default_factory=dict)
    skipped_short: int = 0
    clustered_rows: int = 0
    clusters: int = 0
    cross_source_clusters: int = 0
    intra_source_clusters: int = 0
    redundant_rows: int = 0
    twinned_rows_by_source: dict[str, int] = Field(default_factory=dict)

    @property
    def duplication_rate(self) -> float:
        """Share of clustered rows that are a second copy of something."""
        if not self.clustered_rows:
            return 0.0
        return self.redundant_rows / self.clustered_rows


def normalize_headline(text: str | None) -> str:
    """Fold a headline to a join key.

    Everything that varies between importers goes: markup, case, accents and
    punctuation. `«Dialogo» a Managua` and `"Dialogo" a Managua` are the same
    story and must produce the same key.
    """
    stripped = strip_html(text)
    if not stripped:
        return ""
    decomposed = unicodedata.normalize("NFKD", stripped)
    unaccented = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_KEY.sub(" ", unaccented.lower()).strip()


def row_from_item(
    item: Mapping[str, Any],
    *,
    source_field: str = DEFAULT_SOURCE_FIELD,
    date_field: str = DEFAULT_DATE_FIELD,
    headline_field: str = DEFAULT_HEADLINE_FIELD,
) -> ArticleRow | None:
    """Project a Directus row. Returns None when it carries no usable date —
    such a row cannot be placed on the timeline, so it cannot be matched."""
    day = parse_iso_date(item.get(date_field))
    if day is None:
        return None
    source = item.get(source_field)
    return ArticleRow(
        id=str(item.get("id", "")),
        source=source if isinstance(source, str) and source else UNSET_SOURCE,
        day=day,
        headline=strip_html(item.get(headline_field)),
        status=item.get("status"),
        slug=item.get("slug"),
    )


def build_clusters(
    rows: Iterable[ArticleRow],
    *,
    tolerance_days: int = DEFAULT_TOLERANCE_DAYS,
    min_key_chars: int = DEFAULT_MIN_KEY_CHARS,
) -> tuple[Cluster, ...]:
    """Group rows by headline key, then split each group on date gaps.

    The split is what keeps a recurring column honest: `Il punto della
    settimana` ran for two decades, and without it every instance of it would
    read as a cross-import duplicate of every other.
    """
    by_key: dict[str, list[ArticleRow]] = defaultdict(list)
    for row in rows:
        key = normalize_headline(row.headline)
        if len(key) >= min_key_chars:
            by_key[key].append(row)

    clusters = [
        Cluster(key=key, rows=run)
        for key, group in by_key.items()
        for run in _split_on_date_gaps(group, tolerance_days)
    ]
    clusters.sort(key=lambda c: (-len(c.rows), c.key))
    return tuple(clusters)


def _split_on_date_gaps(
    group: Sequence[ArticleRow], tolerance_days: int
) -> list[tuple[ArticleRow, ...]]:
    ordered = sorted(group, key=lambda r: (r.day, r.id))
    runs: list[list[ArticleRow]] = [[ordered[0]]]
    for row in ordered[1:]:
        if (row.day - runs[-1][-1].day).days > tolerance_days:
            runs.append([row])
        else:
            runs[-1].append(row)
    return [tuple(run) for run in runs]


def summarise(rows: Sequence[ArticleRow], clusters: Sequence[Cluster]) -> WindowStats:
    clustered_rows = sum(len(c.rows) for c in clusters)
    twinned: Counter[str] = Counter()
    for cluster in clusters:
        if cluster.is_cross_source:
            twinned.update(row.source for row in cluster.rows)
    return WindowStats(
        rows_scanned=len(rows),
        rows_by_source=dict(sorted(Counter(r.source for r in rows).items())),
        rows_by_status=dict(sorted(Counter(r.status or "(unset)" for r in rows).items())),
        skipped_short=len(rows) - clustered_rows,
        clustered_rows=clustered_rows,
        clusters=len(clusters),
        cross_source_clusters=sum(1 for c in clusters if c.is_cross_source),
        intra_source_clusters=sum(1 for c in clusters if c.has_intra_source_repeat),
        redundant_rows=sum(len(c.rows) - 1 for c in clusters if len(c.rows) > 1),
        twinned_rows_by_source=dict(sorted(twinned.items())),
    )


def pair_counts(clusters: Iterable[Cluster]) -> dict[tuple[str, str], int]:
    """How many clusters each unordered pair of imports both appear in."""
    counts: Counter[tuple[str, str]] = Counter()
    for cluster in clusters:
        counts.update(itertools.combinations(cluster.sources, 2))
    return dict(sorted(counts.items()))


def month_windows(start: date, end: date) -> tuple[tuple[date, date], ...]:
    """Cut an inclusive date range into half-open month windows.

    Half-open so adjacent windows cannot both claim a boundary row, and monthly
    so no single request has to page through more than a few thousand rows.
    """
    if end < start:
        return ()
    stop = end + timedelta(days=1)
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor < stop:
        following = _first_of_next_month(cursor)
        windows.append((cursor, min(following, stop)))
        cursor = following
    return tuple(windows)


def _first_of_next_month(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)


def overlap_window(
    spans: Sequence[SourceSpan], days: int, *, min_rows: int = MIN_SPAN_ROWS
) -> tuple[date, date] | None:
    """The last `days` of the period during which two imports were both live.

    Scanning the whole overlap would mean most of a 750 000-row archive. The
    tail of it is representative and costs a minute.

    `min_rows` is what keeps the default honest: this instance holds five rows
    with no `syncSource` at all, one of them stamped 2025, and treating that
    stray as an import would aim the whole scan at a year where nothing
    overlaps.
    """
    dated = [
        (s.first, s.last)
        for s in spans
        if s.first is not None and s.last is not None and s.count >= min_rows
    ]
    if len(dated) < 2:
        return None
    # The last day on which some other import was still running alongside the
    # one that outlives it: the second-largest `last` across imports. Likewise
    # the second-smallest `first` is the day the archive stopped being covered
    # by a single import.
    end = sorted((last for _, last in dated), reverse=True)[1]
    begin_of_overlap = sorted(first for first, _ in dated)[1]
    start = max(begin_of_overlap, end - timedelta(days=days - 1))
    return (start, end)


# --------------------------------------------------------------------------
# I/O layer: Directus.
# --------------------------------------------------------------------------


class SourceSpan(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    count: int
    first: date | None = None
    last: date | None = None


class Probe(BaseModel):
    """Where the archive is and what its columns are called."""

    model_config = ConfigDict(frozen=True)

    base_url: str
    collection: str = DEFAULT_COLLECTION
    source_field: str = DEFAULT_SOURCE_FIELD
    date_field: str = DEFAULT_DATE_FIELD
    headline_field: str = DEFAULT_HEADLINE_FIELD
    statuses: tuple[str, ...] = ()  # empty = every status, which is the point

    @property
    def path(self) -> str:
        return f"/items/{self.collection}"


class ProbeError(RuntimeError):
    """The archive could not be read. Always carries what was asked for."""


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> list[Any]:
    try:
        response = await client.get(path, params=params)
    except httpx.HTTPError as err:  # transport, not status
        raise ProbeError(f"GET {path} failed: {err}") from err
    if response.status_code != httpx.codes.OK:
        raise ProbeError(f"GET {path} returned {response.status_code}: {response.text[:200]}")
    try:
        payload = response.json()
    except ValueError as err:
        raise ProbeError(f"GET {path} returned a non-JSON body") from err
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ProbeError(f"GET {path} returned no `data` array")
    return data


def _status_filter(probe: Probe) -> dict[str, str]:
    """Empty by default on purpose: `athena` rows are `archive`, not
    `published`, so a published-only default would hide the very duplicates
    this script is counting."""
    if not probe.statuses:
        return {}
    return {"filter[status][_in]": ",".join(probe.statuses)}


async def fetch_source_spans(client: httpx.AsyncClient, probe: Probe) -> tuple[SourceSpan, ...]:
    """One request: row count and date span per import, over the whole archive."""
    data = await _get(
        client,
        probe.path,
        {
            "aggregate[count]": "id",
            "aggregate[min]": probe.date_field,
            "aggregate[max]": probe.date_field,
            "groupBy": probe.source_field,
            "limit": -1,
            **_status_filter(probe),
        },
    )
    spans = [
        SourceSpan(
            source=row.get(probe.source_field) or UNSET_SOURCE,
            count=int((row.get("count") or {}).get("id") or 0),
            first=parse_iso_date((row.get("min") or {}).get(probe.date_field)),
            last=parse_iso_date((row.get("max") or {}).get(probe.date_field)),
        )
        for row in data
        if isinstance(row, dict)
    ]
    return tuple(sorted(spans, key=lambda s: -s.count))


async def fetch_year_matrix(client: httpx.AsyncClient, probe: Probe) -> dict[int, dict[str, int]]:
    """One request: rows per (year, import) across the whole archive. This is
    what shows *where* the imports overlap without scanning a single row."""
    data = await _get(
        client,
        probe.path,
        {
            "aggregate[count]": "id",
            "groupBy": f"year({probe.date_field}),{probe.source_field}",
            "limit": -1,
            **_status_filter(probe),
        },
    )
    matrix: dict[int, dict[str, int]] = defaultdict(dict)
    for row in data:
        if not isinstance(row, dict):
            continue
        year = row.get(f"{probe.date_field}_year")
        if year is None:
            continue
        source = row.get(probe.source_field) or UNSET_SOURCE
        matrix[int(year)][source] = int((row.get("count") or {}).get("id") or 0)
    return dict(sorted(matrix.items()))


async def iter_rows(
    client: httpx.AsyncClient,
    probe: Probe,
    start: date,
    end: date,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> AsyncIterator[ArticleRow]:
    """Walk every row in an inclusive date range, one month at a time."""
    fields = ",".join(
        ["id", "status", "slug", probe.source_field, probe.date_field, probe.headline_field]
    )
    for window_start, window_stop in month_windows(start, end):
        for offset in range(0, MAX_PAGES_PER_WINDOW * page_size, page_size):
            data = await _get(
                client,
                probe.path,
                {
                    "fields": fields,
                    "sort": f"{probe.date_field},id",
                    "limit": page_size,
                    "offset": offset,
                    f"filter[{probe.date_field}][_gte]": f"{window_start.isoformat()}T00:00:00Z",
                    f"filter[{probe.date_field}][_lt]": f"{window_stop.isoformat()}T00:00:00Z",
                    **_status_filter(probe),
                },
            )
            for item in data:
                row = row_from_item(
                    item,
                    source_field=probe.source_field,
                    date_field=probe.date_field,
                    headline_field=probe.headline_field,
                )
                if row is not None:
                    yield row
            if len(data) < page_size:
                break


# --------------------------------------------------------------------------
# Rendering.
# --------------------------------------------------------------------------


def _rule(title: str) -> str:
    return f"\n\033[1m{title}\033[0m\n" + "-" * max(len(title), 40)


def render_spans(spans: Sequence[SourceSpan]) -> str:
    total = sum(s.count for s in spans) or 1
    lines = [_rule("PROVENANCES"), f"{'source':<16}{'articles':>10}{'share':>8}  {'span'}"]
    for span in spans:
        first = span.first.isoformat() if span.first else "?"
        last = span.last.isoformat() if span.last else "?"
        lines.append(
            f"{span.source:<16}{span.count:>10,}{span.count / total:>8.1%}  {first} -> {last}"
        )
    lines.append(f"{'TOTAL':<16}{total:>10,}")
    return "\n".join(lines)


def is_contested(year_row: Mapping[str, int], threshold: float = MARGINAL_SHARE) -> bool:
    """Is this year genuinely covered twice, or does a stray row make it look so?

    2025 carries 13 674 `wp` rows and exactly one with no `syncSource`. That is
    not a year with two copies of everything, and printing it as one next to
    2023 — where two imports each hold twelve thousand rows — would bury the
    finding in noise.
    """
    if len(year_row) < 2:
        return False
    counts = sorted(year_row.values(), reverse=True)
    total = sum(counts)
    return bool(total) and counts[1] / total >= threshold


def render_year_matrix(matrix: Mapping[int, Mapping[str, int]], sources: Sequence[str]) -> str:
    overlapping = {y: row for y, row in matrix.items() if len(row) > 1}
    contested = {y for y, row in overlapping.items() if is_contested(row)}
    lines = [
        _rule("YEARS COVERED BY MORE THAN ONE IMPORT"),
        "In a year marked *, a date lookup has no single authoritative answer.",
        "",
        f"{'year':<7}" + "".join(f"{s:>16}" for s in sources),
    ]
    for year, row in overlapping.items():
        cells = "".join(f"{row.get(s, 0):>16,}" if s in row else f"{'.':>16}" for s in sources)
        lines.append(f"{year}{'*' if year in contested else ' '}  {cells}")
    single = len(matrix) - len(overlapping)
    lines.append("")
    lines.append(
        f"{len(contested)} contested year(s), "
        f"{len(overlapping) - len(contested)} with a marginal second import; "
        f"{single} year(s) covered by a single import are not listed."
    )
    return "\n".join(lines)


def render_window(window: tuple[date, date], stats: WindowStats) -> str:
    start, end = window
    lines = [
        _rule(f"DUPLICATE SCAN  {start.isoformat()} -> {end.isoformat()}"),
        f"rows scanned            {stats.rows_scanned:>10,}",
        "  by import             "
        + ", ".join(f"{k}={v:,}" for k, v in stats.rows_by_source.items()),
        "  by status             "
        + ", ".join(f"{k}={v:,}" for k, v in stats.rows_by_status.items()),
        f"headline too short      {stats.skipped_short:>10,}  (excluded from matching)",
        f"rows matched on         {stats.clustered_rows:>10,}",
        "",
        f"distinct stories        {stats.clusters:>10,}",
        f"  seen in 2+ imports    {stats.cross_source_clusters:>10,}",
        f"  repeated in 1 import  {stats.intra_source_clusters:>10,}",
        f"redundant copies        {stats.redundant_rows:>10,}",
        f"duplication rate        {stats.duplication_rate:>10.1%}  "
        "(share of matched rows that are a second copy)",
    ]
    if stats.twinned_rows_by_source:
        lines.append("")
        lines.append("rows that have a twin in another import:")
        for source, twinned in stats.twinned_rows_by_source.items():
            scanned = stats.rows_by_source.get(source, 0) or 1
            lines.append(
                f"  {source:<16}{twinned:>10,}  of {scanned:,} scanned ({twinned / scanned:.1%})"
            )
    return "\n".join(lines)


def render_pairs(pairs: Mapping[tuple[str, str], int]) -> str:
    lines = [_rule("IMPORT PAIRS SHARING A STORY")]
    if not pairs:
        lines.append("none in this window.")
        return "\n".join(lines)
    for (left, right), count in sorted(pairs.items(), key=lambda kv: -kv[1]):
        lines.append(f"{left + ' + ' + right:<36}{count:>8,} stories")
    return "\n".join(lines)


def sample_clusters(clusters: Sequence[Cluster], limit: int) -> tuple[Cluster, ...]:
    """Spread the examples evenly over the cross-import clusters.

    They arrive largest-first, and taking the head would show only the three-row
    pathologies — never the two-row pair that is the ordinary case and 98% of
    the finding.
    """
    cross = [c for c in clusters if c.is_cross_source]
    if limit < 1 or not cross:
        return ()
    if len(cross) <= limit:
        return tuple(cross)
    step = len(cross) / limit
    return tuple(cross[int(i * step)] for i in range(limit))


def render_samples(clusters: Sequence[Cluster], limit: int) -> str:
    lines = [_rule("EXAMPLES")]
    shown = sample_clusters(clusters, limit)
    if not shown:
        lines.append("none in this window.")
        return "\n".join(lines)
    for cluster in shown:
        head = cluster.rows[0]
        lines.append(f'\n"{head.headline}"')
        for row in sorted(cluster.rows, key=lambda r: r.source):
            slug = row.slug or "(no slug)"
            lines.append(
                f"  {row.source:<16}{row.day.isoformat()}  {row.status or '?':<10}{row.id}  {slug}"
            )
    return "\n".join(lines)


def render_report(
    spans: Sequence[SourceSpan],
    matrix: Mapping[int, Mapping[str, int]],
    window: tuple[date, date],
    stats: WindowStats,
    pairs: Mapping[tuple[str, str], int],
    clusters: Sequence[Cluster],
    samples: int,
) -> str:
    sources = [s.source for s in spans]
    return "\n".join(
        [
            render_spans(spans),
            render_year_matrix(matrix, sources),
            render_window(window, stats),
            render_pairs(pairs),
            render_samples(clusters, samples),
            "",
        ]
    )


def report_as_json(
    spans: Sequence[SourceSpan],
    matrix: Mapping[int, Mapping[str, int]],
    window: tuple[date, date],
    stats: WindowStats,
    pairs: Mapping[tuple[str, str], int],
    clusters: Sequence[Cluster],
    samples: int,
) -> dict[str, Any]:
    return {
        "provenances": [s.model_dump(mode="json") for s in spans],
        "years": {str(year): dict(row) for year, row in matrix.items()},
        "window": {"from": window[0].isoformat(), "to": window[1].isoformat()},
        "stats": {**stats.model_dump(), "duplication_rate": stats.duplication_rate},
        "pairs": [
            {"sources": list(pair), "stories": count}
            for pair, count in sorted(pairs.items(), key=lambda kv: -kv[1])
        ],
        "examples": [
            {"headline": c.rows[0].headline, "rows": [r.model_dump(mode="json") for r in c.rows]}
            for c in sample_clusters(clusters, samples)
        ],
    }


# --------------------------------------------------------------------------
# Entry point.
# --------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--from", dest="start", type=date.fromisoformat, help="window start (ISO)")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, help="window end (ISO)")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help="window length when --from is omitted (default: %(default)s)",
    )
    parser.add_argument(
        "--tolerance",
        type=int,
        default=DEFAULT_TOLERANCE_DAYS,
        help="days two copies may differ by (default: %(default)s)",
    )
    parser.add_argument(
        "--min-key-chars",
        type=int,
        default=DEFAULT_MIN_KEY_CHARS,
        help="shortest headline that can identify a story (default: %(default)s)",
    )
    parser.add_argument(
        "--status",
        action="append",
        default=[],
        help="restrict to a status; repeatable (default: every status)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLES,
        help="example clusters to print (default: %(default)s)",
    )
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--base-url", default=None, help="overrides DIRECTUS_BASE_URL")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--source-field", default=DEFAULT_SOURCE_FIELD)
    parser.add_argument("--date-field", default=DEFAULT_DATE_FIELD)
    parser.add_argument("--headline-field", default=DEFAULT_HEADLINE_FIELD)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).with_name(".env"))
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")
    if args.tolerance < 0:
        parser.error("--tolerance cannot be negative")
    if args.page_size < 1:
        parser.error("--page-size must be at least 1")
    if args.start and args.end and args.end < args.start:
        parser.error("--to precedes --from")
    return args


def resolve_credentials(args: argparse.Namespace) -> tuple[str, str]:
    load_dotenv(args.env_file)
    base_url = args.base_url or os.environ.get("DIRECTUS_BASE_URL") or DEFAULT_BASE_URL
    # copertine's own deployment names the variable DIRECTUS_API_TOKEN; accept
    # both rather than making anyone rename a secret to run a diagnostic.
    token = os.environ.get("DIRECTUS_API_KEY") or os.environ.get("DIRECTUS_API_TOKEN")
    if not token:
        raise ProbeError(
            f"set DIRECTUS_API_KEY (or DIRECTUS_API_TOKEN), in the environment or in {args.env_file}"
        )
    return base_url, token


def resolve_window(args: argparse.Namespace, spans: Sequence[SourceSpan]) -> tuple[date, date]:
    if args.start and args.end:
        return (args.start, args.end)
    if args.start:
        return (args.start, args.start + timedelta(days=args.days - 1))
    if args.end:
        return (args.end - timedelta(days=args.days - 1), args.end)
    window = overlap_window(spans, args.days)
    if window is None:
        raise ProbeError("this archive has fewer than two dated imports; pass --from/--to")
    return window


async def run(args: argparse.Namespace) -> str:
    base_url, token = resolve_credentials(args)
    probe = Probe(
        base_url=base_url,
        collection=args.collection,
        source_field=args.source_field,
        date_field=args.date_field,
        headline_field=args.headline_field,
        statuses=tuple(args.status),
    )
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(60.0),
    ) as client:
        spans = await fetch_source_spans(client, probe)
        matrix = await fetch_year_matrix(client, probe)
        start, end = resolve_window(args, spans)
        if not args.json:
            print(f"scanning {start} -> {end} ...", file=sys.stderr)
        rows = tuple(
            [row async for row in iter_rows(client, probe, start, end, page_size=args.page_size)]
        )

    clusters = build_clusters(rows, tolerance_days=args.tolerance, min_key_chars=args.min_key_chars)
    stats = summarise(rows, clusters)
    pairs = pair_counts(clusters)
    if args.json:
        import json

        return json.dumps(
            report_as_json(spans, matrix, (start, end), stats, pairs, clusters, args.samples),
            indent=2,
            ensure_ascii=False,
        )
    return render_report(spans, matrix, (start, end), stats, pairs, clusters, args.samples)


def main() -> None:
    args = parse_args()
    try:
        print(asyncio.run(run(args)))
    except ProbeError as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
