import pytest


@pytest.fixture(autouse=True)
def mock_keychain(provider_key):
    """Compiler tests use the shared fake credential; provider calls stay mocked."""
