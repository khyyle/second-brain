from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.wiki.services.assistant_tools import WikiTools


def test_semantic_search_reports_inference_failure_as_503(api_client, monkeypatch):
    def fail(self, query: str, limit: int = 10) -> str:
        raise InferenceUnavailableError("The embedding model is loading.")

    monkeypatch.setattr(WikiTools, "semantic_search", fail)

    response = api_client.post(
        "/v1/assistant-tools/semantic_search",
        {"args": ["gradient"]},
        content_type="application/json",
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The embedding model is loading. "
        "Use keyword search while local inference is unavailable."
    }
