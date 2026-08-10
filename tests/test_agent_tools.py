from casita.agent_tools import RouteContext, compare_listings, search_listings
from casita.models import Listing
from casita.places import Place
from casita.preferences import PlacePreference, PreferenceProfile, RoutePreference


def _listing(source_id: str, **values) -> Listing:
    return Listing(
        source="manual",
        source_id=source_id,
        url=f"https://example.test/{source_id}",
        **values,
    )


def test_search_applies_hard_constraints_without_guessing_missing_values():
    listings = [
        _listing("match", price=4800, beds=2, neighborhood="Inner Richmond"),
        _listing("expensive", price=5200, beds=2, neighborhood="Inner Richmond"),
        _listing("unknown-price", price=None, beds=2, neighborhood="Inner Richmond"),
    ]
    profile = PreferenceProfile(
        max_price=5000,
        min_beds=2,
        neighborhoods=["Inner Richmond"],
    )

    results = search_listings(listings, profile)

    assert [match.listing.key for match in results.matches] == ["manual:match"]
    assert results.total_considered == 3
    assert results.total_matched == 1


def test_search_excludes_incompatible_dog_policies_and_marks_ambiguity():
    listings = [
        _listing("large", price=4800, dog_policy="large_ok"),
        _listing("unspecified", price=4600, dog_policy="dogs_ok"),
        _listing("small", price=4400, dog_policy="small_only"),
    ]

    results = search_listings(
        listings,
        PreferenceProfile(dog_requirement="large_ok"),
    )

    assert [match.listing.key for match in results.matches] == [
        "manual:large",
        "manual:unspecified",
    ]
    assert results.matches[1].unknown_preferences == ["large dog approval"]


def test_search_uses_soft_preferences_to_order_matches():
    listings = [
        _listing("no-yard", price=4500, has_yard=False),
        _listing("yard", price=4700, has_yard=True),
    ]
    profile = PreferenceProfile(preferred_features=["outdoor space"])

    results = search_listings(listings, profile)

    assert [match.listing.key for match in results.matches] == [
        "manual:yard",
        "manual:no-yard",
    ]
    assert results.matches[0].matched_preferences == ["outdoor space"]


def test_search_marks_soft_preferences_unknown_instead_of_inventing_them():
    listing = _listing("unknown", price=4500, light_quality=None)

    results = search_listings(
        [listing],
        PreferenceProfile(preferred_features=["natural light"]),
    )

    assert results.matches[0].unknown_preferences == ["natural light"]


def test_compare_returns_known_facts_and_reports_unknown_keys():
    listing = _listing("one", price=4500, beds=2, dog_policy="dogs_ok")

    comparison = compare_listings([listing], ["manual:one", "manual:missing"])

    assert comparison.listings[0].price == 4500
    assert comparison.listings[0].dog_policy == "dogs_ok"
    assert comparison.missing_keys == ["manual:missing"]


def test_search_enforces_route_time_and_returns_route_evidence():
    listings = [
        _listing("near", price=4800, lat=37.78, lng=-122.46),
        _listing("far", price=4700, lat=37.76, lng=-122.50),
    ]
    from casita.walk import TRAILS

    anchor = TRAILS[0]
    routes = RouteContext(
        walk_map={
            ("manual:near", anchor.name): 10,
            ("manual:far", anchor.name): 25,
        },
        drive_map={},
    )
    profile = PreferenceProfile(
        route_preferences=[RoutePreference(category="trail", max_minutes=15)]
    )

    results = search_listings(listings, profile, route_context=routes)

    assert [match.listing.key for match in results.matches] == ["manual:near"]
    assert results.matches[0].route_evidence[0].minutes == 10
    assert results.matches[0].route_evidence[0].anchor == anchor.short


def test_search_ranks_and_explains_nearby_pet_support():
    listings = [
        _listing("near-vet", price=4800, lat=37.78, lng=-122.46),
        _listing("far-vet", price=4700, lat=37.70, lng=-122.50),
    ]
    place = Place(
        id="test:vet",
        name="Test Emergency Vet",
        category="emergency_vet",
        lat=37.781,
        lng=-122.46,
        source_url="https://example.test/vet",
    )
    profile = PreferenceProfile(
        place_preferences=[PlacePreference(category="emergency_vet")]
    )

    results = search_listings(listings, profile, places=[place])

    assert results.matches[0].listing.key == "manual:near-vet"
    assert results.matches[0].place_evidence[0].name == "Test Emergency Vet"
    assert results.matches[0].place_evidence[0].distance_km < 1
