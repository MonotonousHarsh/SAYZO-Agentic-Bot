-- SAYZO initial schema.
--
-- Conventions
--   * Every tenant-owned table has organisation_id.
--   * Tables that other tenant-owned rows point at have UNIQUE (id, organisation_id), and those
--     references are composite foreign keys on (x_id, organisation_id). The database therefore
--     refuses any row that links records from two different organisations.
--   * Status-like columns are text with CHECK constraints (easier to evolve than enums).
--   * Row level security is enabled on every table with no policies: Supabase's public API
--     roles (anon, authenticated) can read nothing. The backend connects as the table owner
--     and enforces organisation and access-group filters in every query.

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
CREATE SCHEMA IF NOT EXISTS sayzo;
SET LOCAL search_path TO sayzo, public, extensions;

-- ---------------------------------------------------------------------------------------------
-- Identity and access
-- ---------------------------------------------------------------------------------------------

CREATE TABLE organisations (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_key text NOT NULL UNIQUE,
    name         text NOT NULL,
    slug         text NOT NULL UNIQUE,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE profiles (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_key text NOT NULL UNIQUE,
    full_name    text NOT NULL,
    email        text NOT NULL UNIQUE,
    -- Supabase auth.users id, linked when the person signs in for the first time.
    auth_user_id uuid UNIQUE,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE organisation_members (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id uuid NOT NULL REFERENCES organisations (id) ON DELETE CASCADE,
    profile_id      uuid NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    role            text NOT NULL CONSTRAINT ck_member_role
                        CHECK (role IN ('ADMIN', 'MEMBER', 'VIEWER')),
    status          text NOT NULL DEFAULT 'ACTIVE' CONSTRAINT ck_member_status
                        CHECK (status IN ('ACTIVE', 'SUSPENDED')),
    title           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_member_org_profile UNIQUE (organisation_id, profile_id),
    CONSTRAINT uq_member_id_org UNIQUE (id, organisation_id)
);

CREATE TABLE access_groups (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id uuid NOT NULL REFERENCES organisations (id) ON DELETE CASCADE,
    external_key    text NOT NULL UNIQUE,
    name            text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_group_org_name UNIQUE (organisation_id, name),
    CONSTRAINT uq_group_id_org UNIQUE (id, organisation_id)
);

CREATE TABLE access_group_members (
    access_group_id uuid NOT NULL,
    member_id       uuid NOT NULL,
    organisation_id uuid NOT NULL,
    PRIMARY KEY (access_group_id, member_id),
    CONSTRAINT fk_group_member_group_same_org FOREIGN KEY (access_group_id, organisation_id)
        REFERENCES access_groups (id, organisation_id) ON DELETE CASCADE,
    CONSTRAINT fk_group_member_member_same_org FOREIGN KEY (member_id, organisation_id)
        REFERENCES organisation_members (id, organisation_id) ON DELETE CASCADE
);
CREATE INDEX ix_group_members_member ON access_group_members (member_id);

CREATE TABLE projects (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id uuid NOT NULL REFERENCES organisations (id) ON DELETE CASCADE,
    external_key    text NOT NULL UNIQUE,
    name            text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_project_id_org UNIQUE (id, organisation_id)
);

-- ---------------------------------------------------------------------------------------------
-- Knowledge: sources (the source of truth) and their searchable chunks
-- ---------------------------------------------------------------------------------------------

CREATE TABLE sources (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id uuid NOT NULL REFERENCES organisations (id) ON DELETE CASCADE,
    source_system   text NOT NULL CONSTRAINT ck_source_system
                        CHECK (source_system IN ('slack', 'notion', 'meetings', 'upload')),
    external_id     text NOT NULL,
    source_type     text NOT NULL CONSTRAINT ck_source_type
                        CHECK (source_type IN ('message', 'document', 'meeting_transcript')),
    title           text NOT NULL,
    location        text NOT NULL,
    project_id      uuid,
    content         text NOT NULL,
    content_hash    text NOT NULL,
    version         integer NOT NULL CONSTRAINT ck_source_version CHECK (version >= 1),
    status          text NOT NULL DEFAULT 'ACTIVE' CONSTRAINT ck_source_status
                        CHECK (status IN ('ACTIVE', 'DELETED')),
    occurred_at     timestamptz NOT NULL,
    deleted_at      timestamptz,
    -- When the current version's chunks were written. NULL means not searchable yet.
    indexed_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_source_external UNIQUE (organisation_id, source_system, external_id),
    CONSTRAINT uq_source_id_org UNIQUE (id, organisation_id),
    CONSTRAINT fk_source_project_same_org FOREIGN KEY (project_id, organisation_id)
        REFERENCES projects (id, organisation_id),
    CONSTRAINT ck_source_deleted_consistent
        CHECK ((status = 'DELETED') = (deleted_at IS NOT NULL))
);
CREATE INDEX ix_sources_org_hash ON sources (organisation_id, content_hash);
CREATE INDEX ix_sources_org_status ON sources (organisation_id, status);

CREATE TABLE source_authors (
    source_id       uuid NOT NULL,
    member_id       uuid NOT NULL,
    organisation_id uuid NOT NULL,
    PRIMARY KEY (source_id, member_id),
    CONSTRAINT fk_source_author_source_same_org FOREIGN KEY (source_id, organisation_id)
        REFERENCES sources (id, organisation_id) ON DELETE CASCADE,
    CONSTRAINT fk_source_author_member_same_org FOREIGN KEY (member_id, organisation_id)
        REFERENCES organisation_members (id, organisation_id) ON DELETE CASCADE
);

CREATE TABLE source_access_groups (
    source_id       uuid NOT NULL,
    access_group_id uuid NOT NULL,
    organisation_id uuid NOT NULL,
    PRIMARY KEY (source_id, access_group_id),
    CONSTRAINT fk_source_group_source_same_org FOREIGN KEY (source_id, organisation_id)
        REFERENCES sources (id, organisation_id) ON DELETE CASCADE,
    CONSTRAINT fk_source_group_group_same_org FOREIGN KEY (access_group_id, organisation_id)
        REFERENCES access_groups (id, organisation_id) ON DELETE CASCADE
);
CREATE INDEX ix_source_groups_group ON source_access_groups (access_group_id);

CREATE TABLE knowledge_chunks (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id uuid NOT NULL,
    source_id       uuid NOT NULL,
    source_version  integer NOT NULL,
    chunk_index     integer NOT NULL CONSTRAINT ck_chunk_index CHECK (chunk_index >= 0),
    content         text NOT NULL,
    token_count     integer NOT NULL,
    -- 768 = output size of BAAI/bge-base-en-v1.5. A different model needs a new migration.
    embedding       vector(768) NOT NULL,
    embedding_model text NOT NULL,
    -- Keyword index for the lexical half of hybrid search.
    content_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_chunk_source_same_org FOREIGN KEY (source_id, organisation_id)
        REFERENCES sources (id, organisation_id) ON DELETE CASCADE,
    CONSTRAINT uq_chunk_position UNIQUE (source_id, chunk_index),
    CONSTRAINT uq_chunk_id_org UNIQUE (id, organisation_id)
);
CREATE INDEX ix_chunks_org_source ON knowledge_chunks (organisation_id, source_id);
CREATE INDEX ix_chunks_embedding ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_chunks_tsv ON knowledge_chunks USING gin (content_tsv);

-- ---------------------------------------------------------------------------------------------
-- Graph: entities and relationships, every relationship backed by evidence chunks
-- ---------------------------------------------------------------------------------------------

CREATE TABLE entities (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id uuid NOT NULL REFERENCES organisations (id) ON DELETE CASCADE,
    entity_type     text NOT NULL CONSTRAINT ck_entity_type CHECK (entity_type IN
                        ('PERSON', 'PROJECT', 'TEAM', 'TASK', 'TECHNOLOGY', 'CUSTOMER',
                         'PRODUCT', 'OTHER')),
    canonical_name  text NOT NULL,
    normalized_name text NOT NULL,
    -- Set when a PERSON entity is resolved to a known organisation member.
    member_id       uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_entity_name UNIQUE (organisation_id, entity_type, normalized_name),
    CONSTRAINT uq_entity_id_org UNIQUE (id, organisation_id),
    CONSTRAINT fk_entity_member_same_org FOREIGN KEY (member_id, organisation_id)
        REFERENCES organisation_members (id, organisation_id)
);

CREATE TABLE relationships (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id   uuid NOT NULL,
    subject_entity_id uuid NOT NULL,
    relationship_type text NOT NULL CONSTRAINT ck_relationship_type CHECK (relationship_type IN
                          ('OWNS', 'RESPONSIBLE_FOR', 'ASSIGNED_TO', 'WORKS_ON', 'DEPENDS_ON',
                           'BLOCKED_BY', 'USES', 'PART_OF', 'DECIDED')),
    object_entity_id  uuid NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_rel_subject_same_org FOREIGN KEY (subject_entity_id, organisation_id)
        REFERENCES entities (id, organisation_id) ON DELETE CASCADE,
    CONSTRAINT fk_rel_object_same_org FOREIGN KEY (object_entity_id, organisation_id)
        REFERENCES entities (id, organisation_id) ON DELETE CASCADE,
    CONSTRAINT uq_relationship UNIQUE
        (organisation_id, subject_entity_id, relationship_type, object_entity_id),
    CONSTRAINT uq_relationship_id_org UNIQUE (id, organisation_id),
    CONSTRAINT ck_relationship_not_self CHECK (subject_entity_id <> object_entity_id)
);
CREATE INDEX ix_rel_subject ON relationships (organisation_id, subject_entity_id, relationship_type);
CREATE INDEX ix_rel_object ON relationships (organisation_id, object_entity_id, relationship_type);

-- A relationship is only usable while at least one evidence chunk exists. Re-indexing a source
-- deletes its old chunks, which deletes their evidence rows with them.
CREATE TABLE relationship_evidence (
    relationship_id       uuid NOT NULL,
    chunk_id              uuid NOT NULL,
    organisation_id       uuid NOT NULL,
    extraction_confidence real,
    PRIMARY KEY (relationship_id, chunk_id),
    CONSTRAINT fk_evidence_rel_same_org FOREIGN KEY (relationship_id, organisation_id)
        REFERENCES relationships (id, organisation_id) ON DELETE CASCADE,
    CONSTRAINT fk_evidence_chunk_same_org FOREIGN KEY (chunk_id, organisation_id)
        REFERENCES knowledge_chunks (id, organisation_id) ON DELETE CASCADE
);
CREATE INDEX ix_evidence_chunk ON relationship_evidence (chunk_id);

-- ---------------------------------------------------------------------------------------------
-- Conversations, messages and citations
-- ---------------------------------------------------------------------------------------------

CREATE TABLE conversations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id uuid NOT NULL,
    created_by      uuid NOT NULL,
    title           text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    CONSTRAINT fk_conversation_member_same_org FOREIGN KEY (created_by, organisation_id)
        REFERENCES organisation_members (id, organisation_id) ON DELETE CASCADE,
    CONSTRAINT uq_conversation_id_org UNIQUE (id, organisation_id)
);
CREATE INDEX ix_conversations_owner ON conversations (organisation_id, created_by, updated_at);

CREATE TABLE messages (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id   uuid NOT NULL,
    conversation_id   uuid NOT NULL,
    role              text NOT NULL CONSTRAINT ck_message_role
                          CHECK (role IN ('USER', 'ASSISTANT')),
    content           text NOT NULL,
    answer_status     text CONSTRAINT ck_message_answer_status CHECK (answer_status IN
                          ('ANSWERED', 'INSUFFICIENT_EVIDENCE', 'CONFLICTING_EVIDENCE',
                           'CLARIFICATION_REQUIRED', 'TASK_PROPOSED', 'DENIED', 'FAILED')),
    -- Lets a retried request return the original answer instead of creating a second one.
    client_request_id text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_message_conversation_same_org FOREIGN KEY (conversation_id, organisation_id)
        REFERENCES conversations (id, organisation_id) ON DELETE CASCADE,
    CONSTRAINT uq_message_request UNIQUE (conversation_id, client_request_id),
    CONSTRAINT uq_message_id_org UNIQUE (id, organisation_id)
);
CREATE INDEX ix_messages_conversation ON messages (conversation_id, created_at);

-- Citations keep a snapshot (title, version, excerpt) so they stay readable after the source is
-- edited or re-indexed; the links to the live source and chunk become NULL when those go away.
CREATE TABLE message_citations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id uuid NOT NULL,
    message_id      uuid NOT NULL,
    citation_index  integer NOT NULL CONSTRAINT ck_citation_index CHECK (citation_index >= 1),
    source_id       uuid,
    chunk_id        uuid,
    source_version  integer NOT NULL,
    source_title    text NOT NULL,
    excerpt         text NOT NULL,
    CONSTRAINT fk_citation_message_same_org FOREIGN KEY (message_id, organisation_id)
        REFERENCES messages (id, organisation_id) ON DELETE CASCADE,
    CONSTRAINT fk_citation_source_same_org FOREIGN KEY (source_id, organisation_id)
        REFERENCES sources (id, organisation_id) ON DELETE SET NULL (source_id),
    CONSTRAINT fk_citation_chunk_same_org FOREIGN KEY (chunk_id, organisation_id)
        REFERENCES knowledge_chunks (id, organisation_id) ON DELETE SET NULL (chunk_id),
    CONSTRAINT uq_citation_position UNIQUE (message_id, citation_index)
);

-- ---------------------------------------------------------------------------------------------
-- Tasks, their audit trail and idempotent action execution
-- ---------------------------------------------------------------------------------------------

CREATE TABLE tasks (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id        uuid NOT NULL,
    title                  text NOT NULL CONSTRAINT ck_task_title
                               CHECK (char_length(title) BETWEEN 3 AND 200),
    description            text,
    priority               text NOT NULL DEFAULT 'MEDIUM' CONSTRAINT ck_task_priority
                               CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH', 'URGENT')),
    status                 text NOT NULL DEFAULT 'PENDING' CONSTRAINT ck_task_status
                               CHECK (status IN
                                   ('PENDING', 'IN_PROGRESS', 'BLOCKED', 'COMPLETED', 'CANCELLED')),
    created_by             uuid NOT NULL,
    assigned_to            uuid,
    due_at                 timestamptz,
    source_conversation_id uuid,
    source_message_id      uuid,
    evidence_source_id     uuid,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    completed_at           timestamptz,
    CONSTRAINT fk_task_creator_same_org FOREIGN KEY (created_by, organisation_id)
        REFERENCES organisation_members (id, organisation_id),
    CONSTRAINT fk_task_assignee_same_org FOREIGN KEY (assigned_to, organisation_id)
        REFERENCES organisation_members (id, organisation_id),
    CONSTRAINT fk_task_conversation_same_org FOREIGN KEY (source_conversation_id, organisation_id)
        REFERENCES conversations (id, organisation_id) ON DELETE SET NULL (source_conversation_id),
    CONSTRAINT fk_task_message_same_org FOREIGN KEY (source_message_id, organisation_id)
        REFERENCES messages (id, organisation_id) ON DELETE SET NULL (source_message_id),
    CONSTRAINT fk_task_evidence_same_org FOREIGN KEY (evidence_source_id, organisation_id)
        REFERENCES sources (id, organisation_id) ON DELETE SET NULL (evidence_source_id),
    CONSTRAINT uq_task_id_org UNIQUE (id, organisation_id)
);
CREATE INDEX ix_tasks_org_status ON tasks (organisation_id, status);
CREATE INDEX ix_tasks_org_assignee ON tasks (organisation_id, assigned_to);

CREATE TABLE task_events (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id uuid NOT NULL,
    task_id         uuid NOT NULL,
    actor_member_id uuid NOT NULL,
    event_type      text NOT NULL CONSTRAINT ck_task_event_type
                        CHECK (event_type IN ('CREATED', 'STATUS_CHANGED', 'ASSIGNED', 'UPDATED')),
    old_value       jsonb,
    new_value       jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_task_event_task_same_org FOREIGN KEY (task_id, organisation_id)
        REFERENCES tasks (id, organisation_id) ON DELETE CASCADE,
    CONSTRAINT fk_task_event_actor_same_org FOREIGN KEY (actor_member_id, organisation_id)
        REFERENCES organisation_members (id, organisation_id)
);
CREATE INDEX ix_task_events_task ON task_events (task_id, created_at);

-- The audit trail is append-only: existing events can never be rewritten.
CREATE FUNCTION forbid_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME;
END;
$$;
CREATE TRIGGER trg_task_events_append_only
    BEFORE UPDATE ON task_events FOR EACH ROW EXECUTE FUNCTION forbid_update();

CREATE TABLE action_idempotency (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id uuid NOT NULL REFERENCES organisations (id) ON DELETE CASCADE,
    idempotency_key text NOT NULL,
    action_type     text NOT NULL,
    request_hash    text NOT NULL,
    status          text NOT NULL CONSTRAINT ck_idempotency_status
                        CHECK (status IN ('PROCESSING', 'SUCCEEDED', 'FAILED')),
    created_by      uuid NOT NULL,
    result_task_id  uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    CONSTRAINT uq_idempotency_key UNIQUE (organisation_id, idempotency_key),
    CONSTRAINT fk_idempotency_member_same_org FOREIGN KEY (created_by, organisation_id)
        REFERENCES organisation_members (id, organisation_id),
    CONSTRAINT fk_idempotency_task_same_org FOREIGN KEY (result_task_id, organisation_id)
        REFERENCES tasks (id, organisation_id) ON DELETE SET NULL (result_task_id)
);

-- ---------------------------------------------------------------------------------------------
-- Lock the schema away from Supabase's public API roles
-- ---------------------------------------------------------------------------------------------

DO $$
DECLARE
    table_name text;
BEGIN
    FOR table_name IN
        SELECT tablename FROM pg_tables WHERE schemaname = 'sayzo' AND tablename <> 'alembic_version'
    LOOP
        EXECUTE format('ALTER TABLE sayzo.%I ENABLE ROW LEVEL SECURITY', table_name);
    END LOOP;

    -- These roles exist on Supabase only; a plain Postgres install skips this step.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON SCHEMA sayzo FROM anon, authenticated;
        REVOKE ALL ON ALL TABLES IN SCHEMA sayzo FROM anon, authenticated;
    END IF;
END;
$$;
