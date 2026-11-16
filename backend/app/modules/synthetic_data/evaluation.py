"""Ground-truth evaluation questions: what a user asks and how SAYZO must behave."""

from datetime import date
from enum import StrEnum

from pydantic import Field

from app.modules.sources.common import StrictModel


class ExpectedBehaviour(StrEnum):
    ANSWER = "ANSWER"  # answer, citing the expected sources
    ABSTAIN = "ABSTAIN"  # say the accessible information does not support an answer
    CONFLICT = "CONFLICT"  # disclose that the sources disagree, citing both sides
    TASK_PROPOSAL = "TASK_PROPOSAL"  # propose the expected tasks for confirmation
    DENIED = "DENIED"  # refuse: the user's role does not allow the action


class ExpectedTask(StrictModel):
    title_contains: str
    assignee_id: str
    due_date: date


class EvaluationQuestion(StrictModel):
    id: str
    organisation_id: str
    asked_by: str
    question: str
    expected_behaviour: ExpectedBehaviour
    expected_source_ids: tuple[str, ...] = ()
    forbidden_source_ids: tuple[str, ...] = ()
    expected_answer_contains: tuple[str, ...] = ()
    forbidden_answer_contains: tuple[str, ...] = ()
    expected_tasks: tuple[ExpectedTask, ...] = ()
    forbids_actions: bool = False
    tests: str = Field(description="What this question is designed to test.")


class EvaluationSet(StrictModel):
    questions: tuple[EvaluationQuestion, ...]
