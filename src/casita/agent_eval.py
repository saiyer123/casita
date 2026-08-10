"""Small, credentials-free evaluation harness for the conversational agent."""

import json
from pathlib import Path

from pydantic import BaseModel, Field

from .agent import ConversationState, Interpreter
from .preferences import apply_update


class AgentEvalCase(BaseModel):
    name: str
    messages: list[str]
    expected_intents: list[str]
    expected_profile: dict[str, object] = Field(default_factory=dict)
    expected_unsupported: list[str] = Field(default_factory=list)


class AgentEvalFailure(BaseModel):
    case: str
    detail: str


class AgentEvalReport(BaseModel):
    total_cases: int
    passed_cases: int
    intent_checks: int
    correct_intents: int
    profile_checks: int
    correct_profile_fields: int
    unsupported_checks: int
    correct_unsupported: int
    failures: list[AgentEvalFailure] = Field(default_factory=list)

    @property
    def case_accuracy(self) -> float:
        return self.passed_cases / self.total_cases if self.total_cases else 1.0


def load_eval_cases(path: Path) -> list[AgentEvalCase]:
    return [AgentEvalCase.model_validate(case) for case in json.loads(path.read_text())]


def evaluate_interpreter(
    interpreter: Interpreter,
    cases: list[AgentEvalCase],
) -> AgentEvalReport:
    failures: list[AgentEvalFailure] = []
    intent_checks = correct_intents = 0
    profile_checks = correct_profile_fields = 0
    unsupported_checks = correct_unsupported = 0
    passed_cases = 0

    for case in cases:
        state = ConversationState()
        actual_intents: list[str] = []
        actual_unsupported: list[str] = []
        case_failures: list[str] = []

        for message in case.messages:
            plan = interpreter.interpret(message, state)
            actual_intents.append(plan.intent)
            actual_unsupported.extend(plan.update.unsupported_requests)
            state.profile = apply_update(state.profile, plan.update)

        for index, expected in enumerate(case.expected_intents):
            intent_checks += 1
            actual = actual_intents[index] if index < len(actual_intents) else None
            if actual == expected:
                correct_intents += 1
            else:
                case_failures.append(
                    f"turn {index + 1} intent: expected {expected!r}, got {actual!r}"
                )

        profile = state.profile.model_dump()
        for field, expected in case.expected_profile.items():
            profile_checks += 1
            actual = profile.get(field)
            if actual == expected:
                correct_profile_fields += 1
            else:
                case_failures.append(
                    f"profile.{field}: expected {expected!r}, got {actual!r}"
                )

        unsupported_checks += 1
        if set(actual_unsupported) == set(case.expected_unsupported):
            correct_unsupported += 1
        else:
            case_failures.append(
                "unsupported requests: expected "
                f"{case.expected_unsupported!r}, got {actual_unsupported!r}"
            )

        if case_failures:
            failures.extend(
                AgentEvalFailure(case=case.name, detail=detail)
                for detail in case_failures
            )
        else:
            passed_cases += 1

    return AgentEvalReport(
        total_cases=len(cases),
        passed_cases=passed_cases,
        intent_checks=intent_checks,
        correct_intents=correct_intents,
        profile_checks=profile_checks,
        correct_profile_fields=correct_profile_fields,
        unsupported_checks=unsupported_checks,
        correct_unsupported=correct_unsupported,
        failures=failures,
    )
