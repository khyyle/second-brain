"""Application operations for build planning and compilation.

Own validation and transaction boundaries here. Accept ordinary arguments rather
than HTTP requests; keep long processing work outside database transactions.
"""
