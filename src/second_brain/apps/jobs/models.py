"""Durable operation records; worker execution is still to be implemented."""

import uuid

from django.db import models


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        CANCELLED = "cancelled"
        INTERRUPTED = "interrupted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=40)
    lane = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=Status, default=Status.QUEUED)
    payload = models.JSONField(default=dict)
    config_snapshot = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    cancel_requested = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    retry_of = models.ForeignKey("self", null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["lane", "status", "created_at"])]
