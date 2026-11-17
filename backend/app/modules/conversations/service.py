"""Ask a question inside a conversation and keep the exchange.

The answer is generated before the write transaction opens (it is slow). The question, the
reply and its citation snapshots are then stored together. A retried request with the same
client_request_id returns the stored reply instead of answering, and storing, twice.
"""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.modules.answering import AnswerService
from app.modules.conversations.models import ChatTurn, ConversationNotFound, StoredMessage
from app.modules.conversations.repository import ConversationRepository
from app.modules.identity import AccessContext
from app.shared.database import UnitOfWork

TITLE_CHARS = 80
MAX_REQUEST_ID_CHARS = 100


class ChatService:
    def __init__(self, unit_of_work: Callable[[], UnitOfWork], answers: AnswerService) -> None:
        self._unit_of_work = unit_of_work
        self._answers = answers

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

        answer = self._answers.answer(access, question)

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
                    answer.text,
                    answer.status.value,
                    client_request_id,
                )
                repository.add_citations(access.organisation_id, reply_id, answer.citations)
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
        return ChatTurn(conversation_id, reply)

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


def _title(question: str) -> str:
    title = " ".join(question.split())
    return title if len(title) <= TITLE_CHARS else title[: TITLE_CHARS - 1] + "…"
