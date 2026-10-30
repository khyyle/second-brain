"""Assistant installation and connection status."""

from ninja import Schema


class IntegrationStatus(Schema):
    target: str
    installed: bool
    configured: bool
    message: str
