from second_brain.apps.compilation.services.build_log import append_build_actions


def test_build_log_endpoint_reads_committed_page_actions(api_client, config):
    append_build_actions(config.data_dir, [{"action": "created", "path": "concepts/a.md"}])
    append_build_actions(config.data_dir, [{"action": "updated", "path": "concepts/a.md"}])
    result = api_client.get("/v1/builds/actions")
    assert result.status_code == 200
    assert [row["action"] for row in result.json()] == ["updated", "created"]
    assert all(row["path"] == "concepts/a.md" for row in result.json())
