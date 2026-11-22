from django.test import Client

from second_brain.apps.inference.errors import InferenceUnavailableError
from second_brain.apps.wiki.services.assistant_tools import WikiTools
from second_brain.apps.wiki.services.search_index import SearchIndex
from second_brain.pipeline_config import PipelineConfig


def test_keyword_search_does_not_need_valid_preferences(
    api_client: Client, config: PipelineConfig
) -> None:
    page = config.wiki_dir / "concepts" / "gradient.md"
    page.write_text("---\ntitle: Gradient\ntype: concept\n---\nA direction of ascent.")
    SearchIndex(config.search_db_path).sync_from_wiki(config.wiki_dir)
    (config.data_dir / "config.yaml").write_text("invalid: [")

    result = api_client.get("/v1/wiki/search", {"q": "ascent"})
    assert result.status_code == 200
    assert [hit["title"] for hit in result.json()] == ["Gradient"]

    tool = api_client.post(
        "/v1/assistant-tools/search_wiki", {"args": ["ascent"]}, content_type="application/json"
    )
    assert tool.status_code == 200
    assert "**Gradient**" in tool.json()


def test_capture_rejects_invalid_ingest_preferences_before_writing(
    api_client: Client, config: PipelineConfig
) -> None:
    (config.data_dir / "config.yaml").write_text("invalid: [")
    response = api_client.post(
        "/v1/assistant-tools/capture_note", {"args": ["A note"]}, content_type="application/json"
    )
    assert response.status_code == 500
    assert not list((config.drops_dir / "documents").iterdir())


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
