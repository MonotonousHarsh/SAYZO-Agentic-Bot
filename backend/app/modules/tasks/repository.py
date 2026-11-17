"""SQL for tasks, their audit events and idempotent creation. Scoped by organisation;
methods never commit."""

import json
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text

from app.modules.identity import AccessContext
from app.modules.tasks.models import Task, TaskEvent, TaskPriority, TaskStatus

_TASK_SELECT = """
    SELECT t.id, t.title, t.description, t.status, t.priority, t.created_by,
           cp.full_name AS created_by_name, t.assigned_to, ap.full_name AS assigned_to_name,
           t.due_at, t.evidence_source_id, s.title AS evidence_title, t.created_at, t.updated_at
    FROM tasks t
    JOIN organisation_members cm ON cm.id = t.created_by
    JOIN profiles cp ON cp.id = cm.profile_id
    LEFT JOIN organisation_members am ON am.id = t.assigned_to
    LEFT JOIN profiles ap ON ap.id = am.profile_id
    LEFT JOIN sources s ON s.id = t.evidence_source_id
"""


@dataclass(frozen=True)
class IdempotencyRecord:
    id: UUID
    request_hash: str
    status: str
    result_task_id: UUID | None


class TaskRepository:
    def __init__(self, connection: Connection, timezone: str) -> None:
        self._db = connection
        self._timezone = timezone

    # -- idempotency ---------------------------------------------------------------------------

    def claim(
        self, organisation_id: UUID, key: str, action: str, request_hash: str, member_id: UUID
    ) -> IdempotencyRecord | None:
        """Claim a key for this request. Returns None when newly claimed, or the existing
        record. A concurrent claim of the same key waits here until the first one commits."""
        claimed = self._db.execute(
            text(
                """
                INSERT INTO action_idempotency
                    (organisation_id, idempotency_key, action_type, request_hash, status,
                     created_by)
                VALUES (:org, :key, :action, :hash, 'PROCESSING', :member)
                ON CONFLICT (organisation_id, idempotency_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "org": organisation_id,
                "key": key,
                "action": action,
                "hash": request_hash,
                "member": member_id,
            },
        ).scalar_one_or_none()
        if claimed is not None:
            return None
        row = self._db.execute(
            text(
                "SELECT id, request_hash, status, result_task_id FROM action_idempotency "
                "WHERE organisation_id = :org AND idempotency_key = :key"
            ),
            {"org": organisation_id, "key": key},
        ).one()
        return IdempotencyRecord(row.id, row.request_hash, row.status, row.result_task_id)

    def complete(self, organisation_id: UUID, key: str, task_id: UUID) -> None:
        self._db.execute(
            text(
                "UPDATE action_idempotency SET status = 'SUCCEEDED', result_task_id = :task, "
                "completed_at = now() WHERE organisation_id = :org AND idempotency_key = :key"
            ),
            {"org": organisation_id, "key": key, "task": task_id},
        )

    # -- checks --------------------------------------------------------------------------------

    def visible_source_title(self, access: AccessContext, source_id: UUID) -> str | None:
        """The source's title if the member may see it (same rule as retrieval)."""
        return self._db.execute(
            text(
                """
                SELECT s.title FROM sources s
                WHERE s.id = :id AND s.organisation_id = :org AND s.status = 'ACTIVE'
                  AND EXISTS (
                      SELECT 1 FROM source_access_groups sag
                      WHERE sag.source_id = s.id AND sag.organisation_id = s.organisation_id
                        AND sag.access_group_id = ANY(CAST(:groups AS uuid[])))
                """
            ),
            {
                "id": source_id,
                "org": access.organisation_id,
                "groups": [str(g) for g in access.access_group_ids],
            },
        ).scalar_one_or_none()

    def conversation_owner(self, organisation_id: UUID, conversation_id: UUID) -> UUID | None:
        return self._db.execute(
            text(
                "SELECT created_by FROM conversations WHERE id = :c AND organisation_id = :org "
                "AND deleted_at IS NULL"
            ),
            {"c": conversation_id, "org": organisation_id},
        ).scalar_one_or_none()

    def message_in_conversation(
        self, organisation_id: UUID, conversation_id: UUID, message_id: UUID
    ) -> bool:
        return self._db.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM messages WHERE id = :m AND conversation_id = :c "
                "AND organisation_id = :org)"
            ),
            {"m": message_id, "c": conversation_id, "org": organisation_id},
        ).scalar_one()

    # -- tasks ---------------------------------------------------------------------------------

    def insert(
        self,
        organisation_id: UUID,
        *,
        title: str,
        description: str | None,
        priority: TaskPriority,
        created_by: UUID,
        assigned_to: UUID | None,
        due_date: date | None,
        evidence_source_id: UUID | None,
        conversation_id: UUID | None,
        message_id: UUID | None,
    ) -> UUID:
        return self._db.execute(
            text(
                """
                INSERT INTO tasks (organisation_id, title, description, priority, created_by,
                    assigned_to, due_at, evidence_source_id, source_conversation_id,
                    source_message_id)
                VALUES (:org, :title, :description, :priority, :created_by, :assigned_to,
                    CASE WHEN CAST(:due AS date) IS NULL THEN NULL
                         ELSE (CAST(:due AS date) + time '23:59') AT TIME ZONE :tz END,
                    :evidence, :conversation, :message)
                RETURNING id
                """
            ),
            {
                "org": organisation_id,
                "title": title,
                "description": description,
                "priority": priority.value,
                "created_by": created_by,
                "assigned_to": assigned_to,
                "due": due_date,
                "tz": self._timezone,
                "evidence": evidence_source_id,
                "conversation": conversation_id,
                "message": message_id,
            },
        ).scalar_one()

    def get(self, organisation_id: UUID, task_id: UUID) -> Task | None:
        row = self._db.execute(
            text(_TASK_SELECT + " WHERE t.organisation_id = :org AND t.id = :id"),
            {"org": organisation_id, "id": task_id},
        ).one_or_none()
        return None if row is None else self._task(row)

    def list_tasks(
        self, organisation_id: UUID, assigned_to: UUID | None = None, limit: int = 100
    ) -> list[Task]:
        rows = self._db.execute(
            text(
                _TASK_SELECT + " WHERE t.organisation_id = :org "
                "AND (CAST(:assignee AS uuid) IS NULL OR t.assigned_to = CAST(:assignee AS uuid)) "
                "ORDER BY t.due_at NULLS LAST, t.created_at, t.id LIMIT :limit"
            ),
            {"org": organisation_id, "assignee": assigned_to, "limit": limit},
        ).all()
        return [self._task(row) for row in rows]

    def set_status(self, organisation_id: UUID, task_id: UUID, status: TaskStatus) -> None:
        self._db.execute(
            text(
                """
                UPDATE tasks SET status = :status, updated_at = now(),
                    completed_at = CASE WHEN :status = 'COMPLETED' THEN now() END
                WHERE organisation_id = :org AND id = :id
                """
            ),
            {"org": organisation_id, "id": task_id, "status": status.value},
        )

    def add_event(
        self,
        organisation_id: UUID,
        task_id: UUID,
        actor: UUID,
        event_type: str,
        old: dict | None,
        new: dict | None,
    ) -> None:
        self._db.execute(
            text(
                """
                INSERT INTO task_events (organisation_id, task_id, actor_member_id, event_type,
                                         old_value, new_value)
                VALUES (:org, :task, :actor, :type, CAST(:old AS jsonb), CAST(:new AS jsonb))
                """
            ),
            {
                "org": organisation_id,
                "task": task_id,
                "actor": actor,
                "type": event_type,
                "old": None if old is None else json.dumps(old),
                "new": None if new is None else json.dumps(new),
            },
        )

    def events(self, organisation_id: UUID, task_id: UUID) -> list[TaskEvent]:
        rows = self._db.execute(
            text(
                """
                SELECT e.event_type, p.full_name, e.old_value, e.new_value, e.created_at
                FROM task_events e
                JOIN organisation_members m ON m.id = e.actor_member_id
                JOIN profiles p ON p.id = m.profile_id
                WHERE e.organisation_id = :org AND e.task_id = :task
                ORDER BY e.created_at, e.id
                """
            ),
            {"org": organisation_id, "task": task_id},
        ).all()
        return [
            TaskEvent(r.event_type, r.full_name, r.old_value, r.new_value, r.created_at)
            for r in rows
        ]

    def _task(self, row) -> Task:  # type: ignore[no-untyped-def]
        due: datetime | None = row.due_at
        return Task(
            id=row.id,
            title=row.title,
            description=row.description,
            status=TaskStatus(row.status),
            priority=TaskPriority(row.priority),
            created_by=row.created_by,
            created_by_name=row.created_by_name,
            assigned_to=row.assigned_to,
            assigned_to_name=row.assigned_to_name,
            due_date=None if due is None else self._local_date(due),
            evidence_source_id=row.evidence_source_id,
            evidence_title=row.evidence_title,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _local_date(self, moment: datetime) -> date:
        return moment.astimezone(ZoneInfo(self._timezone)).date()
