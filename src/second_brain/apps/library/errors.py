"""Expected library failures and the HTTP status each one represents."""

from second_brain.django_config.errors import ServiceError


class LibraryError(ServiceError):
    """An explainable source failure handled by the shared API error handler."""

    status_code = 500


class SourceNotFoundError(LibraryError):
    """The requested source record or raw file is missing from the library."""

    status_code = 404


class InvalidSourcePathError(LibraryError):
    """The caller supplied something other than a safe relative raw Markdown path."""

    status_code = 400


class InvalidTriageError(LibraryError):
    """The decision or source lane is outside the manual triage contract."""

    status_code = 400


class SourceConflictError(LibraryError):
    """The current files prevent the operation from proceeding without data loss."""

    status_code = 409


class LibraryConfigurationError(LibraryError):
    """The selected vault's configuration needs repair before using the library."""

    status_code = 500
