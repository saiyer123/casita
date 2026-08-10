"""Fail-closed live inventory refresh for conversational search.

Live mode reuses Casita's existing source search adapters. A source only
contributes listings when its current search returns results; blocked or
failed sources never fall back to the historical demo fixture.
"""

import sqlite3
from collections.abc import Awaitable, Callable

from playwright.async_api import BrowserContext

from . import craigslist, dedup, redfin, storage, zillow, zumper
from .browser import context
from .models import Listing


SourceScraper = Callable[[BrowserContext], Awaitable[list[Listing]]]


def normalize_live_listing(listing: Listing) -> Listing:
    """Fill only facts guaranteed by a source's configured search filter."""

    if (
        listing.source in {"craigslist", "zumper", "redfin"}
        and listing.pets_allowed is True
        and listing.dog_policy is None
    ):
        listing.dog_policy = "dogs_ok"
    return listing


async def scrape_live_inventory(
    *,
    headless: bool = True,
) -> tuple[list[Listing], list[str]]:
    """Return listings observed in current rental searches and successful sources."""

    listings: list[Listing] = []
    succeeded: list[str] = []
    scrapers: tuple[tuple[str, SourceScraper], ...] = (
        ("zillow", zillow.scrape_all),
        ("craigslist", craigslist.scrape),
        ("zumper", zumper.scrape_all),
        ("redfin", redfin.scrape_all),
    )

    async with context(headless=headless, persistent=True) as browser_context:
        for source, scraper in scrapers:
            print(f"live refresh: {source}…")
            try:
                found = await scraper(browser_context)
            except Exception as exc:
                print(f"live refresh: {source} failed: {exc}")
                continue
            if not found:
                print(f"live refresh: {source} returned no verifiable listings")
                continue
            succeeded.append(source)
            listings.extend(normalize_live_listing(listing) for listing in found)

    by_key = {listing.key: listing for listing in listings}
    return dedup.dedupe(list(by_key.values())), succeeded


def replace_active_inventory(
    conn: sqlite3.Connection,
    listings: list[Listing],
    succeeded_sources: list[str],
) -> tuple[int, int]:
    """Replace active rows with one live refresh, never retaining stale rows."""

    if not listings or not succeeded_sources:
        raise ValueError("live refresh returned no verifiable listings")
    conn.execute("UPDATE listings SET active = 0")
    return storage.upsert_run(
        conn,
        listings,
        succeeded_sources=succeeded_sources,
    )
