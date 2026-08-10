"""Offline nearby-place data used by the conversational search tools."""

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


PlaceCategory = Literal["veterinary", "emergency_vet", "dog_park"]
DEFAULT_PLACES_FIXTURE = Path(__file__).parent / "fixtures" / "demo-places.json"


class Place(BaseModel):
    id: str
    name: str
    category: PlaceCategory
    lat: float
    lng: float
    source_url: str
    website: str | None = None
    note: str | None = None


def load_places(path: Path = DEFAULT_PLACES_FIXTURE) -> list[Place]:
    payload = json.loads(path.read_text())
    return [Place.model_validate(place) for place in payload["places"]]


def distance_km(
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
) -> float:
    """Return straight-line distance in kilometers."""

    radius_km = 6371.0
    dlat = math.radians(to_lat - from_lat)
    dlng = math.radians(to_lng - from_lng)
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(from_lat))
        * math.cos(math.radians(to_lat))
        * math.sin(dlng / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(value))


def nearest_place(
    lat: float,
    lng: float,
    category: PlaceCategory,
    places: list[Place],
) -> tuple[Place, float] | None:
    candidates = [place for place in places if place.category == category]
    if not candidates:
        return None
    return min(
        ((place, distance_km(lat, lng, place.lat, place.lng)) for place in candidates),
        key=lambda item: item[1],
    )
