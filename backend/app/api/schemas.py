"""Request and response bodies of the HTTP API."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.conversations import StoredCitation, StoredMessage
from app.modules.tasks import Task, TaskDraft, TaskEvent, TaskPriority, TaskStatus


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(Body):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None
    client_request_id: str | None = Field(default=None, min_length=1, max_length=100)


class CitationOut(BaseModel):
    index: int
    source_title: str
    source_version: int
    excerpt: str
    source_id: UUID | None
    chunk_id: UUID | None

    @classmethod
    def of(cls, citation: StoredCitation) -> "CitationOut":
        return cls(
            index=citation.index,
            source_title=citation.source_title,
            source_version=citation.source_version,
            excerpt=citation.excerpt,
            source_id=citation.source_id,
            chunk_id=citation.chunk_id,
        )


class TaskDraftIn(Body):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=4000)
    assignee_member_id: UUID | None = None
    due_date: date | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    evidence_source_id: UUID | None = None

    def to_draft(self) -> TaskDraft:
        return TaskDraft(
            title=self.title,
            description=self.description,
            assignee_member_id=self.assignee_member_id,
            due_date=self.due_date,
            priority=self.priority,
            evidence_source_id=self.evidence_source_id,
        )


class TaskDraftOut(TaskDraftIn):
    model_config = ConfigDict(extra="ignore")
    assignee_name: str | None = None
    evidence_title: str | None = None
    warnings: list[str] = []

    @classmethod
    def of(cls, draft: TaskDraft) -> "TaskDraftOut":
        return cls(
            title=draft.title,
            description=draft.description,
            assignee_member_id=draft.assignee_member_id,
            due_date=draft.due_date,
            priority=draft.priority,
            evidence_source_id=draft.evidence_source_id,
            assignee_name=draft.assignee_name,
            evidence_title=draft.evidence_title,
            warnings=list(draft.warnings),
        )


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    status: str | None
    created_at: datetime
    citations: list[CitationOut]

    @classmethod
    def of(cls, message: StoredMessage) -> "MessageOut":
        return cls(
            id=message.id,
            role=message.role,
            content=message.content,
            status=message.answer_status,
            created_at=message.created_at,
            citations=[CitationOut.of(c) for c in message.citations],
        )


class ChatResponse(BaseModel):
    conversation_id: UUID
    reply: MessageOut
    replayed: bool
    proposed_tasks: list[TaskDraftOut] | None = None


class ConversationOut(BaseModel):
    id: UUID
    title: str
    updated_at: datetime


class CreateTaskRequest(Body):
    draft: TaskDraftIn
    idempotency_key: str = Field(min_length=1, max_length=200)
    conversation_id: UUID | None = None
    message_id: UUID | None = None


class TaskOut(BaseModel):
    id: UUID
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    created_by_name: str
    assigned_to: UUID | None
    assigned_to_name: str | None
    due_date: date | None
    evidence_title: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, task: Task) -> "TaskOut":
        return cls(
            id=task.id,
            title=task.title,
            description=task.description,
            status=task.status,
            priority=task.priority,
            created_by_name=task.created_by_name,
            assigned_to=task.assigned_to,
            assigned_to_name=task.assigned_to_name,
            due_date=task.due_date,
            evidence_title=task.evidence_title,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


class CreateTaskResponse(BaseModel):
    task: TaskOut
    replayed: bool


class UpdateTaskRequest(Body):
    status: TaskStatus


class TaskEventOut(BaseModel):
    event_type: str
    actor_name: str
    old_value: dict | None  # type: ignore[type-arg]
    new_value: dict | None  # type: ignore[type-arg]
    created_at: datetime

    @classmethod
    def of(cls, event: TaskEvent) -> "TaskEventOut":
        return cls(
            event_type=event.event_type,
            actor_name=event.actor_name,
            old_value=event.old_value,
            new_value=event.new_value,
            created_at=event.created_at,
        )


class MeResponse(BaseModel):
    member_id: UUID
    full_name: str
    role: str
    organisation_id: UUID
    organisation_name: str
    access_groups: list[str]


class PublicConfig(BaseModel):
    """Values the browser needs. All public: never put a secret here."""

    auth_mode: str
    supabase_url: str | None
    supabase_publishable_key: str | None
    dev_people: list[str] = []
