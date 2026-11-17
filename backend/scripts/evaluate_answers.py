"""Run the ground-truth questions through the full answering pipeline and score them.

Usage (from the backend folder, after ingesting the same dataset):
    python scripts/evaluate_answers.py
    python scripts/evaluate_answers.py --verbose     # print every answer in full

Checks per question: right status (answer / abstain / conflict), expected facts present,
forbidden facts absent, expected sources cited, nothing cited the asker may not see.
Uses one or two Groq calls per question, paced to stay within the free tier.
Exits 1 if any citation leaked.
"""

import argparse
import sys
from pathlib import Path

from app import bootstrap
from app.config import get_settings
from app.modules.answering.evaluation import AnswerEvaluator
from app.modules.identity import AccessService
from app.modules.synthetic_data import DatasetLoadError, load_dataset

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "datasets" / "seed"


def main() -> int:
    args = parse_args()
    settings = get_settings()
    try:
        dataset = load_dataset(args.dataset)
        unit_of_work = bootstrap.unit_of_work_factory(bootstrap.database_engine(settings))
        answers = bootstrap.answer_service(settings, unit_of_work)
    except (bootstrap.ConfigurationError, DatasetLoadError) as error:
        print(error, file=sys.stderr)
        return 1

    card = AnswerEvaluator(AccessService(unit_of_work), answers).evaluate(dataset)
    for score in card.scores:
        mark = "PASS" if score.passed else "FAIL"
        print(f"{score.question_id}  {mark}  {score.status.value:<22} {score.question}")
        for problem in score.problems:
            print(f"      - {problem}")
        if args.verbose or not score.passed:
            print(f"      answer: {' '.join(score.text.split())}")
            print(f"      cited:  {', '.join(score.cited) or 'nothing'}")
            print(f"      shown:  {', '.join(score.shown) or 'nothing'}")
    print(f"\nPassed {card.passed}/{len(card.scores)}   Citation leaks: {card.leaks}")
    return 1 if card.leaks else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
