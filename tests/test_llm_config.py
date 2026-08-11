import pytest

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
