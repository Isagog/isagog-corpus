"""List "author — title" for every article in the newest edition.

Titles print as OSC 8 terminal hyperlinks, underlined so they read as links
even in terminals that add no styling of their own (Ghostty). Terminals that
do not understand OSC 8 just show the plain text.

How to follow a link depends on the terminal: iTerm2 and kitty open it on
cmd-click, Ghostty needs cmd held *while moving the mouse over* the title --
it only resolves a link on modifier-hover, and pressing cmd without moving
does nothing.

Usage:
    cp examples/.env.example examples/.env   # then fill in a real API key
    uv run --extra examples python examples/latest_edition_authors.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from corpus import Capability, CorpusRequirements, EditionQuery
from corpus_directus import DirectusCorpus, DirectusCorpusSettings
from dotenv import load_dotenv
from pydantic import SecretStr

ARTICLE_URL_TEMPLATE = "https://ilmanifesto.it/{slug}"

REQUIREMENTS = CorpusRequirements(required=frozenset({Capability.EDITIONS, Capability.ARTICLES}))

# Editions publish daily; a couple of weeks is enough to always catch the
# newest one without listing the entire archive.
LOOKBACK_DAYS = 14


# OSC 8 carries the target; the SGR underline is what makes the title *look*
# clickable. Ghostty renders OSC 8 text identically to plain text otherwise,
# so without this there is nothing to tell a reader a link is there at all.
UNDERLINE_ON = "\033[4m"
UNDERLINE_OFF = "\033[24m"


def _hyperlink(url: str, text: str) -> str:
    return f"\033]8;;{url}\033\\{UNDERLINE_ON}{text}{UNDERLINE_OFF}\033]8;;\033\\"


async def main() -> None:
    load_dotenv(Path(__file__).with_name(".env"))

    try:
        base_url = os.environ["DIRECTUS_BASE_URL"]
        api_key = os.environ["DIRECTUS_API_KEY"]
    except KeyError as exc:
        sys.exit(f"missing required env var: {exc}")

    settings = DirectusCorpusSettings(base_url=base_url, api_key=SecretStr(api_key))
    corpus = DirectusCorpus.from_settings(settings)

    try:
        corpus.require(REQUIREMENTS)  # fail fast, names the gap
        await corpus.ping()  # fail fast, auth/connectivity

        today = date.today()
        refs = await corpus.list_editions(
            EditionQuery(date_from=today - timedelta(days=LOOKBACK_DAYS), date_to=today)
        )
        if not refs:
            sys.exit(f"no editions found in the last {LOOKBACK_DAYS} days")
        newest = max(refs, key=lambda ref: ref.date)

        edition = await corpus.get_edition(newest.id)
        print(f"Edition {edition.date} (id={edition.id}) — {len(edition.articles)} article(s)\n")

        for article in edition.articles:
            author = article.author or "Unknown"
            url = ARTICLE_URL_TEMPLATE.format(slug=article.slug)
            print(f"{author} — {_hyperlink(url, article.headline)} (id={article.id})")
    finally:
        await corpus.aclose()


if __name__ == "__main__":
    asyncio.run(main())
