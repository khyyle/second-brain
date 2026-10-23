"""Application operations for source identities, provenance, and triage decisions.

Own validation and transaction boundaries here. Accept ordinary arguments rather
than HTTP requests; keep long processing work outside database transactions.
"""
