"""Assemble feature routers and expose the API process liveness endpoint."""

from typing import Literal

from django.conf import settings
from django.http import HttpRequest
from ninja import NinjaAPI, Schema

from second_brain.apps.compilation.views import router as compilation_router
from second_brain.apps.ingestion.views import router as ingestion_router
from second_brain.apps.jobs.views import router as jobs_router
from second_brain.apps.library.views import router as library_router
from second_brain.apps.preferences.views import router as preferences_router
from second_brain.apps.wiki.assistant_views import router as assistant_router
from second_brain.apps.wiki.views import router as wiki_router
from second_brain.django_config.api_errors import register_exception_handlers
from second_brain.django_config.auth import LocalTokenAuth

api = NinjaAPI(
    title="Second Brain",
    version="1.0.0",
    auth=LocalTokenAuth(),
    docs_url="/docs" if settings.API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if settings.API_DOCS_ENABLED else None,
)


register_exception_handlers(api)


class HealthResponse(Schema):
    status: Literal["ok"]


@api.get("/health", response=HealthResponse, tags=["System"], auth=None)
def health(request: HttpRequest) -> HealthResponse:
    """Report that the API process can answer requests.

    Parameters
    ----------
    request: HttpRequest
        Incoming HTTP request; this endpoint does not require authentication.

    Returns
    -------
    HealthResponse
        A response whose status is `ok`.
    """
    return HealthResponse(status="ok")


api.add_router("", library_router)
api.add_router("", ingestion_router)
api.add_router("", compilation_router)
api.add_router("", wiki_router)
api.add_router("", jobs_router)
api.add_router("", preferences_router)
api.add_router("", assistant_router)
