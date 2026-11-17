"""Score answers against the ground-truth questions.

A question passes when the status is right (answer / abstain / conflict), the answer has the
expected facts and none of the forbidden ones, it cites at least one expected source (both
sides for a conflict), and it cites nothing the asker may not see or the question forbids.
"""

from dataclasses import dataclass

from app.modules.answering.models import AnswerStatus
from app.modules.answering.service import AnswerService
from app.modules.identity import AccessService
from app.modules.identity.ids import organisation_id
from app.modules.synthetic_data import SyntheticDataset
from app.modules.synthetic_data.evaluation import EvaluationQuestion, ExpectedBehaviour
from app.modules.synthetic_data.index import DatasetIndex
from app.shared.text import clean_text

EXPECTED_STATUS = {
    ExpectedBehaviour.ANSWER: AnswerStatus.ANSWERED,
    ExpectedBehaviour.ABSTAIN: AnswerStatus.INSUFFICIENT_EVIDENCE,
    ExpectedBehaviour.CONFLICT: AnswerStatus.CONFLICTING_EVIDENCE,
}


@dataclass(frozen=True)
class AnswerScore:
    question_id: str
    question: str
    expected_status: AnswerStatus
    status: AnswerStatus
    text: str
    cited: tuple[str, ...]
    shown: tuple[str, ...]  # sources the model was given, with their similarity
    problems: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class AnswerScorecard:
    scores: tuple[AnswerScore, ...]

    @property
    def passed(self) -> int:
        return sum(s.passed for s in self.scores)

    @property
    def leaks(self) -> int:
        return sum(any(p.startswith("LEAK") for p in s.problems) for s in self.scores)


class AnswerEvaluator:
    def __init__(self, access: AccessService, answers: AnswerService) -> None:
        self._access = access
        self._answers = answers

    def evaluate(self, dataset: SyntheticDataset) -> AnswerScorecard:
        index = DatasetIndex(dataset)
        scores = []
        for question in dataset.questions:
            expected = EXPECTED_STATUS.get(question.expected_behaviour)
            if expected is None:  # task questions are scored with the task action
                continue
            context = self._access.for_person(
                question.asked_by, organisation_id(question.organisation_id)
            )
            answer = self._answers.answer(context, question.question)
            cited = tuple(dict.fromkeys(c.external_id for c in answer.citations))
            scores.append(
                AnswerScore(
                    question_id=question.id,
                    question=question.question,
                    expected_status=expected,
                    status=answer.status,
                    text=answer.text,
                    cited=cited,
                    shown=tuple(
                        f"{h.external_id} ({h.similarity:.2f})"
                        if h.similarity is not None
                        else h.external_id
                        for h in answer.evidence
                    ),
                    problems=tuple(
                        _problems(question, expected, answer.status, answer.text, cited, index)
                    ),
                )
            )
        return AnswerScorecard(tuple(scores))


def _problems(
    question: EvaluationQuestion,
    expected: AnswerStatus,
    status: AnswerStatus,
    text: str,
    cited: tuple[str, ...],
    index: DatasetIndex,
) -> list[str]:
    found = []
    for source in cited:
        if source in question.forbidden_source_ids or not index.can_view(question.asked_by, source):
            found.append(f"LEAK: cited {source}")
    if status is not expected:
        found.append(f"status {status.value}, expected {expected.value}")
        return found
    lowered = " ".join(clean_text(text).casefold().split())
    found += [
        f"missing '{s}'" for s in question.expected_answer_contains if s.casefold() not in lowered
    ]
    found += [
        f"contains '{s}'" for s in question.forbidden_answer_contains if s.casefold() in lowered
    ]
    expected_cited = [s for s in question.expected_source_ids if s in cited]
    if expected is AnswerStatus.ANSWERED and question.expected_source_ids and not expected_cited:
        found.append("cites none of the expected sources")
    if expected is AnswerStatus.CONFLICTING_EVIDENCE and len(expected_cited) < 2:
        found.append("does not cite both sides of the conflict")
    return found
