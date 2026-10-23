"""HTTP entry points for job lifecycle, progress, attempts, and cancellation."""

from ninja import Router

router = Router(tags=["Jobs"])
