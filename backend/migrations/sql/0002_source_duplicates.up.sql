-- Exact-duplicate tracking. A source whose content is identical to another live source in the
-- same organisation is stored (so its metadata is not lost) but points at the original and gets
-- no chunks of its own, so the same text is never retrieved or cited twice.
SET LOCAL search_path TO sayzo, public, extensions;

ALTER TABLE sources ADD COLUMN duplicate_of_source_id uuid;
ALTER TABLE sources ADD CONSTRAINT fk_source_duplicate_same_org
    FOREIGN KEY (duplicate_of_source_id, organisation_id)
    REFERENCES sources (id, organisation_id) ON DELETE SET NULL (duplicate_of_source_id);
ALTER TABLE sources ADD CONSTRAINT ck_source_not_own_duplicate
    CHECK (duplicate_of_source_id <> id);
CREATE INDEX ix_sources_duplicate_of ON sources (duplicate_of_source_id);
