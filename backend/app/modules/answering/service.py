"""Answer a question from what the asker may see, with checked citations.

Flow: retrieve (access-filtered) -> keep only relevant evidence -> abstain early if none ->
ask the model for a cited draft -> validate citations, one corrective retry -> renumber.
The model never sees a chunk the asker cannot see, so it cannot leak one.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime

from app.modules.answering.citations import CheckedAnswer, check
from app.modules.answering.evidence import EvidenceSelector
from app.modules.answering.models import Answer, AnswerStatus, Citation
from app.modules.answering.prompt import SYSTEM_PROMPT, DraftAnswer, build_user_prompt
from app.modules.identity import AccessContext
from app.modules.retrieval import RetrievalResult, RetrievalService, SearchHit
from app.shared.llm import LLMError, LLMProvider

logger = logging.getLogger(__name__)

NO_EVIDENCE = (
    "I couldn't find anything in the sources you have access to that answers this question."
)
UNAVAILABLE = "I couldn't generate an answer right now. Please try again in a moment."
UNGROUNDED = "I couldn't produce an answer that is properly backed by your sources."
EXCERPT_CHARS = 300
MAX_DRAFTS = 2  # the first draft plus one corrective retry


class AnswerService:
    def __init__(
        self,
        retrieval: RetrievalService,
        llm: LLMProvider,
        selector: EvidenceSelector | None = None,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._retrieval = retrieval
        self._llm = llm
        self._selector = selector or EvidenceSelector()
        self._today = today

    def answer(self, access: AccessContext, question: str) -> Answer:
        result = self._retrieval.search(access, question)
        sources = self._selector.select(result)
        if not sources:
            return Answer(AnswerStatus.INSUFFICIENT_EVIDENCE, NO_EVIDENCE, (), result)

        try:
            checked = self._draft(result, sources)
        except LLMError as error:
            logger.warning("Answer generation failed: %s", error)
            return Answer(AnswerStatus.FAILED, UNAVAILABLE, (), result)
        if checked is None:
            return Answer(AnswerStatus.FAILED, UNGROUNDED, (), result)

        citations = tuple(
            _citation(index, sources[original - 1])
            for index, original in enumerate(checked.used, start=1)
        )
        return Answer(AnswerStatus(checked.status), checked.text, citations, result)

    def _draft(self, result: RetrievalResult, sources: Sequence[SearchHit]) -> CheckedAnswer | None:
        feedback: Sequence[str] = ()
        for attempt in range(1, MAX_DRAFTS + 1):
            draft = self._llm.generate_structured(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(
                    result.question, sources, result.graph_facts, self._today(), feedback
                ),
                response_schema=DraftAnswer,
                temperature=0.0,
            )
            checked, feedback = check(draft, len(sources))
            if checked is not None:
                return checked
            logger.warning("Rejected draft %d/%d: %s", attempt, MAX_DRAFTS, "; ".join(feedback))
        return None


def _citation(index: int, hit: SearchHit) -> Citation:
    excerpt = " ".join(hit.content.split())
    if len(excerpt) > EXCERPT_CHARS:
        excerpt = excerpt[: EXCERPT_CHARS - 1].rsplit(" ", 1)[0] + "…"
    return Citation(
        index=index,
        chunk_id=hit.chunk_id,
        source_id=hit.source_id,
        source_version=hit.source_version,
        external_id=hit.external_id,
        title=hit.title,
        source_type=hit.source_type,
        location=hit.location,
        authors=hit.authors,
        occurred_at=hit.occurred_at,
        excerpt=excerpt,
    )
