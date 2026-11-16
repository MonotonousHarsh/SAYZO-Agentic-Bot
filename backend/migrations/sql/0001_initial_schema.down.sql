-- Reverses 0001. Drops every SAYZO table (and its data). Keeps the schema itself, because
-- Alembic's version table lives in it, and keeps the shared pgvector extension.
SET LOCAL search_path TO sayzo, public, extensions;

DROP TABLE IF EXISTS
    action_idempotency,
    task_events,
    tasks,
    message_citations,
    messages,
    conversations,
    relationship_evidence,
    relationships,
    entities,
    knowledge_chunks,
    source_access_groups,
    source_authors,
    sources,
    projects,
    access_group_members,
    access_groups,
    organisation_members,
    profiles,
    organisations;

DROP FUNCTION IF EXISTS forbid_update();
