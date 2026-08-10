"""Deterministic tools exposed to the conversational Casita agent."""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from .models import Listing
from .places import Place, nearest_place
from .preferences import PlacePreference, PreferenceProfile, RoutePreference
from .rank import score as baseline_score
from .storage import active_listings


class ListingFacts(BaseModel):
    """A compact, evidence-friendly view of a listing."""

    key: str
    address: str | None
    neighborhood: str | None
    price: int | None
    beds: float | None
    baths: float | None
    dog_policy: str | None
    has_yard: bool | None
    parking: str | None
    laundry: str | None
    light_quality: str | None
    view_quality: str | None
    url: str

    @classmethod
    def from_listing(cls, listing: Listing) -> "ListingFacts":
        return cls(
            key=listing.key,
            address=listing.address,
            neighborhood=listing.hood,
            price=listing.price,
            beds=listing.beds,
            baths=listing.baths,
            dog_policy=listing.dog_policy,
            has_yard=listing.has_yard,
            parking=listing.parking,
            laundry=listing.laundry,
            light_quality=listing.light_quality,
            view_quality=listing.view_quality,
            url=listing.url,
        )


class ListingMatch(BaseModel):
    listing: ListingFacts
    score: int
    matched_preferences: list[str] = Field(default_factory=list)
    unknown_preferences: list[str] = Field(default_factory=list)
    route_evidence: list["RouteFact"] = Field(default_factory=list)
    place_evidence: list["PlaceFact"] = Field(default_factory=list)


class RouteFact(BaseModel):
    category: str
    anchor: str
    minutes: int
    mode: Literal["walk", "drive"]


class PlaceFact(BaseModel):
    category: str
    name: str
    distance_km: float
    source_url: str
    website: str | None = None


@dataclass(frozen=True)
class RouteContext:
    walk_map: dict[tuple[str, str], int]
    drive_map: dict[tuple[str, str], int]


class SearchResults(BaseModel):
    matches: list[ListingMatch]
    total_considered: int
    total_matched: int


class ComparisonResults(BaseModel):
    listings: list[ListingFacts]
    missing_keys: list[str] = Field(default_factory=list)


def _has_parking(listing: Listing) -> bool:
    parking = (listing.parking or "").strip().lower()
    return bool(parking and parking not in {"none", "no parking"})


def _has_laundry(listing: Listing) -> bool:
    laundry = (listing.laundry or "").strip().lower()
    return bool(laundry and laundry != "none")


def _matches_hard_constraints(
    listing: Listing,
    profile: PreferenceProfile,
    route_context: RouteContext | None = None,
    places: list[Place] | None = None,
) -> bool:
    if profile.max_price is not None:
        if listing.price is None or listing.price > profile.max_price:
            return False
    if profile.min_beds is not None:
        if listing.beds is None or listing.beds < profile.min_beds:
            return False
    if profile.min_baths is not None:
        if listing.baths is None or listing.baths < profile.min_baths:
            return False
    if profile.neighborhoods:
        location = f"{listing.hood or ''} {listing.address or ''}".casefold()
        if not any(name.casefold() in location for name in profile.neighborhoods):
            return False
    if profile.dog_requirement == "large_ok":
        if listing.dog_policy not in {"large_ok", "dogs_ok"}:
            return False
    if profile.dog_requirement == "dogs_ok":
        if listing.dog_policy not in {"large_ok", "dogs_ok", "small_only"}:
            return False
    if profile.yard_required and listing.has_yard is not True:
        return False
    if profile.parking_required and not _has_parking(listing):
        return False
    if profile.laundry_required and not _has_laundry(listing):
        return False
    for preference in profile.route_preferences:
        if preference.max_minutes is None:
            continue
        fact = _route_fact(listing, preference, route_context)
        if fact is None or fact.minutes > preference.max_minutes:
            return False
    for preference in profile.place_preferences:
        if preference.max_distance_km is None:
            continue
        fact = _place_fact(listing, preference, places or [])
        if fact is None or fact.distance_km > preference.max_distance_km:
            return False
    return True


def _route_anchors(category: str):
    from .walk import BAKERIES, BEACHES, SF_CENTER, TRAILS

    return {
        "trail": TRAILS,
        "beach": BEACHES,
        "bakery": BAKERIES,
        "downtown": SF_CENTER,
    }[category]


def _route_fact(
    listing: Listing,
    preference: RoutePreference,
    route_context: RouteContext | None,
) -> RouteFact | None:
    if route_context is None:
        return None
    from .walk import is_marin, nearest

    mode: Literal["walk", "drive"] = "drive" if is_marin(listing) else "walk"
    route_map = route_context.drive_map if mode == "drive" else route_context.walk_map
    result = nearest(route_map, listing.key, _route_anchors(preference.category))
    if result is None:
        return None
    anchor, minutes = result
    return RouteFact(
        category=preference.category,
        anchor=anchor.short,
        minutes=minutes,
        mode=mode,
    )


def _place_fact(
    listing: Listing,
    preference: PlacePreference,
    places: list[Place],
) -> PlaceFact | None:
    if listing.lat is None or listing.lng is None:
        return None
    result = nearest_place(listing.lat, listing.lng, preference.category, places)
    if result is None:
        return None
    place, distance = result
    return PlaceFact(
        category=preference.category,
        name=place.name,
        distance_km=round(distance, 1),
        source_url=place.source_url,
        website=place.website,
    )


def _feature_match(listing: Listing, feature: str) -> bool | None:
    normalized = feature.casefold().strip()
    if any(word in normalized for word in ("yard", "outdoor", "garden", "patio")):
        return listing.has_yard
    if any(word in normalized for word in ("parking", "garage")):
        return _has_parking(listing) if listing.parking is not None else None
    if any(word in normalized for word in ("laundry", "washer", "dryer")):
        return _has_laundry(listing) if listing.laundry is not None else None
    if any(word in normalized for word in ("light", "sunny", "bright")):
        if listing.light_quality is None:
            return None
        return listing.light_quality in {"abundant", "moderate"}
    if "view" in normalized:
        if listing.view_quality is None:
            return None
        return listing.view_quality in {"panoramic", "open"}

    corpus = " ".join(
        value
        for value in (
            listing.description,
            listing.yard_note,
            listing.visual_summary,
            listing.other_visible,
        )
        if value
    ).casefold()
    if not corpus:
        return None
    return normalized in corpus


def _preference_evidence(
    listing: Listing,
    profile: PreferenceProfile,
    route_context: RouteContext | None = None,
    places: list[Place] | None = None,
) -> tuple[int, list[str], list[str], list[RouteFact], list[PlaceFact]]:
    preference_score = 0
    matched: list[str] = []
    unknown: list[str] = []
    route_evidence: list[RouteFact] = []
    place_evidence: list[PlaceFact] = []
    if profile.dog_requirement == "large_ok" and listing.dog_policy == "dogs_ok":
        unknown.append("large dog approval")
    for feature in profile.preferred_features:
        result = _feature_match(listing, feature)
        if result is True:
            preference_score += 10
            matched.append(feature)
        elif result is None:
            unknown.append(feature)
    for preference in profile.route_preferences:
        fact = _route_fact(listing, preference, route_context)
        if fact is None:
            unknown.append(f"{preference.category} travel time")
            continue
        route_evidence.append(fact)
        matched.append(preference.category)
        if fact.minutes <= 10:
            preference_score += 15
        elif fact.minutes <= 20:
            preference_score += 10
        elif fact.minutes <= 30:
            preference_score += 5
    for preference in profile.place_preferences:
        fact = _place_fact(listing, preference, places or [])
        if fact is None:
            unknown.append(f"nearby {preference.category.replace('_', ' ')}")
            continue
        place_evidence.append(fact)
        matched.append(preference.category)
        if fact.distance_km <= 1:
            preference_score += 15
        elif fact.distance_km <= 3:
            preference_score += 10
        elif fact.distance_km <= 5:
            preference_score += 5
    return preference_score, matched, unknown, route_evidence, place_evidence


def search_listings(
    listings: Iterable[Listing],
    profile: PreferenceProfile,
    *,
    limit: int = 5,
    route_context: RouteContext | None = None,
    places: list[Place] | None = None,
) -> SearchResults:
    """Filter listings by hard constraints, then rank by stated preferences."""

    candidates = list(listings)
    ranked: list[tuple[int, ListingMatch]] = []
    for listing in candidates:
        if not _matches_hard_constraints(listing, profile, route_context, places):
            continue
        preference_score, matched, unknown, routes, nearby = _preference_evidence(
            listing,
            profile,
            route_context,
            places,
        )
        total_score = baseline_score(listing) + preference_score
        ranked.append((
            total_score,
            ListingMatch(
                listing=ListingFacts.from_listing(listing),
                score=total_score,
                matched_preferences=matched,
                unknown_preferences=unknown,
                route_evidence=routes,
                place_evidence=nearby,
            ),
        ))

    ranked.sort(
        key=lambda item: (
            -item[0],
            item[1].listing.price is None,
            item[1].listing.price or 0,
            item[1].listing.key,
        )
    )
    matches = [item[1] for item in ranked]
    return SearchResults(
        matches=matches[:limit],
        total_considered=len(candidates),
        total_matched=len(matches),
    )


def search_active_listings(
    conn: sqlite3.Connection,
    profile: PreferenceProfile,
    *,
    limit: int = 5,
) -> SearchResults:
    listings = active_listings(conn)
    route_context = None
    if profile.route_preferences:
        from . import walk

        route_context = RouteContext(
            walk_map=walk.populate_for(listings),
            drive_map=walk.populate_drive_for_marin(listings),
        )
    place_data = None
    if profile.place_preferences:
        from .places import load_places

        place_data = load_places()
    return search_listings(
        listings,
        profile,
        limit=limit,
        route_context=route_context,
        places=place_data,
    )


def compare_listings(
    listings: Iterable[Listing],
    keys: list[str],
) -> ComparisonResults:
    by_key = {listing.key: listing for listing in listings}
    found = [ListingFacts.from_listing(by_key[key]) for key in keys if key in by_key]
    missing = [key for key in keys if key not in by_key]
    return ComparisonResults(listings=found, missing_keys=missing)


def compare_active_listings(
    conn: sqlite3.Connection,
    keys: list[str],
) -> ComparisonResults:
    return compare_listings(active_listings(conn), keys)
