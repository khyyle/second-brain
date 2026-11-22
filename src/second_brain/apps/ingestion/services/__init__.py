"""Application operations for imports, capture, parsing, and ingestion.

Own validation and transaction boundaries here. Accept ordinary arguments rather
than HTTP requests; keep long processing work outside database transactions.
"""
