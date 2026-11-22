"""Local API authentication, separate from provider credentials and Django signing."""

import secrets

from django.conf import settings
from django.http import HttpRequest
from ninja.security import HttpBearer


class LocalTokenAuth(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> str | None:
        """Compare a bearer token with the configured or vault-stored API token.

        Parameters
        ----------
        request: HttpRequest
            Incoming HTTP request required by Ninja's authentication interface.
        token: str
            Bearer token supplied in the Authorization header.

        Returns
        -------
        str | None
            local-client on a constant-time match, otherwise None. An unreadable vault token
            also fails authentication.
        """
        expected = getattr(settings, "API_TOKEN", "")
        if not expected:
            try:
                expected = (settings.VAULT_DIR / ".backend" / "api-token").read_text().strip()
            except OSError:
                return None
        if expected and secrets.compare_digest(token.encode(), expected.encode()):
            return "local-client"
        return None
