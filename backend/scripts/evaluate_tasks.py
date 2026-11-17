"""Score the task questions from the ground truth: the proposal for the Atlas sync action
items, and the refusal for a viewer. Proposals only; nothing is created.

Usage (from the backend folder):  python scripts/evaluate_tasks.py
"""

import sys
from pathlib import Path

from app import bootstrap
from app.config import get_settings
from app.modules.identity import AccessService
from app.modules.synthetic_data import load_dataset
from app.modules.tasks.evaluation import TaskEvaluator

DATASET = Path(__file__).resolve().parents[2] / "datasets" / "seed"


def main() -> int:
    settings = get_settings()
    try:
        unit_of_work = bootstrap.unit_of_work_factory(bootstrap.database_engine(settings))
        proposals = bootstrap.task_proposal_service(settings, unit_of_work)
    except bootstrap.ConfigurationError as error:
        print(error, file=sys.stderr)
        return 1
    scores = TaskEvaluator(AccessService(unit_of_work), proposals).evaluate(load_dataset(DATASET))
    for score in scores:
        print(
            f"{score.question_id}  {'PASS' if score.passed else 'FAIL'}  "
            f"{score.expected:<13} {score.outcome:<20} {score.question}"
        )
        for problem in score.problems:
            print(f"      - {problem}")
        if score.proposal is not None:
            for draft in score.proposal.drafts:
                print(f"      * {draft.title} [{draft.assignee_name}, {draft.due_date}]")
    passed = sum(s.passed for s in scores)
    print(f"\nPassed {passed}/{len(scores)}")
    return 0 if passed == len(scores) else 1


if __name__ == "__main__":
    sys.exit(main())
