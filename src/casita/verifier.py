"""Optional second-agent verification of grounded Casita responses."""

import json
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .agent_tools import ComparisonResults, SearchResults


class EvidenceBundle(BaseModel):
    search_results: SearchResults | None = None
    comparison_results: ComparisonResults | None = None


class VerificationReport(BaseModel):
    verdict: Literal["pass", "warn", "unavailable"]
    unsupported_claims: list[str] = Field(default_factory=list)
    note: str | None = None


class ResponseVerifier(Protocol):
    def verify(
        self,
        answer: str,
        evidence: EvidenceBundle,
    ) -> VerificationReport: ...


class GeminiVerifier:
    """Use a separate Gemini call to audit a primary-agent answer."""

    def verify(
        self,
        answer: str,
        evidence: EvidenceBundle,
    ) -> VerificationReport:
        return self.verify_payload(answer, evidence.model_dump(mode="json"))

    def verify_payload(self, answer: str, evidence: dict) -> VerificationReport:
        from . import llm

        prompt = json.dumps({"answer": answer, "evidence": evidence}, indent=2)
        result = llm.verify_agent_response(prompt, VerificationReport)
        if isinstance(result, VerificationReport):
            return result
        return VerificationReport(
            verdict="unavailable",
            note="The verifier model did not return a valid report.",
        )
