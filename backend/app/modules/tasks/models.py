"""Tasks: proposals the user reviews, and the tasks that confirmation creates."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID


class TaskPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ActionDenied(Exception):
    """The member's role or relationship to the task does not allow this action."""


class InvalidTask(ValueError):
    """The task as submitted cannot be created (bad title, unknown assignee, ...)."""


class IdempotencyConflict(Exception):
    """The idempotency key was already used for a different request."""


class TaskNotFound(Exception):
    """No such task in the member's organisation."""


@dataclass(frozen=True)
class TaskDraft:
    """A task as proposed to, and confirmed by, the user. Nothing is stored until confirmed,
    and a confirmed draft is validated again: the client may have changed it."""

    title: str
    description: str = ""
    assignee_member_id: UUID | None = None
    assignee_name: str | None = None
    due_date: date | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    evidence_source_id: UUID | None = None
    evidence_title: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskProposal:
    request: str
    drafts: tuple[TaskDraft, ...]
    note: str = ""  # shown when nothing could be proposed

    @property
    def is_empty(self) -> bool:
        return not self.drafts


@dataclass(frozen=True)
class Task:
    id: UUID
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    created_by: UUID
    created_by_name: str
    assigned_to: UUID | None
    assigned_to_name: str | None
    due_date: date | None
    evidence_source_id: UUID | None
    evidence_title: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TaskEvent:
    event_type: str
    actor_name: str
    old_value: dict | None
    new_value: dict | None
    created_at: datetime


@dataclass(frozen=True)
class CreateResult:
    task: Task
    replayed: bool = False  # True when the idempotency key had already created this task
