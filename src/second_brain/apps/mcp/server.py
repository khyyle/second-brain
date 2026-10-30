"""MCP stdio server forwarding assistant tools to the local HTTP backend."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from second_brain.apps.mcp.tool_definitions import WIKI_TOOL_NAMES, WikiToolDefinitions
from second_brain.backend_client import BackendClient, BackendRequestError


def create_server(client: BackendClient | None = None) -> FastMCP:
    """Register wiki tools against a local backend client.

    Parameters
    ----------
    client: BackendClient | None
        Client selecting the vault and port. None uses the startup configuration.

    Returns
    -------
    FastMCP
        Configured server; no transport is started until run is called.
    """
    backend = client if client is not None else BackendClient()

    def dispatch(name: str, *args: Any, **kwargs: Any) -> str:
        try:
            return backend.request(
                "POST",
                f"/assistant-tools/{name}",
                payload={"args": list(args), "kwargs": kwargs},
            )
        except BackendRequestError as error:
            if name != "semantic_search" or error.status_code != 503:
                raise
            return (
                "Ollama isn't responding, so search by meaning is unavailable. Tell the "
                "user, and use search_wiki for keyword search instead."
            )

    server = FastMCP("second-brain")
    definitions = WikiToolDefinitions(dispatch)
    for name in WIKI_TOOL_NAMES:
        server.add_tool(getattr(definitions, name))
    return server


def serve(client: BackendClient | None = None) -> None:
    """Run the MCP stdio server until its input connection closes.

    Parameters
    ----------
    client: BackendClient | None
        Client selecting the vault and port. None uses the startup configuration.

    Returns
    -------
    None
        Returns when the server stops.
    """
    create_server(client).run(transport="stdio")


if __name__ == "__main__":
    serve()
