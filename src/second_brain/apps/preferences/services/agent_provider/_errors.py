"""Translate Pi command failures to the errors exposed by Settings."""

from collections.abc import Iterator
from contextlib import contextmanager

from second_brain.cloud_llm.pi.pi_helper import PiHelperError, PiUsageError
from second_brain.django_config.errors import DependencyError, ServiceError


@contextmanager
def settings_errors() -> Iterator[None]:
    try:
        yield
    except PiUsageError as exc:
        raise ServiceError(exc.reason) from exc
    except PiHelperError as exc:
        raise DependencyError(exc.reason) from exc
