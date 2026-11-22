"""Django registration for the ingestion feature."""

from django.apps import AppConfig


class IngestionConfig(AppConfig):
    name = "second_brain.apps.ingestion"
    verbose_name = "Ingestion"
