import sqlite3

import pytest

from casita.agent import (
    CasitaAgent,
    ConversationState,
    GeminiInterpreter,
    RuleBasedInterpreter,
    TurnInterpretation,
)
from casita.models import Listing
from casita.preferences import PreferenceUpdate
from casita.storage import _listing_to_row
from casita.verifier import VerificationReport


def _connection(*listings: Listing) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from casita.storage import SCHEMA, _migrate

    conn.executescript(SCHEMA)
    _migrate(conn)
    for listing in listings:
        row = _listing_to_row(listing)
        columns = list(row) + ["first_seen", "last_seen", "active"]
        values = [*row.values(), "2026-01-01", "2026-01-01", 1]
        conn.execute(
            f"INSERT INTO listings ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            values,
        )
    conn.commit()
    return conn


def _listing(source_id: str, **values) -> Listing:
    return Listing(
        source="manual",
        source_id=source_id,
        url=f"https://example.test/{source_id}",
        **values,
    )


class StubInterpreter:
    def __init__(self, *plans: TurnInterpretation):
        self.plans = list(plans)

    def interpret(self, message: str, state: ConversationState) -> TurnInterpretation:
        return self.plans.pop(0)


class StubVerifier:
    def verify(self, answer, evidence):
        assert evidence.search_results is not None
        return VerificationReport(
            verdict="warn",
            unsupported_claims=["test claim"],
        )


def test_rule_interpreter_maps_common_language_to_a_preference_update():
    plan = RuleBasedInterpreter().interpret(
        "Find a 2 bedroom under $5k in Inner Richmond for two large dogs, preferably with a yard",
        ConversationState(),
    )

    assert plan.update.max_price == 5000
    assert plan.update.min_beds == 2
    assert plan.update.neighborhoods == ["Inner Richmond"]
    assert plan.update.dog_requirement == "large_ok"
    assert "yard" in (plan.update.preferred_features or [])


def test_rule_interpreter_parses_comma_separated_budget():
    plan = RuleBasedInterpreter().interpret(
        "Show me homes under $5,500",
        ConversationState(),
    )

    assert plan.update.max_price == 5500


def test_rule_interpreter_keeps_required_and_preferred_amenities_separate():
    plan = RuleBasedInterpreter().interpret(
        "I need a yard, but parking would be nice",
        ConversationState(),
    )

    assert plan.update.yard_required is True
    assert plan.update.parking_required is None
    assert "parking" in (plan.update.preferred_features or [])


def test_rule_interpreter_maps_route_language():
    plan = RuleBasedInterpreter().interpret(
        "Find homes within a 15 minute walk of a trail",
        ConversationState(),
    )

    assert plan.update.route_preferences is not None
    assert plan.update.route_preferences[0].category == "trail"
    assert plan.update.route_preferences[0].max_minutes == 15


def test_rule_interpreter_maps_nearby_emergency_vet_language():
    plan = RuleBasedInterpreter().interpret(
        "Find a home within 2 miles of a 24 hour emergency vet",
        ConversationState(),
    )

    assert plan.update.place_preferences is not None
    assert plan.update.place_preferences[0].category == "emergency_vet"
    assert plan.update.place_preferences[0].max_distance_km == pytest.approx(3.21868)


def test_rule_interpreter_marks_pet_walkers_unsupported():
    plan = RuleBasedInterpreter().interpret(
        "Are there dog walkers nearby?",
        ConversationState(),
    )

    assert plan.update.unsupported_requests == ["pet walker availability"]


def test_gemini_interpreter_uses_the_typed_llm_contract(monkeypatch):
    expected = TurnInterpretation(
        update=PreferenceUpdate(max_price=5100, min_beds=2),
    )

    def fake_interpret(prompt, schema):
        assert '\"message\": \"find me something roomy\"' in prompt
        assert schema is TurnInterpretation
        return expected

    monkeypatch.setattr("casita.llm.interpret_agent_turn", fake_interpret)

    result = GeminiInterpreter().interpret(
        "find me something roomy",
        ConversationState(),
    )

    assert result == expected


def test_agent_preserves_preferences_across_search_turns():
    conn = _connection(
        _listing("one", address="One St", price=4800, beds=2, dog_policy="dogs_ok", has_yard=True),
        _listing("two", address="Two St", price=5200, beds=2, dog_policy="dogs_ok", has_yard=True),
    )
    interpreter = StubInterpreter(
        TurnInterpretation(update=PreferenceUpdate(max_price=5000)),
        TurnInterpretation(update=PreferenceUpdate(yard_required=True)),
    )
    agent = CasitaAgent(conn, interpreter)

    first = agent.respond("under 5000")
    second = agent.respond("I also need a yard")

    assert first.search_results is not None
    assert first.search_results.total_matched == 1
    assert second.state.profile.max_price == 5000
    assert second.state.profile.yard_required is True
    assert second.search_results is not None
    assert second.search_results.matches[0].listing.key == "manual:one"


def test_agent_compares_the_previous_top_results():
    conn = _connection(
        _listing("one", address="One St", price=4800),
        _listing("two", address="Two St", price=4900),
    )
    interpreter = StubInterpreter(
        TurnInterpretation(intent="search"),
        TurnInterpretation(intent="compare"),
    )
    agent = CasitaAgent(conn, interpreter)

    agent.respond("show homes")
    comparison = agent.respond("compare them")

    assert comparison.comparison_results is not None
    assert len(comparison.comparison_results.listings) == 2
    assert "One St" in comparison.message
    assert "Two St" in comparison.message


def test_agent_declines_unsupported_request_without_searching():
    conn = _connection(_listing("one", price=4800))
    agent = CasitaAgent(conn, RuleBasedInterpreter())

    response = agent.respond("Is this neighborhood safe at night?")

    assert response.search_results is None
    assert "cannot verify neighborhood safety" in response.message


def test_optional_verifier_annotates_a_grounded_search_response():
    conn = _connection(_listing("one", address="One St", price=4800))
    agent = CasitaAgent(conn, RuleBasedInterpreter(), verifier=StubVerifier())

    response = agent.respond("Show homes")

    assert response.verification is not None
    assert response.verification.verdict == "warn"
    assert "Verifier warning: test claim" in response.message
