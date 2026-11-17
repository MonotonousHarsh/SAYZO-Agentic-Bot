"""Run every ground-truth question through retrieval and score it.

Usage (from the backend folder, after ingesting the same dataset):
    python scripts/evaluate_retrieval.py
    python scripts/evaluate_retrieval.py --dataset ../datasets/generated/sayzo_generated_v1

Prints recall (expected sources found) per question and checks that nothing leaked: no
source the asker may not see, no deleted source, nothing the question forbids. Exits 1 on
any leak, because that is a security failure, not a quality one.
"""

import argparse
import sys
from pathlib import Path

from app.config import get_settings
from app.modules.identity import AccessService
from app.modules.retrieval import RetrievalEvaluator, RetrievalService, RetrievalSettings
from app.modules.synthetic_data import DatasetLoadError, load_dataset
from app.shared.database import UnitOfWork, create_database_engine
from app.shared.embeddings.sentence_transformer_provider import SentenceTransformerEmbeddings

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "datasets" / "seed"


def main() -> int:
    args = parse_args()
    settings = get_settings()
    if settings.database_url is None:
        print("DATABASE_URL is not set (add it to the .env file).", file=sys.stderr)
        return 1
    try:
        dataset = load_dataset(args.dataset)
    except DatasetLoadError as error:
        print(f"Could not load the dataset: {error}", file=sys.stderr)
        return 1

    engine = create_database_engine(settings.database_url.get_secret_value())
    unit_of_work = lambda: UnitOfWork(engine)  # noqa: E731
    embedder = SentenceTransformerEmbeddings(
        settings.embedding_model,
        expected_dimension=settings.embedding_dimension,
        device=settings.embedding_device,
    )
    retrieval = RetrievalService(unit_of_work, embedder, RetrievalSettings(max_results=args.k))
    card = RetrievalEvaluator(AccessService(unit_of_work), retrieval).evaluate(dataset)

    for score in card.scores:
        recall = "  n/a" if score.recall is None else f"{score.recall:5.0%}"
        missing = [s for s in score.expected if s not in score.found]
        flag = "LEAK" if score.leaked else "ok  "
        print(
            f"{score.question_id}  {flag} recall {recall}  {score.behaviour:<13} {score.question}"
        )
        if missing:
            print(f"      missing: {', '.join(missing)}")
        if score.leaked:
            print(f"      LEAKED: {', '.join(score.leaked)}")
    print(f"\nMean recall@{args.k}: {card.mean_recall:.0%}   Leaks: {card.leaks}")
    return 1 if card.leaks else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--k", type=int, default=8, help="results per question")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
