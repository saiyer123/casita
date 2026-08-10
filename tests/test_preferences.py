import pytest
from pydantic import ValidationError

from casita.preferences import (
    PreferenceProfile,
    PreferenceUpdate,
    PlacePreference,
    RoutePreference,
    apply_update,
)


def test_profile_defaults_leave_search_open():
    profile = PreferenceProfile()

    assert profile.max_price is None
    assert profile.neighborhoods == []
    assert profile.dog_requirement == "any"
    assert profile.yard_required is False


def test_apply_update_preserves_preferences_from_prior_turns():
    profile = PreferenceProfile(max_price=5000, min_beds=2)
    update = PreferenceUpdate(
        neighborhoods=["Inner Richmond", "Presidio"],
        dog_requirement="large_ok",
    )

    updated = apply_update(profile, update)

    assert updated.max_price == 5000
    assert updated.min_beds == 2
    assert updated.neighborhoods == ["Inner Richmond", "Presidio"]
    assert updated.dog_requirement == "large_ok"


def test_apply_update_can_relax_a_prior_constraint():
    profile = PreferenceProfile(
        max_price=5000,
        neighborhoods=["Inner Richmond"],
        yard_required=True,
    )
    update = PreferenceUpdate(clear=["max_price", "neighborhoods"])

    updated = apply_update(profile, update)

    assert updated.max_price is None
    assert updated.neighborhoods == []
    assert updated.yard_required is True


def test_profile_rejects_impossible_numeric_constraints():
    with pytest.raises(ValidationError):
        PreferenceProfile(max_price=-1)


def test_route_preferences_persist_across_other_updates():
    profile = PreferenceProfile(
        route_preferences=[RoutePreference(category="trail", max_minutes=15)]
    )

    updated = apply_update(profile, PreferenceUpdate(max_price=5000))

    assert updated.route_preferences == [
        RoutePreference(category="trail", max_minutes=15)
    ]


def test_place_preferences_are_typed_and_persisted():
    profile = PreferenceProfile(
        place_preferences=[PlacePreference(category="emergency_vet", max_distance_km=5)]
    )

    updated = apply_update(profile, PreferenceUpdate(min_beds=2))

    assert updated.place_preferences[0].category == "emergency_vet"
    assert updated.place_preferences[0].max_distance_km == 5
