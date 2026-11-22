"""Reusable OCR results, shared by incremental ingestion runs."""

from django.db import models


class PageCache(models.Model):
    page_hash = models.TextField(primary_key=True)
    parse_lane = models.TextField()
    raw_markdown = models.TextField()
    parsed_at = models.DateTimeField()

    class Meta:
        db_table = "page_cache"
