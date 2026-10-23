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
from second_brain.apps.wiki.views import router as wiki_router

api = NinjaAPI(
    title="Second Brain",
    version="1.0.0",
    docs_url="/docs" if settings.API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if settings.API_DOCS_ENABLED else None,
)


class HealthResponse(Schema):
    status: Literal["ok"]


@api.get("/health", response=HealthResponse, tags=["System"])
def health(request: HttpRequest) -> HealthResponse:
    """Confirm the API process responds"""
    return HealthResponse(status="ok")


# Each app owns its endpoint paths
api.add_router("", library_router)
api.add_router("", ingestion_router)
api.add_router("", compilation_router)
api.add_router("", wiki_router)
api.add_router("", jobs_router)
api.add_router("", preferences_router)
