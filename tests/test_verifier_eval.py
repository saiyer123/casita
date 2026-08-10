from casita.verifier import GeminiVerifier, VerificationReport
from casita.verifier_eval import VerifierEvalCase, evaluate_verifier


class ExpectedVerifier:
    def verify_payload(self, answer: str, evidence: dict) -> VerificationReport:
        verdict = "warn" if evidence.get("unsupported") else "pass"
        return VerificationReport(verdict=verdict)


def test_verifier_evaluation_compares_expected_verdicts():
    cases = [
        VerifierEvalCase(
            name="supported",
            answer="Supported",
            evidence={},
            expected_verdict="pass",
        ),
        VerifierEvalCase(
            name="unsupported",
            answer="Unsupported",
            evidence={"unsupported": True},
            expected_verdict="warn",
        ),
    ]

    report = evaluate_verifier(ExpectedVerifier(), cases)

    assert report.correct_cases == 2
    assert report.failures == []


def test_gemini_verifier_uses_the_typed_llm_contract(monkeypatch):
    expected = VerificationReport(verdict="warn", unsupported_claims=["large dogs"])

    def fake_verify(prompt, schema):
        assert '\"answer\": \"Large dogs are accepted.\"' in prompt
        assert schema is VerificationReport
        return expected

    monkeypatch.setattr("casita.llm.verify_agent_response", fake_verify)

    result = GeminiVerifier().verify_payload(
        "Large dogs are accepted.",
        {"listing": {"dog_policy": "dogs_ok"}},
    )

    assert result == expected
