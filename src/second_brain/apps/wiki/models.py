"""User decisions about wiki health findings; page content remains Markdown."""

from django.db import models


class DismissedDuplicate(models.Model):
    pk = models.CompositePrimaryKey("stem_a", "stem_b")
    stem_a = models.TextField()
    stem_b = models.TextField()
    dismissed_at = models.DateTimeField()

    class Meta:
        db_table = "dismissed_duplicates"
