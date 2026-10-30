import asyncio
import inspect
import subprocess
import sys
from unittest.mock import Mock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError

from second_brain.apps.mcp.server import create_server
from second_brain.apps.mcp.tool_definitions import WIKI_TOOL_NAMES, get_tool_schemas
from second_brain.backend_client import BackendClient, BackendRequestError


@pytest.fixture
def backend_client() -> Mock:
    return Mock(spec=BackendClient)


def test_mcp_semantic_search_notifies_when_ollama_is_down(backend_client: Mock) -> None:
    backend_client.request.side_effect = BackendRequestError(503, "Ollama is unavailable.")

    _, structured = asyncio.run(
        create_server(backend_client).call_tool("semantic_search", {"query": "gradient"})
    )

    assert structured["result"] == (
        "Ollama isn't responding, so search by meaning is unavailable. Tell the "
        "user, and use search_wiki for keyword search instead."
    )


@pytest.mark.parametrize("status", [401, 400, 500])
def test_mcp_semantic_search_preserves_other_backend_failures(
    backend_client: Mock, status: int
) -> None:
    backend_client.request.side_effect = BackendRequestError(status, "Request failed.")

    with pytest.raises(ToolError, match="Request failed") as error:
        asyncio.run(
            create_server(backend_client).call_tool("semantic_search", {"query": "gradient"})
        )

    assert isinstance(error.value.__cause__, BackendRequestError)
    assert error.value.__cause__.status_code == status


def test_registered_tools_match_shared_definitions(backend_client: Mock) -> None:
    registered = asyncio.run(create_server(backend_client).list_tools())
    schemas = get_tool_schemas(WIKI_TOOL_NAMES)

    assert [tool.name for tool in registered] == list(WIKI_TOOL_NAMES)
    for tool, schema in zip(registered, schemas, strict=True):
        assert tool.inputSchema == schema["input_schema"]
        assert inspect.cleandoc(tool.description) == inspect.cleandoc(schema["description"])
    backend_client.request.assert_not_called()


def test_mcp_validates_then_forwards_tool_arguments(backend_client: Mock) -> None:
    backend_client.request.return_value = "Matching pages"
    server = create_server(backend_client)

    _, structured = asyncio.run(server.call_tool("search_wiki", {"query": "gradient", "limit": 5}))

    assert structured == {"result": "Matching pages"}
    backend_client.request.assert_called_once_with(
        "POST", "/assistant-tools/search_wiki", payload={"args": ["gradient", 5], "kwargs": {}}
    )
    backend_client.request.reset_mock()
    with pytest.raises(ToolError, match="less than or equal to 50"):
        asyncio.run(server.call_tool("search_wiki", {"query": "gradient", "limit": 51}))
    backend_client.request.assert_not_called()


def test_compilation_schemas_do_not_initialize_mcp_server_or_http_client() -> None:
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
sys.modules['second_brain.apps.mcp.server'] = None
from second_brain.apps.compilation.services.agent.tools import explore_tool_schemas
assert len(explore_tool_schemas()) == 8
assert 'second_brain.backend_client' not in sys.modules
""",
        ],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr


def test_stdio_lists_tools_without_a_running_backend(tmp_path) -> None:
    async def inspect_tools() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "second_brain.apps.mcp.server"],
            env={"PYTHON_DOTENV_DISABLED": "1", "SECOND_BRAIN_VAULT": str(tmp_path)},
        )
        async with asyncio.timeout(15), stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert tuple(tool.name for tool in listed.tools) == WIKI_TOOL_NAMES

    asyncio.run(inspect_tools())
