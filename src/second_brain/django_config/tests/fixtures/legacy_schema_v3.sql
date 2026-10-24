-- Frozen pre-Django schema changes for version 3; database-adoption tests only.

ALTER TABLE manifest ADD COLUMN origin_path TEXT;

ALTER TABLE manifest ADD COLUMN origin_sha256 TEXT;

CREATE UNIQUE INDEX idx_manifest_origin ON manifest(origin_path);
