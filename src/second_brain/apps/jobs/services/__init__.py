"""Application operations for job lifecycle, progress, attempts, and cancellation.

Own validation and transaction boundaries here. Accept ordinary arguments rather
than HTTP requests; keep long processing work outside database transactions.
"""
