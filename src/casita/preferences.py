"""Structured preference state for conversational listing search.

The language model may propose changes to this state, but the state itself is
plain, typed data. Search tools consume ``PreferenceProfile`` rather than raw
conversation text so hard constraints remain inspectable and deterministic.
"""

from typing import Literal

from pydantic import BaseModel, Field


DogRequirement = Literal["any", "dogs_ok", "large_ok"]
RouteCategory = Literal["trail", "beach", "bakery", "downtown"]
PlaceCategory = Literal["veterinary", "emergency_vet", "dog_park"]
PreferenceField = Literal[
    "max_price",
    "min_beds",
    "min_baths",
    "neighborhoods",
    "dog_requirement",
    "yard_required",
    "parking_required",
    "laundry_required",
    "preferred_features",
    "route_preferences",
    "place_preferences",
]


class RoutePreference(BaseModel):
    category: RouteCategory
    max_minutes: int | None = Field(default=None, gt=0)


class PlacePreference(BaseModel):
    category: PlaceCategory
    max_distance_km: float | None = Field(default=None, gt=0)


class PreferenceProfile(BaseModel):
    """The current, reviewable interpretation of a user's preferences."""

    max_price: int | None = Field(default=None, gt=0)
    min_beds: float | None = Field(default=None, ge=0)
    min_baths: float | None = Field(default=None, ge=0)
    neighborhoods: list[str] = Field(default_factory=list)
    dog_requirement: DogRequirement = "any"
    yard_required: bool = False
    parking_required: bool = False
    laundry_required: bool = False
    preferred_features: list[str] = Field(default_factory=list)
    route_preferences: list[RoutePreference] = Field(default_factory=list)
    place_preferences: list[PlacePreference] = Field(default_factory=list)


class PreferenceUpdate(BaseModel):
    """A partial change extracted from one conversation turn.

    ``None`` means "leave the current value alone". ``clear`` explicitly
    removes constraints when the user says something like "budget no longer
    matters" or "any neighborhood is fine".
    """

    max_price: int | None = Field(default=None, gt=0)
    min_beds: float | None = Field(default=None, ge=0)
    min_baths: float | None = Field(default=None, ge=0)
    neighborhoods: list[str] | None = None
    dog_requirement: DogRequirement | None = None
    yard_required: bool | None = None
    parking_required: bool | None = None
    laundry_required: bool | None = None
    preferred_features: list[str] | None = None
    route_preferences: list[RoutePreference] | None = None
    place_preferences: list[PlacePreference] | None = None
    clear: list[PreferenceField] = Field(default_factory=list)
    unsupported_requests: list[str] = Field(default_factory=list)


_DEFAULTS = PreferenceProfile()


def apply_update(
    profile: PreferenceProfile,
    update: PreferenceUpdate,
) -> PreferenceProfile:
    """Return a new profile with one conversational update applied."""

    values = profile.model_dump()
    for field in PreferenceProfile.model_fields:
        value = getattr(update, field)
        if value is not None:
            values[field] = value

    defaults = _DEFAULTS.model_dump()
    for field in update.clear:
        values[field] = defaults[field]

    return PreferenceProfile.model_validate(values)
