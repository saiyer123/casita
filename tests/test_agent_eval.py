from casita import AGENT_EVAL_FIXTURE
from casita.agent import RuleBasedInterpreter
from casita.agent_eval import evaluate_interpreter, load_eval_cases


def test_offline_interpreter_passes_public_agent_evals():
    cases = load_eval_cases(AGENT_EVAL_FIXTURE)

    report = evaluate_interpreter(RuleBasedInterpreter(), cases)

    assert report.total_cases == 6
    assert report.passed_cases == report.total_cases, report.failures
    assert report.correct_intents == report.intent_checks
    assert report.correct_profile_fields == report.profile_checks
    assert report.correct_unsupported == report.unsupported_checks
