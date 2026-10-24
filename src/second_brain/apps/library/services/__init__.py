"""Application operations for source identities, provenance, and triage decisions.

Own validation and transaction boundaries here. Accept ordinary arguments rather
than HTTP requests; keep long processing work outside database transactions.

source_queries: list/fetch Source database records.
raw_files: locate/read raw Markdown and manage reversible file moves.
source_actions: delete sources or clear their build records for recompilation.
manual_triage: list and apply user Keep/Review/Skip decisions.
library_state: derive the combined library summary from records and files.
"""
