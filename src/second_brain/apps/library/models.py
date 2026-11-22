"""Source identities and triage decisions in the vault database."""

from django.db import models


class Source(models.Model):
    file_path = models.TextField(primary_key=True)
    sha256 = models.TextField()
    source_type = models.TextField()
    status = models.TextField(default="pending")
    parse_lane = models.TextField(null=True)
    raw_output = models.TextField(null=True)
    ingested_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    content_hash = models.TextField(null=True)
    error = models.TextField(null=True)
    origin_path = models.TextField(null=True, unique=True)
    origin_sha256 = models.TextField(null=True)

    class Meta:
        db_table = "manifest"
        indexes = [
            models.Index(fields=["status"], name="idx_manifest_status"),
            models.Index(fields=["sha256"], name="idx_manifest_hash"),
        ]


class TriageDecision(models.Model):
    raw_path = models.TextField(primary_key=True)
    decision = models.TextField()
    confidence = models.FloatField()
    reason = models.TextField(null=True)
    triaged_at = models.DateTimeField()

    class Meta:
        db_table = "triage"
