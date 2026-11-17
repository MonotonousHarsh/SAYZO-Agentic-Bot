SET LOCAL search_path TO sayzo, public, extensions;

DROP INDEX IF EXISTS ix_sources_duplicate_of;
ALTER TABLE sources DROP CONSTRAINT IF EXISTS ck_source_not_own_duplicate;
ALTER TABLE sources DROP CONSTRAINT IF EXISTS fk_source_duplicate_same_org;
ALTER TABLE sources DROP COLUMN IF EXISTS duplicate_of_source_id;
