"""Small authenticated loopback client shared by CLI and MCP adapters."""

import os
from pathlib import Path
from typing import Any

import httpx

from second_brain.django_config.environment import load_environment


class BackendRequestError(RuntimeError):
    """An HTTP failure with a public reason and status code for client-side handling."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code


class BackendClient:
    def __init__(self, vault: Path | None = None, port: int | None = None) -> None:
        load_environment()
        self.vault = (
            (vault or Path(os.environ.get("SECOND_BRAIN_VAULT", "~/second-brain")))
            .expanduser()
            .resolve()
        )
        self.port = port if port is not None else int(os.environ.get("SECOND_BRAIN_PORT", "8000"))

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> Any:
        """Call a versioned API route; return parsed JSON or None for a 204 response.

        The token is read from this vault's private runtime directory. Redirects are
        disabled and URLs are restricted to the selected loopback backend.
        """
        if not path.startswith("/") or path.startswith("//") or ".." in path.split("/"):
            raise ValueError("Use an API path beginning with /.")
        try:
            token = (self.vault / ".backend" / "api-token").read_text().strip()
        except FileNotFoundError as exc:
            raise RuntimeError("Start the backend for this vault first.") from exc
        with httpx.Client(timeout=120, trust_env=False, follow_redirects=False) as client:
            response = client.request(
                method,
                f"http://127.0.0.1:{self.port}/v1{path}",
                json=payload,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.is_error:
            try:
                detail = response.json().get("detail", f"HTTP {response.status_code}")
            except ValueError:
                detail = f"HTTP {response.status_code}"
            raise BackendRequestError(response.status_code, str(detail))
        return None if response.status_code == 204 else response.json()
