"""Build completion and deferral records for specific source versions."""

from django.db import models


class CompiledSource(models.Model):
    raw_path = models.TextField(primary_key=True)
    compiled_at = models.DateTimeField()
    content_hash = models.TextField(null=True)

    class Meta:
        db_table = "compiled"


class DeferredSource(models.Model):
    raw_path = models.TextField(primary_key=True)
    reason = models.TextField()
    deferred_at = models.DateTimeField()
    retryable = models.BooleanField(default=True)
    content_hash = models.TextField(null=True)

    class Meta:
        db_table = "deferred_sources"
