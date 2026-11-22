"""Register expected service-error mappings once when assembling the API."""

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI

from second_brain.django_config.errors import ServiceError
from second_brain.django_config.schemas import ErrorResponse
from second_brain.pipeline_config import ConfigError


def register_exception_handlers(api: NinjaAPI) -> None:
    """Register JSON error responses for service and configuration exceptions.

    Parameters
    ----------
    api: NinjaAPI
        API instance to modify. Service errors use their declared status; configuration
        errors return HTTP 500.

    Returns
    -------
    None
    """

    @api.exception_handler(ServiceError)
    def service_error(request: HttpRequest, exc: ServiceError) -> HttpResponse:
        """Serialize the public reason with the service exception's declared HTTP status."""
        # Exception responses bypass the view's response schema, so validate here.
        body = ErrorResponse(detail=exc.reason)
        return api.create_response(request, body.model_dump(), status=exc.status_code)

    @api.exception_handler(ConfigError)
    def configuration_error(request: HttpRequest, exc: ConfigError) -> HttpResponse:
        body = ErrorResponse(detail=exc.reason)
        return api.create_response(request, body.model_dump(), status=500)
