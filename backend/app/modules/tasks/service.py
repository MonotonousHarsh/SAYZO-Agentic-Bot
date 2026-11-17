"""Create and update tasks: permission check, re-validation, idempotency and audit, all in
one transaction, so a task never exists without its CREATED event and idempotency record."""

import hashlib
import json
from collections.abc import Callable
from uuid import UUID

from app.modules.identity import AccessContext, MemberDirectory
from app.modules.tasks.models import (
    CreateResult,
    IdempotencyConflict,
    InvalidTask,
    Task,
    TaskDraft,
    TaskEvent,
    TaskNotFound,
    TaskPriority,
    TaskStatus,
)
from app.modules.tasks.policy import TaskPolicy
from app.modules.tasks.repository import TaskRepository
from app.shared.database import UnitOfWork
from app.shared.text import clean_text

ACTION_CREATE_TASK = "CREATE_TASK"
MAX_KEY_CHARS = 200
MAX_DESCRIPTION_CHARS = 4000


class TaskService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        policy: TaskPolicy | None = None,
        timezone: str = "Asia/Kolkata",
    ) -> None:
        self._unit_of_work = unit_of_work
        self._policy = policy or TaskPolicy()
        self._timezone = timezone

    def create(
        self,
        access: AccessContext,
        draft: TaskDraft,
        idempotency_key: str,
        conversation_id: UUID | None = None,
        message_id: UUID | None = None,
    ) -> CreateResult:
        self._policy.ensure_can_create(access)
        if not 0 < len(idempotency_key) <= MAX_KEY_CHARS:
            raise InvalidTask(f"idempotency key must be 1-{MAX_KEY_CHARS} characters")
        title = " ".join(clean_text(draft.title).split())
        description = clean_text(draft.description) or None
        if not 3 <= len(title) <= 200:
            raise InvalidTask("title must be 3-200 characters")
        if description is not None and len(description) > MAX_DESCRIPTION_CHARS:
            raise InvalidTask(f"description must be at most {MAX_DESCRIPTION_CHARS} characters")
        priority = TaskPriority(draft.priority)
        request_hash = _hash(title, description, priority, draft, conversation_id, message_id)

        with self._unit_of_work() as uow:
            repository = TaskRepository(uow.connection, self._timezone)
            existing = repository.claim(
                access.organisation_id,
                idempotency_key,
                ACTION_CREATE_TASK,
                request_hash,
                access.member_id,
            )
            if existing is not None:
                return self._replay(
                    repository, access, existing.request_hash, request_hash, existing.result_task_id
                )

            self._validate_links(uow, repository, access, draft, conversation_id, message_id)
            task_id = repository.insert(
                access.organisation_id,
                title=title,
                description=description,
                priority=priority,
                created_by=access.member_id,
                assigned_to=draft.assignee_member_id,
                due_date=draft.due_date,
                evidence_source_id=draft.evidence_source_id,
                conversation_id=conversation_id,
                message_id=message_id,
            )
            task = repository.get(access.organisation_id, task_id)
            assert task is not None
            repository.add_event(
                access.organisation_id, task_id, access.member_id, "CREATED", None, _snapshot(task)
            )
            repository.complete(access.organisation_id, idempotency_key, task_id)
        return CreateResult(task)

    def update_status(self, access: AccessContext, task_id: UUID, status: TaskStatus) -> Task:
        status = TaskStatus(status)
        with self._unit_of_work() as uow:
            repository = TaskRepository(uow.connection, self._timezone)
            task = repository.get(access.organisation_id, task_id)
            if task is None:
                raise TaskNotFound("task not found")
            self._policy.ensure_can_update(access, task)
            if task.status is status:
                return task
            repository.set_status(access.organisation_id, task_id, status)
            repository.add_event(
                access.organisation_id,
                task_id,
                access.member_id,
                "STATUS_CHANGED",
                {"status": task.status.value},
                {"status": status.value},
            )
            updated = repository.get(access.organisation_id, task_id)
        assert updated is not None
        return updated

    def list_tasks(self, access: AccessContext, assigned_to_me: bool = False) -> list[Task]:
        with self._unit_of_work() as uow:
            return TaskRepository(uow.connection, self._timezone).list_tasks(
                access.organisation_id, access.member_id if assigned_to_me else None
            )

    def history(self, access: AccessContext, task_id: UUID) -> list[TaskEvent]:
        with self._unit_of_work() as uow:
            repository = TaskRepository(uow.connection, self._timezone)
            if repository.get(access.organisation_id, task_id) is None:
                raise TaskNotFound("task not found")
            return repository.events(access.organisation_id, task_id)

    # -- helpers -------------------------------------------------------------------------------

    @staticmethod
    def _replay(repository, access, stored_hash, request_hash, task_id) -> CreateResult:  # type: ignore[no-untyped-def]
        if stored_hash != request_hash:
            raise IdempotencyConflict("this idempotency key was used for a different task")
        task = None if task_id is None else repository.get(access.organisation_id, task_id)
        if task is None:
            raise IdempotencyConflict("the task created with this key no longer exists")
        return CreateResult(task, replayed=True)

    @staticmethod
    def _validate_links(uow, repository, access, draft, conversation_id, message_id) -> None:  # type: ignore[no-untyped-def]
        """The draft came from the client: check every id it carries against the database."""
        org = access.organisation_id
        if draft.assignee_member_id is not None and not MemberDirectory(
            uow.connection
        ).is_active_member(org, draft.assignee_member_id):
            raise InvalidTask("the assignee is not an active member of this organisation")
        if draft.evidence_source_id is not None and (
            repository.visible_source_title(access, draft.evidence_source_id) is None
        ):
            raise InvalidTask("the evidence source was not found")
        if message_id is not None and conversation_id is None:
            raise InvalidTask("a message id needs its conversation id")
        if conversation_id is not None:
            if repository.conversation_owner(org, conversation_id) != access.member_id:
                raise InvalidTask("the conversation was not found")
            if message_id is not None and not repository.message_in_conversation(
                org, conversation_id, message_id
            ):
                raise InvalidTask("the message was not found in that conversation")


def _snapshot(task: Task) -> dict:
    return {
        "title": task.title,
        "status": task.status.value,
        "priority": task.priority.value,
        "assigned_to": None if task.assigned_to is None else str(task.assigned_to),
        "due_date": None if task.due_date is None else task.due_date.isoformat(),
        "evidence_source_id": (
            None if task.evidence_source_id is None else str(task.evidence_source_id)
        ),
    }


def _hash(title, description, priority, draft, conversation_id, message_id) -> str:  # type: ignore[no-untyped-def]
    payload = {
        "title": title,
        "description": description,
        "priority": priority.value,
        "assignee": None if draft.assignee_member_id is None else str(draft.assignee_member_id),
        "due": None if draft.due_date is None else draft.due_date.isoformat(),
        "evidence": None if draft.evidence_source_id is None else str(draft.evidence_source_id),
        "conversation": None if conversation_id is None else str(conversation_id),
        "message": None if message_id is None else str(message_id),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
