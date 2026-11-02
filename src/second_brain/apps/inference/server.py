"""Serve llama.cpp on a loopback socket reserved by the inference worker."""

import argparse
import os
import socket
from pathlib import Path

import uvicorn
from llama_cpp.server.app import create_app
from llama_cpp.server.settings import ConfigFileSettings, ServerSettings


def serve(config_path: Path, socket_fd: int) -> None:
    """Load the configured models and serve authenticated HTTP on an inherited socket.

    Parameters
    ----------
    config_path: Path
        Private JSON file containing server settings and model paths.
    socket_fd: int
        Inherited file descriptor for an already bound IPv4 loopback socket.
        This function owns and closes the descriptor.

    Returns
    -------
    None
        Returns after Uvicorn shuts down.

    Raises
    ------
    ValueError
        The socket or configuration would expose unauthenticated or non-loopback HTTP.
    """
    with socket.socket(fileno=socket_fd) as listener:
        config = ConfigFileSettings.model_validate_json(config_path.read_text())
        if (
            listener.getsockname()[0] != "127.0.0.1"
            or config.host != "127.0.0.1"
            or not config.api_key
        ):
            raise ValueError("Inference requires a loopback address and authentication token.")
        # The library's app factory otherwise replaces even explicitly supplied settings.
        os.environ.pop("CONFIG_FILE", None)
        app = create_app(
            server_settings=ServerSettings.model_validate(config),
            model_settings=config.models,
        )
        uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=listener.getsockname()[1], access_log=False)
        ).run(sockets=[listener])


def main() -> None:
    """Read the private configuration path and inherited socket descriptor from argv."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config_path", type=Path)
    parser.add_argument("socket_fd", type=int)
    arguments = parser.parse_args()
    serve(arguments.config_path, arguments.socket_fd)


if __name__ == "__main__":
    main()
