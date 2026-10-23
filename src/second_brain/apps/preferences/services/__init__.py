"""Application operations for user settings and the application catalog.

Own validation and transaction boundaries here. Accept ordinary arguments rather
than HTTP requests; keep long processing work outside database transactions.
"""
