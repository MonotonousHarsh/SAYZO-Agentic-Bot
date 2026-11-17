"""FastAPI application: routes, error mapping and the static chat UI.

Every route resolves the caller to an AccessContext from the database first; services then
enforce visibility (access groups) and permissions (roles) themselves, so the API layer
cannot accidentally skip a check.
"""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.auth import AuthError
from app.api.container import Container
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationOut,
    CreateTaskRequest,
    CreateTaskResponse,
    DevPerson,
    MemberOut,
    MeResponse,
    MessageOut,
    PublicConfig,
    TaskDraftOut,
    TaskEventOut,
    TaskOut,
    UpdateTaskRequest,
)
from app.modules.conversations import ConversationNotFound
from app.modules.identity import AccessContext, AccessDenied
from app.modules.tasks import (
    ActionDenied,
    IdempotencyConflict,
    InvalidTask,
    TaskNotFound,
)

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

ERRORS: dict[type[Exception], int] = {
    AuthError: 401,
    AccessDenied: 403,
    ActionDenied: 403,
    ConversationNotFound: 404,
    TaskNotFound: 404,
    IdempotencyConflict: 409,
    InvalidTask: 422,
}


def create_app(container_factory: Callable[[], Container]) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        app.state.container = container_factory()  # loads the embedding model once
        yield

    app = FastAPI(title="SAYZO", version="0.1.0", lifespan=lifespan)

    for error_type, status in ERRORS.items():
        app.add_exception_handler(error_type, _handler(status))

    def container(request: Request) -> Container:
        return request.app.state.container

    def current_access(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        x_dev_user: Annotated[str | None, Header()] = None,
        x_organisation_id: Annotated[UUID | None, Header()] = None,
    ) -> AccessContext:
        services = container(request)
        identity = services.authenticator.authenticate(authorization, x_dev_user)
        if identity.person_key is not None:
            return services.access.for_person(identity.person_key, x_organisation_id)
        assert identity.auth_user_id is not None and identity.email is not None
        return services.access.for_auth_user(
            identity.auth_user_id, identity.email, x_organisation_id
        )

    Access = Annotated[AccessContext, Depends(current_access)]
    Services = Annotated[Container, Depends(container)]

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    def config(services: Services) -> PublicConfig:
        return PublicConfig(
            auth_mode=services.auth_mode,
            supabase_url=services.supabase_url,
            supabase_publishable_key=services.supabase_publishable_key,
            dev_people=[DevPerson(**p) for p in services.dev_people],
        )

    @app.get("/api/me")
    def me(access: Access, services: Services) -> MeResponse:
        name, groups = services.access.organisation_name_and_groups(access)
        return MeResponse(
            member_id=access.member_id,
            full_name=access.full_name,
            role=access.role.value,
            organisation_id=access.organisation_id,
            organisation_name=name,
            access_groups=groups,
        )

    @app.get("/api/members")
    def members(access: Access, services: Services) -> list[MemberOut]:
        """Active members of my organisation, for choosing a task assignee."""
        return [
            MemberOut(member_id=m.member_id, full_name=m.full_name)
            for m in services.access.members(access)
        ]

    @app.post("/api/chat")
    def chat(body: ChatRequest, access: Access, services: Services) -> ChatResponse:
        try:
            turn = services.chat.ask(
                access, body.message, body.conversation_id, body.client_request_id
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return ChatResponse(
            conversation_id=turn.conversation_id,
            reply=MessageOut.of(turn.reply),
            replayed=turn.replayed,
            proposed_tasks=(
                None
                if turn.proposal is None
                else [TaskDraftOut.of(d) for d in turn.proposal.drafts]
            ),
        )

    @app.get("/api/conversations")
    def conversations(access: Access, services: Services) -> list[ConversationOut]:
        return [
            ConversationOut(id=c.id, title=c.title, updated_at=c.updated_at)
            for c in services.chat.conversations(access)
        ]

    @app.get("/api/conversations/{conversation_id}/messages")
    def messages(conversation_id: UUID, access: Access, services: Services) -> list[MessageOut]:
        return [MessageOut.of(m) for m in services.chat.history(access, conversation_id)]

    @app.post("/api/tasks", status_code=201)
    def create_task(
        body: CreateTaskRequest, access: Access, services: Services
    ) -> CreateTaskResponse:
        result = services.tasks.create(
            access,
            body.draft.to_draft(),
            body.idempotency_key,
            body.conversation_id,
            body.message_id,
        )
        return CreateTaskResponse(task=TaskOut.of(result.task), replayed=result.replayed)

    @app.get("/api/tasks")
    def list_tasks(access: Access, services: Services, mine: bool = False) -> list[TaskOut]:
        return [TaskOut.of(t) for t in services.tasks.list_tasks(access, assigned_to_me=mine)]

    @app.patch("/api/tasks/{task_id}")
    def update_task(
        task_id: UUID, body: UpdateTaskRequest, access: Access, services: Services
    ) -> TaskOut:
        return TaskOut.of(services.tasks.update_status(access, task_id, body.status))

    @app.get("/api/tasks/{task_id}/events")
    def task_events(task_id: UUID, access: Access, services: Services) -> list[TaskEventOut]:
        return [TaskEventOut.of(e) for e in services.tasks.history(access, task_id)]

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _handler(status: int):  # type: ignore[no-untyped-def]
    async def handle(request: Request, error: Exception) -> JSONResponse:
        if status >= 500:
            logger.exception("Unhandled error", exc_info=error)
        return JSONResponse({"detail": str(error)}, status_code=status)

    return handle
