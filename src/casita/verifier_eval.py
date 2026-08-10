"""Evaluation harness for the optional verifier agent."""

import json
import time
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from .verifier import VerificationReport


class PayloadVerifier(Protocol):
    def verify_payload(self, answer: str, evidence: dict) -> VerificationReport: ...


class VerifierEvalCase(BaseModel):
    name: str
    answer: str
    evidence: dict
    expected_verdict: str


class VerifierEvalReport(BaseModel):
    total_cases: int
    correct_cases: int
    elapsed_seconds: float
    failures: list[str] = Field(default_factory=list)


def load_verifier_cases(path: Path) -> list[VerifierEvalCase]:
    return [VerifierEvalCase.model_validate(case) for case in json.loads(path.read_text())]


def evaluate_verifier(
    verifier: PayloadVerifier,
    cases: list[VerifierEvalCase],
) -> VerifierEvalReport:
    started = time.perf_counter()
    failures = []
    correct = 0
    for case in cases:
        result = verifier.verify_payload(case.answer, case.evidence)
        if result.verdict == case.expected_verdict:
            correct += 1
        else:
            failures.append(
                f"{case.name}: expected {case.expected_verdict}, got {result.verdict}"
            )
    return VerifierEvalReport(
        total_cases=len(cases),
        correct_cases=correct,
        elapsed_seconds=round(time.perf_counter() - started, 3),
        failures=failures,
    )
