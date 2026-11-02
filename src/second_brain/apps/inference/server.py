"""Serve llama.cpp on a loopback socket reserved by the inference worker."""

import argparse
import os
import socket
import threading
from pathlib import Path

import uvicorn
from llama_cpp.server.app import create_app
from llama_cpp.server.settings import ConfigFileSettings, ServerSettings

PARENT_CHECK_SECONDS = 1


def serve(config_path: Path, socket_fd: int, parent_pid: int) -> None:
    """Load one model and serve authenticated HTTP on an inherited socket.

    Parameters
    ----------
    config_path: Path
        Private JSON file containing server settings and model paths.
    socket_fd: int
        Inherited file descriptor for an already bound IPv4 loopback socket.
        This function owns and closes the descriptor.
    parent_pid: int
        Worker that owns this server. Parent exit terminates the model process.

    Returns
    -------
    None
        Returns after Uvicorn shuts down.

    Raises
    ------
    ValueError
        The socket or configuration would expose unauthenticated or non-loopback HTTP.
        The configuration does not contain exactly one model.
    """
    with socket.socket(fileno=socket_fd) as listener:
        server_config = ConfigFileSettings.model_validate_json(config_path.read_text())
        if listener.getsockname()[0] != "127.0.0.1":
            raise ValueError("Inference socket must be bound to 127.0.0.1.")
        if not server_config.api_key:
            raise ValueError("Inference authentication token is missing.")
        if len(server_config.models) != 1:
            raise ValueError("Each inference server requires exactly one model.")
        stop = threading.Event()
        watcher = threading.Thread(target=_watch_parent, args=(parent_pid, stop), daemon=True)
        watcher.start()
        try:
            # The library's app factory otherwise replaces even explicitly supplied settings.
            os.environ.pop("CONFIG_FILE", None)
            app = create_app(
                server_settings=ServerSettings.model_validate(server_config),
                model_settings=server_config.models,
            )
            uvicorn.Server(
                uvicorn.Config(
                    app, host="127.0.0.1", port=listener.getsockname()[1], access_log=False
                )
            ).run(sockets=[listener])
        finally:
            stop.set()
            watcher.join()


def _watch_parent(parent_pid: int, stop: threading.Event) -> None:
    """Release native model memory if the owning worker disappears."""
    while not stop.wait(PARENT_CHECK_SECONDS):
        if os.getppid() != parent_pid:
            # This child owns no durable writes; exiting also stops unresponsive native inference.
            os._exit(1)


def main() -> None:
    """Read the private configuration path and inherited socket descriptor from argv."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config_path", type=Path)
    parser.add_argument("socket_fd", type=int)
    parser.add_argument("parent_pid", type=int)
    arguments = parser.parse_args()
    serve(arguments.config_path, arguments.socket_fd, arguments.parent_pid)


if __name__ == "__main__":
    main()
