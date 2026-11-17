"""Ask a question inside a conversation and keep the exchange.

The answer is generated before the write transaction opens (it is slow). The question, the
reply and its citation snapshots are then stored together. A retried request with the same
client_request_id returns the stored reply instead of answering, and storing, twice.
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.modules.answering import Answer, AnswerService, AnswerStatus, Citation
from app.modules.conversations.models import ChatTurn, ConversationNotFound, StoredMessage
from app.modules.conversations.repository import ConversationRepository
from app.modules.identity import AccessContext
from app.modules.tasks import ActionDenied, TaskProposal, TaskProposalService, is_task_request
from app.shared.database import UnitOfWork
from app.shared.llm import LLMError

TITLE_CHARS = 80
MAX_REQUEST_ID_CHARS = 100
STATUS_TASK_PROPOSED = "TASK_PROPOSED"
STATUS_DENIED = "DENIED"


class ChatService:
    def __init__(
        self,
        unit_of_work: Callable[[], UnitOfWork],
        answers: AnswerService,
        proposals: TaskProposalService | None = None,
    ) -> None:
        self._unit_of_work = unit_of_work
        self._answers = answers
        self._proposals = proposals

    def ask(
        self,
        access: AccessContext,
        question: str,
        conversation_id: UUID | None = None,
        client_request_id: str | None = None,
    ) -> ChatTurn:
        if client_request_id is not None and not 0 < len(client_request_id) <= MAX_REQUEST_ID_CHARS:
            raise ValueError(f"client_request_id must be 1-{MAX_REQUEST_ID_CHARS} characters")
        if conversation_id is not None:
            with self._unit_of_work() as uow:
                repository = ConversationRepository(uow.connection)
                self._check_owner(repository, access, conversation_id)
                if client_request_id is not None:
                    stored = repository.reply_for_request(
                        access.organisation_id, conversation_id, client_request_id
                    )
                    if stored is not None:
                        return ChatTurn(conversation_id, stored, replayed=True)

        response, proposal = self._respond(access, question)

        try:
            with self._unit_of_work() as uow:
                repository = ConversationRepository(uow.connection)
                if conversation_id is None:
                    conversation_id = repository.create(
                        access.organisation_id, access.member_id, _title(question)
                    )
                else:
                    self._check_owner(repository, access, conversation_id)
                repository.add_message(
                    access.organisation_id, conversation_id, "USER", question.strip()
                )
                reply_id = repository.add_message(
                    access.organisation_id,
                    conversation_id,
                    "ASSISTANT",
                    response.text,
                    response.status,
                    client_request_id,
                )
                repository.add_citations(access.organisation_id, reply_id, response.citations)
                (reply,) = repository.messages(
                    access.organisation_id, conversation_id, only=reply_id
                )
        except IntegrityError:
            # A concurrent retry stored the same request first: return that reply.
            if conversation_id is None or client_request_id is None:
                raise
            with self._unit_of_work() as uow:
                stored = ConversationRepository(uow.connection).reply_for_request(
                    access.organisation_id, conversation_id, client_request_id
                )
            if stored is None:
                raise
            return ChatTurn(conversation_id, stored, replayed=True)
        return ChatTurn(conversation_id, reply, proposal=proposal)

    def _respond(self, access: AccessContext, message: str) -> tuple["_Reply", TaskProposal | None]:
        """Task requests get a proposal for the user to confirm; everything else an answer.
        Which path is taken is decided by code, never by the model."""
        if self._proposals is None or not is_task_request(message):
            return _Reply.of(self._answers.answer(access, message)), None
        try:
            proposal = self._proposals.propose(access, message)
        except ActionDenied as error:
            return _Reply(STATUS_DENIED, str(error)), None
        except LLMError:
            return _Reply(AnswerStatus.FAILED.value, "I couldn't draft tasks right now."), None
        return _Reply(STATUS_TASK_PROPOSED, describe(proposal)), proposal

    def history(self, access: AccessContext, conversation_id: UUID) -> list[StoredMessage]:
        with self._unit_of_work() as uow:
            repository = ConversationRepository(uow.connection)
            self._check_owner(repository, access, conversation_id)
            return repository.messages(access.organisation_id, conversation_id)

    @staticmethod
    def _check_owner(
        repository: ConversationRepository, access: AccessContext, conversation_id: UUID
    ) -> None:
        if repository.owner_of(access.organisation_id, conversation_id) != access.member_id:
            raise ConversationNotFound("conversation not found")


@dataclass(frozen=True)
class _Reply:
    status: str
    text: str
    citations: tuple[Citation, ...] = ()

    @classmethod
    def of(cls, answer: Answer) -> "_Reply":
        return cls(answer.status.value, answer.text, answer.citations)


def describe(proposal: TaskProposal) -> str:
    """The proposal as the stored reply text. Nothing is created until the user confirms."""
    if proposal.is_empty:
        return proposal.note
    lines = [f"I can create {len(proposal.drafts)} task(s). Please review and confirm:"]
    for n, draft in enumerate(proposal.drafts, start=1):
        details = [
            draft.assignee_name or "unassigned",
            f"due {draft.due_date:%d %b %Y}" if draft.due_date else "no due date",
            draft.priority.value.lower(),
        ]
        source = f" (from {draft.evidence_title})" if draft.evidence_title else ""
        lines.append(f"{n}. {draft.title} - {', '.join(details)}{source}")
        lines += [f"   ! {warning}" for warning in draft.warnings]
    return "\n".join(lines)


def _title(question: str) -> str:
    title = " ".join(question.split())
    return title if len(title) <= TITLE_CHARS else title[: TITLE_CHARS - 1] + "…"
