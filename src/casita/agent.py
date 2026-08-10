"""Single-agent conversational search over Casita's deterministic tools."""

import json
import re
import sqlite3
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .agent_tools import (
    ComparisonResults,
    ListingFacts,
    SearchResults,
    compare_active_listings,
    search_active_listings,
)
from .locations import MARIN_CITY_NAMES, SF_NEIGHBORHOOD_NAMES
from .preferences import (
    PlacePreference,
    PreferenceProfile,
    PreferenceUpdate,
    RoutePreference,
    apply_update,
)
from .verifier import EvidenceBundle, ResponseVerifier, VerificationReport


Intent = Literal["search", "compare", "details", "show_preferences", "help", "exit"]
DataMode = Literal["snapshot", "live"]


class TurnInterpretation(BaseModel):
    """The structured plan produced from one user message."""

    intent: Intent = "search"
    update: PreferenceUpdate = Field(default_factory=PreferenceUpdate)
    listing_keys: list[str] = Field(default_factory=list)
    clarification: str | None = None


class ConversationState(BaseModel):
    profile: PreferenceProfile = Field(default_factory=PreferenceProfile)
    last_result_keys: list[str] = Field(default_factory=list)


class AgentResponse(BaseModel):
    message: str
    state: ConversationState
    search_results: SearchResults | None = None
    comparison_results: ComparisonResults | None = None
    verification: VerificationReport | None = None
    should_exit: bool = False


class Interpreter(Protocol):
    def interpret(
        self,
        message: str,
        state: ConversationState,
    ) -> TurnInterpretation: ...


_KNOWN_LOCATIONS = (
    *SF_NEIGHBORHOOD_NAMES,
    *MARIN_CITY_NAMES,
    "Central Richmond",
    "Central Sunset",
    "Presidio",
    "Parkside",
    "Golden Gate Heights",
    "Laurel Heights",
)


def _money_value(match: re.Match[str]) -> int:
    raw = match.group("amount").replace(",", "")
    value = float(raw)
    if (match.groupdict().get("suffix") or "").casefold() == "k":
        value *= 1000
    return int(value)


class RuleBasedInterpreter:
    """Credentials-free interpreter for common rental-search language.

    The optional LLM interpreter covers broader phrasing. This fallback keeps
    the public demo useful and makes the agent loop fully testable offline.
    """

    _PRICE = re.compile(
        r"(?:under|below|less than|up to|max(?:imum)?|budget(?: of| is|:)?)[\s$]*"
        r"(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<suffix>k)?\b",
        re.IGNORECASE,
    )
    _BEDS = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?:bed|bedroom)s?\b", re.IGNORECASE)
    _BATHS = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?:bath|bathroom)s?\b", re.IGNORECASE)
    _KEY = re.compile(r"\b(?:zillow|craigslist|zumper|redfin|manual):[\w.-]+\b", re.IGNORECASE)

    def interpret(self, message: str, state: ConversationState) -> TurnInterpretation:
        text = message.strip()
        lowered = text.casefold()

        if lowered in {"exit", "quit", "bye"}:
            return TurnInterpretation(intent="exit")
        if lowered in {"help", "?"}:
            return TurnInterpretation(intent="help")
        if "preference" in lowered and any(word in lowered for word in ("show", "what", "current")):
            return TurnInterpretation(intent="show_preferences")

        keys = [key.casefold() for key in self._KEY.findall(text)]
        if "compare" in lowered:
            return TurnInterpretation(intent="compare", listing_keys=keys)
        if any(phrase in lowered for phrase in ("details", "tell me about", "what do we know")):
            return TurnInterpretation(intent="details", listing_keys=keys)

        values: dict = {}
        clear: list[str] = []
        preferred_features: list[str] = []

        price = self._PRICE.search(text)
        if price:
            values["max_price"] = _money_value(price)
        if any(phrase in lowered for phrase in ("no budget", "any budget", "remove the budget")):
            clear.append("max_price")

        beds = self._BEDS.search(text)
        if beds:
            values["min_beds"] = float(beds.group("value"))
        baths = self._BATHS.search(text)
        if baths:
            values["min_baths"] = float(baths.group("value"))

        neighborhoods = [name for name in _KNOWN_LOCATIONS if name.casefold() in lowered]
        if neighborhoods:
            values["neighborhoods"] = neighborhoods
        if any(phrase in lowered for phrase in ("any neighborhood", "anywhere is fine", "remove location")):
            clear.append("neighborhoods")

        if re.search(r"\b(?:large|big) dogs?\b", lowered):
            values["dog_requirement"] = "large_ok"
        elif re.search(r"\bdogs?\b", lowered):
            values["dog_requirement"] = "dogs_ok"
        if any(phrase in lowered for phrase in ("no dog requirement", "dogs no longer matter")):
            clear.append("dog_requirement")

        self._extract_amenity(
            lowered,
            name="yard",
            synonyms=("yard", "outdoor space", "garden", "patio"),
            required_field="yard_required",
            values=values,
            clear=clear,
            preferred_features=preferred_features,
        )
        self._extract_amenity(
            lowered,
            name="parking",
            synonyms=("parking", "garage"),
            required_field="parking_required",
            values=values,
            clear=clear,
            preferred_features=preferred_features,
        )
        self._extract_amenity(
            lowered,
            name="laundry",
            synonyms=("laundry", "washer", "dryer"),
            required_field="laundry_required",
            values=values,
            clear=clear,
            preferred_features=preferred_features,
        )
        for name, synonyms in {
            "natural light": ("natural light", "sunny", "bright"),
            "view": ("view", "views"),
        }.items():
            if any(word in lowered for word in synonyms):
                preferred_features.append(name)

        existing_features = state.profile.preferred_features
        if preferred_features:
            values["preferred_features"] = list(dict.fromkeys([
                *existing_features,
                *preferred_features,
            ]))

        route_preferences = list(state.profile.route_preferences)
        route_terms = {
            "trail": ("trail", "trailhead", "presidio"),
            "beach": ("beach", "ocean"),
            "bakery": ("bakery", "pastry", "croissant"),
            "downtown": ("downtown", "ferry building", "embarcadero", "commute"),
        }
        minutes_match = re.search(
            r"(?:within|under|less than)?\s*(?:a\s+)?(\d+)\s*(?:minute|min)s?",
            lowered,
        )
        for category, terms in route_terms.items():
            if not any(term in lowered for term in terms):
                continue
            route_preferences = [
                preference
                for preference in route_preferences
                if preference.category != category
            ]
            route_preferences.append(RoutePreference(
                category=category,
                max_minutes=int(minutes_match.group(1)) if minutes_match else None,
            ))
        if route_preferences != state.profile.route_preferences:
            values["route_preferences"] = route_preferences

        place_preferences = list(state.profile.place_preferences)
        place_category = None
        if any(term in lowered for term in ("emergency vet", "emergency hospital", "animal hospital")):
            place_category = "emergency_vet"
        elif any(term in lowered for term in ("dog park", "off-leash", "dog run")):
            place_category = "dog_park"
        elif any(term in lowered for term in ("veterinary", "veterinarian", " vet ", "vet near")):
            place_category = "veterinary"
        if place_category:
            distance_match = re.search(
                r"within\s+(\d+(?:\.\d+)?)\s*(mile|miles|mi|kilometer|kilometers|km)",
                lowered,
            )
            max_distance_km = None
            if distance_match:
                max_distance_km = float(distance_match.group(1))
                if distance_match.group(2) in {"mile", "miles", "mi"}:
                    max_distance_km *= 1.60934
            place_preferences = [
                preference
                for preference in place_preferences
                if preference.category != place_category
            ]
            place_preferences.append(PlacePreference(
                category=place_category,
                max_distance_km=max_distance_km,
            ))
            values["place_preferences"] = place_preferences

        unsupported = []
        unsupported_topics = {
            "neighborhood safety": ("safe", "safety", "crime"),
            "noise level": ("quiet", "noisy", "noise"),
            "landlord negotiation": ("negotiate", "negotiation"),
            "mold or air quality": ("mold", "air quality"),
            "current availability": ("still available", "available right now"),
            "pet walker availability": ("pet walker", "dog walker"),
        }
        for topic, terms in unsupported_topics.items():
            if any(term in lowered for term in terms):
                unsupported.append(topic)

        update = PreferenceUpdate(
            **values,
            clear=clear,
            unsupported_requests=unsupported,
        )
        recognized = bool(values or clear)
        if not recognized and unsupported:
            return TurnInterpretation(intent="search", update=update)
        if not recognized and not any(word in lowered for word in ("show", "find", "search", "list", "home", "place")):
            return TurnInterpretation(
                intent="search",
                update=update,
                clarification=(
                    "I could not map that request to Casita's listing data. "
                    "Try a budget, bedrooms, neighborhood, dog policy, yard, parking, or laundry."
                ),
            )
        return TurnInterpretation(intent="search", update=update)

    @staticmethod
    def _extract_amenity(
        text: str,
        *,
        name: str,
        synonyms: tuple[str, ...],
        required_field: str,
        values: dict,
        clear: list[str],
        preferred_features: list[str],
    ) -> None:
        if not any(word in text for word in synonyms):
            return
        if any(phrase in text for phrase in (f"no longer need {name}", f"{name} not required")):
            clear.append(required_field)
            return
        matching_clauses = [
            clause
            for clause in re.split(r"[,;]|\bbut\b|\band\b", text)
            if any(word in clause for word in synonyms)
        ]
        is_required = any(
            any(word in clause for word in ("must", "need", "require", "required"))
            and not any(word in clause for word in ("prefer", "nice", "like"))
            for clause in matching_clauses
        )
        if is_required:
            values[required_field] = True
        else:
            preferred_features.append(name)


class GeminiInterpreter:
    """Optional broad-language interpreter backed by Casita's Gemini client."""

    def __init__(self, fallback: Interpreter | None = None):
        self.fallback = fallback or RuleBasedInterpreter()

    def interpret(self, message: str, state: ConversationState) -> TurnInterpretation:
        from . import llm

        prompt = json.dumps({
            "message": message,
            "current_profile": state.profile.model_dump(),
            "last_result_keys": state.last_result_keys,
        }, indent=2)
        result = llm.interpret_agent_turn(prompt, TurnInterpretation)
        if isinstance(result, TurnInterpretation):
            return result
        return self.fallback.interpret(message, state)


class CasitaAgent:
    """Own conversation state and orchestrate Casita's read-only tools."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        interpreter: Interpreter | None = None,
        state: ConversationState | None = None,
        verifier: ResponseVerifier | None = None,
        data_mode: DataMode = "snapshot",
    ):
        self.conn = conn
        self.interpreter = interpreter or RuleBasedInterpreter()
        self.state = state or ConversationState()
        self.verifier = verifier
        self.data_mode = data_mode

    def respond(self, message: str) -> AgentResponse:
        plan = self.interpreter.interpret(message, self.state)
        self.state.profile = apply_update(self.state.profile, plan.update)

        if plan.intent == "exit":
            return self._response("Goodbye.", should_exit=True)
        if plan.intent == "help":
            return self._response(
                "Ask me to find, refine, or compare listings using budget, bedrooms, "
                "neighborhood, dog policy, yard, parking, laundry, light, or views."
            )
        if plan.intent == "show_preferences":
            return self._response(self._format_preferences())
        if plan.clarification:
            return self._response(plan.clarification)

        unsupported = plan.update.unsupported_requests
        if unsupported == ["current availability"] and self.data_mode == "live":
            return self._response(self._format_live_availability())
        if unsupported and not self._has_search_change(plan.update):
            return self._response(
                "I cannot verify " + ", ".join(unsupported) + " from Casita's current data."
            )

        if plan.intent in {"compare", "details"}:
            keys = plan.listing_keys
            if not keys:
                count = 1 if plan.intent == "details" else 2
                keys = self.state.last_result_keys[:count]
            if not keys:
                return self._response("Search for listings first, then tell me which results to inspect.")
            comparison = compare_active_listings(self.conn, keys)
            return self._response(
                self._format_comparison(comparison, unsupported),
                comparison_results=comparison,
            )

        results = search_active_listings(self.conn, self.state.profile)
        self.state.last_result_keys = [match.listing.key for match in results.matches]
        return self._response(
            self._format_search(results, unsupported),
            search_results=results,
        )

    def _response(
        self,
        message: str,
        *,
        search_results: SearchResults | None = None,
        comparison_results: ComparisonResults | None = None,
        should_exit: bool = False,
    ) -> AgentResponse:
        verification = None
        if self.verifier is not None and (search_results or comparison_results):
            verification = self.verifier.verify(
                message,
                EvidenceBundle(
                    search_results=search_results,
                    comparison_results=comparison_results,
                ),
            )
            if verification.verdict == "warn":
                warning = ", ".join(verification.unsupported_claims) or "unsupported claims"
                message += f"\nVerifier warning: {warning}."
        return AgentResponse(
            message=message,
            state=self.state.model_copy(deep=True),
            search_results=search_results,
            comparison_results=comparison_results,
            verification=verification,
            should_exit=should_exit,
        )

    @staticmethod
    def _has_search_change(update: PreferenceUpdate) -> bool:
        changed = set(update.model_fields_set) - {"unsupported_requests", "clear"}
        return bool(changed or update.clear)

    def _format_preferences(self) -> str:
        profile = self.state.profile
        parts = []
        if profile.max_price is not None:
            parts.append(f"budget up to ${profile.max_price:,}")
        if profile.min_beds is not None:
            parts.append(f"at least {profile.min_beds:g} bedrooms")
        if profile.min_baths is not None:
            parts.append(f"at least {profile.min_baths:g} bathrooms")
        if profile.neighborhoods:
            parts.append("in " + ", ".join(profile.neighborhoods))
        if profile.dog_requirement != "any":
            parts.append(profile.dog_requirement.replace("_", " "))
        for field, label in (
            (profile.yard_required, "yard required"),
            (profile.parking_required, "parking required"),
            (profile.laundry_required, "laundry required"),
        ):
            if field:
                parts.append(label)
        if profile.preferred_features:
            parts.append("prefer " + ", ".join(profile.preferred_features))
        for route in profile.route_preferences:
            label = f"near {route.category}"
            if route.max_minutes is not None:
                label += f" within {route.max_minutes} minutes"
            parts.append(label)
        for place in profile.place_preferences:
            label = "near " + place.category.replace("_", " ")
            if place.max_distance_km is not None:
                label += f" within {place.max_distance_km:.1f} km"
            parts.append(label)
        return "Current preferences: " + ("; ".join(parts) if parts else "none yet") + "."

    def _format_search(self, results: SearchResults, unsupported: list[str]) -> str:
        if not results.matches:
            if self.data_mode == "live":
                message = "No currently observed listings satisfy all current hard constraints."
            else:
                message = "No listings in the stored snapshot satisfy all current hard constraints."
        else:
            if self.data_mode == "live":
                lines = [
                    f"Found {results.total_matched} live-source matches. Showing the top "
                    f"{len(results.matches)}. Each was observed in a current rental search "
                    "when this server started; source pages can still change:"
                ]
            else:
                lines = [
                    f"Found {results.total_matched} snapshot matches. Showing the top "
                    f"{len(results.matches)}. Prices are stored snapshot values; current "
                    "price and availability are not verified:"
                ]
            lines.extend(
                f"{index}. {self._format_facts(match.listing)}"
                + (
                    " Routes: "
                    + ", ".join(
                        f"{route.minutes}-min {route.mode} to {route.anchor}"
                        for route in match.route_evidence
                    )
                    + "."
                    if match.route_evidence else ""
                )
                + (
                    " Needs confirmation: " + ", ".join(match.unknown_preferences) + "."
                    if match.unknown_preferences else ""
                )
                + (
                    " Nearby: "
                    + ", ".join(
                        f"{place.name} ({place.distance_km:g} km)"
                        for place in match.place_evidence
                    )
                    + "."
                    if match.place_evidence else ""
                )
                for index, match in enumerate(results.matches, 1)
            )
            message = "\n".join(lines)
        if unsupported:
            message += "\nCould not verify: " + ", ".join(unsupported) + "."
        return message

    def _format_comparison(self, results: ComparisonResults, unsupported: list[str]) -> str:
        if not results.listings:
            noun = "live inventory" if self.data_mode == "live" else "stored snapshot"
            message = f"I could not find those listing keys in the {noun}."
        else:
            if self.data_mode == "live":
                message = (
                    "Comparing listings observed in the live source refresh; source pages can still change:\n"
                    + "\n".join(self._format_facts(listing) for listing in results.listings)
                )
            else:
                message = (
                    "Comparing stored snapshot facts; current price and availability are not verified:\n"
                    + "\n".join(self._format_facts(listing) for listing in results.listings)
                )
        if results.missing_keys:
            message += "\nMissing listings: " + ", ".join(results.missing_keys) + "."
        if unsupported:
            message += "\nCould not verify: " + ", ".join(unsupported) + "."
        return message

    def _format_facts(self, facts: ListingFacts) -> str:
        name = facts.address or facts.key
        price_label = "live" if self.data_mode == "live" else "snapshot"
        fields = [
            f"{price_label} ${facts.price:,}"
            if facts.price is not None
            else f"{price_label} price not recorded",
            f"{facts.beds:g} bd" if facts.beds is not None else "beds unknown",
            f"{facts.baths:g} ba" if facts.baths is not None else "baths unknown",
        ]
        if facts.neighborhood:
            fields.append(facts.neighborhood)
        if facts.dog_policy:
            fields.append("dogs: " + facts.dog_policy.replace("_", " "))
        if facts.has_yard is True:
            fields.append("yard")
        if facts.last_seen is not None:
            observed = facts.last_seen.strftime("%b %d, %Y").replace(" 0", " ")
            fields.append(f"last observed {observed}")
        if self.data_mode == "live":
            fields.append(f"observed in live {facts.source} rental search")
        else:
            fields.append("availability unverified")
        return f"{name} ({facts.key}) — " + ", ".join(fields)

    def _format_live_availability(self) -> str:
        if not self.state.last_result_keys:
            return "Search the live inventory first, then ask about a result."
        comparison = compare_active_listings(self.conn, self.state.last_result_keys[:1])
        if not comparison.listings:
            return "That result is no longer present in the current live inventory."
        facts = comparison.listings[0]
        name = facts.address or facts.key
        observed = (
            facts.last_seen.strftime("%b %d, %Y at %H:%M UTC")
            if facts.last_seen is not None
            else "the latest refresh"
        )
        price = f" at ${facts.price:,}/month" if facts.price is not None else ""
        return (
            f"{name} was observed in the live {facts.source} rental search "
            f"on {observed}{price}. Reopen the source before acting because listings can change."
        )
