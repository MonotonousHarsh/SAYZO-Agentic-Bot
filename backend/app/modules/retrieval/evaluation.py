"""Score retrieval against the ground-truth questions.

Two things are measured per question:
- recall: how many of the expected sources appear in the results;
- leaks: any returned source the asker must not see (checked independently against the
  dataset's own access rule) or that the question explicitly forbids. Leaks must be zero.
"""

from dataclasses import dataclass

from app.modules.identity import AccessService
from app.modules.identity.ids import organisation_id
from app.modules.retrieval.service import RetrievalService
from app.modules.synthetic_data import SyntheticDataset
from app.modules.synthetic_data.evaluation import ExpectedBehaviour
from app.modules.synthetic_data.index import DatasetIndex

# Action questions are about permissions on writes, not about what can be found.
_SKIPPED = {ExpectedBehaviour.DENIED}


@dataclass(frozen=True)
class QuestionScore:
    question_id: str
    question: str
    behaviour: str
    expected: tuple[str, ...]
    returned: tuple[str, ...]
    found: tuple[str, ...]
    leaked: tuple[str, ...]

    @property
    def recall(self) -> float | None:
        return len(self.found) / len(self.expected) if self.expected else None


@dataclass(frozen=True)
class RetrievalScorecard:
    scores: tuple[QuestionScore, ...]

    @property
    def leaks(self) -> int:
        return sum(len(s.leaked) for s in self.scores)

    @property
    def mean_recall(self) -> float:
        recalls = [s.recall for s in self.scores if s.recall is not None]
        return sum(recalls) / len(recalls) if recalls else 1.0


class RetrievalEvaluator:
    def __init__(self, access: AccessService, retrieval: RetrievalService) -> None:
        self._access = access
        self._retrieval = retrieval

    def evaluate(self, dataset: SyntheticDataset) -> RetrievalScorecard:
        index = DatasetIndex(dataset)
        scores = []
        for question in dataset.questions:
            if question.expected_behaviour in _SKIPPED:
                continue
            context = self._access.for_person(
                question.asked_by, organisation_id(question.organisation_id)
            )
            result = self._retrieval.search(context, question.question)
            returned = tuple(dict.fromkeys(hit.external_id for hit in result.hits))
            leaked = tuple(
                external_id
                for external_id in returned
                if external_id in question.forbidden_source_ids
                or not index.can_view(question.asked_by, external_id)
                or not index.is_live(external_id)
            )
            scores.append(
                QuestionScore(
                    question_id=question.id,
                    question=question.question,
                    behaviour=question.expected_behaviour.value,
                    expected=question.expected_source_ids,
                    returned=returned,
                    found=tuple(s for s in question.expected_source_ids if s in returned),
                    leaked=leaked,
                )
            )
        return RetrievalScorecard(tuple(scores))
