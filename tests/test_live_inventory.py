import asyncio
import sqlite3
from contextlib import asynccontextmanager

import pytest

from casita import storage
from casita import live_inventory
from casita.live_inventory import normalize_live_listing, replace_active_inventory
from casita.models import Listing


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(storage.SCHEMA)
    storage._migrate(conn)
    return conn


def _listing(source: str, source_id: str, **values) -> Listing:
    return Listing(
        source=source,
        source_id=source_id,
        url=f"https://example.test/{source_id}",
        **values,
    )


def test_live_refresh_deactivates_every_stale_fixture_row():
    conn = _connection()
    storage.upsert_run(
        conn,
        [
            _listing("zillow", "stale", price=5000),
            _listing("craigslist", "also-stale", price=4900),
        ],
        succeeded_sources=["zillow", "craigslist"],
    )

    replace_active_inventory(
        conn,
        [_listing("craigslist", "current", price=4750, dog_policy="dogs_ok")],
        ["craigslist"],
    )

    active = conn.execute(
        "SELECT key, price FROM listings WHERE active = 1 ORDER BY key"
    ).fetchall()
    assert [(row["key"], row["price"]) for row in active] == [
        ("craigslist:current", 4750)
    ]


def test_live_refresh_refuses_to_serve_when_every_source_failed():
    conn = _connection()
    storage.upsert_run(
        conn,
        [_listing("zillow", "stale", price=5000)],
        succeeded_sources=["zillow"],
    )

    with pytest.raises(ValueError, match="no verifiable listings"):
        replace_active_inventory(conn, [], [])

    assert conn.execute(
        "SELECT active FROM listings WHERE key = 'zillow:stale'"
    ).fetchone()[0] == 1


def test_live_dog_filtered_sources_get_only_the_supported_baseline_policy():
    listing = _listing(
        "zumper",
        "current",
        price=4800,
        pets_allowed=True,
    )

    normalized = normalize_live_listing(listing)

    assert normalized.dog_policy == "dogs_ok"


def test_live_scrape_omits_failed_sources_and_keeps_successful_ones(monkeypatch):
    @asynccontextmanager
    async def fake_context(**kwargs):
        assert kwargs == {"headless": True, "persistent": True}
        yield object()

    async def failed_source(context):
        return []

    async def successful_source(context):
        return [
            _listing(
                "craigslist",
                "current",
                price=4750,
                pets_allowed=True,
            )
        ]

    monkeypatch.setattr(live_inventory, "context", fake_context)
    monkeypatch.setattr(live_inventory.zillow, "scrape_all", failed_source)
    monkeypatch.setattr(live_inventory.craigslist, "scrape", successful_source)
    monkeypatch.setattr(live_inventory.zumper, "scrape_all", failed_source)
    monkeypatch.setattr(live_inventory.redfin, "scrape_all", failed_source)

    listings, succeeded = asyncio.run(live_inventory.scrape_live_inventory())

    assert succeeded == ["craigslist"]
    assert [listing.key for listing in listings] == ["craigslist:current"]
    assert listings[0].dog_policy == "dogs_ok"
