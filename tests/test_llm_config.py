import pytest
from pydantic import BaseModel, Field

from casita import llm


@pytest.fixture(autouse=True)
def reset_llm_client(monkeypatch):
    for name in (
        "CASITA_GCP_PROJECT",
        "CASITA_VERTEX_LOCATION",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "_client_config", None)


def test_gemini_api_key_configures_developer_api(monkeypatch):
    sentinel = object()
    calls = []
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setattr(llm.genai, "Client", lambda **kwargs: calls.append(kwargs) or sentinel)

    assert llm.llm_is_configured()
    assert llm._get_client() is sentinel
    assert calls == [{"api_key": "test-gemini-key"}]


def test_google_api_key_is_supported(monkeypatch):
    sentinel = object()
    calls = []
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.setattr(llm.genai, "Client", lambda **kwargs: calls.append(kwargs) or sentinel)

    assert llm._get_client() is sentinel
    assert calls == [{"api_key": "test-google-key"}]


def test_vertex_project_configures_vertex_client(monkeypatch):
    sentinel = object()
    calls = []
    monkeypatch.setenv("CASITA_GCP_PROJECT", "test-project")
    monkeypatch.setenv("CASITA_VERTEX_LOCATION", "us-central1")
    monkeypatch.setattr(llm.genai, "Client", lambda **kwargs: calls.append(kwargs) or sentinel)

    assert llm.llm_is_configured()
    assert llm._get_client() is sentinel
    assert calls == [{
        "vertexai": True,
        "project": "test-project",
        "location": "us-central1",
    }]


def test_missing_gemini_configuration_has_actionable_error():
    assert not llm.llm_is_configured()

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        llm._get_client()


def test_structured_call_uses_gemini_compatible_json_schema(monkeypatch):
    class PositiveResult(BaseModel):
        value: int = Field(gt=0)

    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {"text": '{"value": 7}'})()

    fake_client = type("Client", (), {"models": FakeModels()})()
    monkeypatch.setattr(llm, "_get_client", lambda: fake_client)

    result = llm._call_structured(
        "gemini-3.5-flash-lite",
        "Return a positive value.",
        "Seven",
        PositiveResult,
    )

    assert result == PositiveResult(value=7)
    config = captured["config"]
    assert config.response_schema is None
    assert config.response_json_schema["properties"]["value"]["minimum"] == 0
    assert "exclusiveMinimum" not in str(config.response_json_schema)
