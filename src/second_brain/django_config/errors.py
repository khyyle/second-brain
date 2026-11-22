"""Expected service failures and their public HTTP representation."""

from second_brain.errors import SecondBrainError


class ServiceError(SecondBrainError):
    status_code = 400


class NotFoundError(ServiceError):
    status_code = 404


class ConflictError(ServiceError):
    status_code = 409


class DependencyError(ServiceError):
    status_code = 503
