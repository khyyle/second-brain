-- Frozen pre-Django schema changes for version 2; database-adoption tests only.

ALTER TABLE compiled ADD COLUMN content_hash TEXT;

ALTER TABLE deferred_sources ADD COLUMN content_hash TEXT;
