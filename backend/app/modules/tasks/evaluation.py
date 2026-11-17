"""Score the task questions: proposals must contain the expected tasks (title, assignee, due
date), and roles that may not act must be refused before anything is proposed."""

from dataclasses import dataclass

from app.modules.identity import AccessService
from app.modules.identity.ids import member_id, organisation_id
from app.modules.synthetic_data import SyntheticDataset
from app.modules.synthetic_data.evaluation import ExpectedBehaviour
from app.modules.tasks.models import ActionDenied, TaskProposal
from app.modules.tasks.proposals import TaskProposalService


@dataclass(frozen=True)
class TaskScore:
    question_id: str
    question: str
    expected: str
    outcome: str
    proposal: TaskProposal | None
    problems: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.problems


class TaskEvaluator:
    def __init__(self, access: AccessService, proposals: TaskProposalService) -> None:
        self._access = access
        self._proposals = proposals

    def evaluate(self, dataset: SyntheticDataset) -> tuple[TaskScore, ...]:
        scores = []
        for question in dataset.questions:
            if question.expected_behaviour not in (
                ExpectedBehaviour.TASK_PROPOSAL,
                ExpectedBehaviour.DENIED,
            ):
                continue
            context = self._access.for_person(
                question.asked_by, organisation_id(question.organisation_id)
            )
            problems: list[str] = []
            proposal = None
            try:
                proposal = self._proposals.propose(context, question.question)
                outcome = f"{len(proposal.drafts)} task(s) proposed"
            except ActionDenied:
                outcome = "denied"

            if question.expected_behaviour is ExpectedBehaviour.DENIED:
                if proposal is not None:
                    problems.append("expected the request to be denied")
            elif proposal is None:
                problems.append("expected a proposal, got denied")
            else:
                for task in question.expected_tasks:
                    assignee = member_id(question.organisation_id, task.assignee_id)
                    match = next(
                        (
                            d
                            for d in proposal.drafts
                            if task.title_contains.casefold() in d.title.casefold()
                        ),
                        None,
                    )
                    if match is None:
                        problems.append(f"no task about '{task.title_contains}'")
                        continue
                    if match.assignee_member_id != assignee:
                        problems.append(
                            f"'{match.title}' assigned to {match.assignee_name}, "
                            f"expected {task.assignee_id}"
                        )
                    if match.due_date != task.due_date:
                        problems.append(
                            f"'{match.title}' due {match.due_date}, expected {task.due_date}"
                        )
            scores.append(
                TaskScore(
                    question.id,
                    question.question,
                    question.expected_behaviour.value,
                    outcome,
                    proposal,
                    tuple(problems),
                )
            )
        return tuple(scores)
