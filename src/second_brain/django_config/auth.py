"""Local API authentication, separate from provider credentials and Django signing."""

import secrets

from django.conf import settings
from django.http import HttpRequest
from ninja.security import HttpBearer


class LocalTokenAuth(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> str | None:
        expected = getattr(settings, "API_TOKEN", "")
        if not expected:
            try:
                expected = (settings.VAULT_DIR / ".backend" / "api-token").read_text().strip()
            except OSError:
                return None
        if expected and secrets.compare_digest(token.encode(), expected.encode()):
            return "local-client"
        return None
