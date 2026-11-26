"""Connect a model provider and list model choices for Settings.

A login ID identifies an in-progress sign-in on this backend. It expires and does
not survive a restart. A completed sign-in stores the credential in macOS Keychain.

``list_models`` and ``logout`` take a provider ID and need no active login.
"""

from .model_selection import ModelChoice, list_models
from .provider_auth import answer_login, cancel_login, logout, read_login_event, start_login

__all__ = [
    "ModelChoice",
    "answer_login",
    "cancel_login",
    "list_models",
    "logout",
    "read_login_event",
    "start_login",
]
