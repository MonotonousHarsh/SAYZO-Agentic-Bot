"""Ingest a dataset into the database: sources, chunks, embeddings and the relationship graph.

Usage (from the backend folder, after `alembic upgrade head`):
    python scripts/ingest_dataset.py                                   # the seed dataset
    python scripts/ingest_dataset.py --dataset ../datasets/generated/sayzo_generated_v1
    python scripts/ingest_dataset.py --as-of 2026-11-10T12:00:00+05:30 # state as of that moment
    python scripts/ingest_dataset.py --no-graph                        # skip LLM graph extraction

Re-running is safe: unchanged sources are skipped, edited ones re-indexed, deleted ones removed.
`--as-of` replays the sources as they were at an earlier moment, which makes a good demo:
ingest as of 10 Nov, then run again without it and watch the edit and the deletion arrive.
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.modules.identity import DirectorySeeder
from app.modules.ingestion import IngestionReport, IngestionService
from app.modules.ingestion.graph import LLMGraphExtractor
from app.modules.synthetic_data import DatasetLoadError
from app.modules.synthetic_data.loader import load_raw_dataset, to_synthetic_dataset
from app.shared.database import UnitOfWork, create_database_engine
from app.shared.embeddings.sentence_transformer_provider import SentenceTransformerEmbeddings
from app.shared.llm.groq_provider import GroqProvider

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "datasets" / "seed"


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    if settings.database_url is None:
        print("DATABASE_URL is not set (add it to the .env file).", file=sys.stderr)
        return 1

    try:
        raw = load_raw_dataset(args.dataset)
        documents = to_synthetic_dataset(raw).documents
    except DatasetLoadError as error:
        print(f"Could not load the dataset: {error}", file=sys.stderr)
        return 1
    if args.as_of is not None:
        documents = tuple(d for d in documents if d.occurred_at <= args.as_of)
        print(f"Replaying sources as of {args.as_of.isoformat()} ({len(documents)} snapshots)")

    engine = create_database_engine(settings.database_url.get_secret_value())
    DirectorySeeder(lambda: UnitOfWork(engine)).seed(raw.directory)

    graph_extractor = None
    if args.no_graph:
        print("Graph extraction: skipped (--no-graph)")
    elif settings.groq_api_key is None:
        print("Graph extraction: skipped (GROQ_API_KEY is not set)")
    else:
        llm = GroqProvider(
            api_key=settings.groq_api_key.get_secret_value(),
            model=settings.groq_model,
            base_url=settings.groq_base_url,
            timeout_seconds=settings.groq_timeout_seconds,
            requests_per_minute=settings.groq_requests_per_minute or None,
        )
        graph_extractor = LLMGraphExtractor(llm)
        print(f"Graph extraction: {settings.groq_model}")

    print(f"Loading embedding model {settings.embedding_model} (first run downloads it)...")
    embedder = SentenceTransformerEmbeddings(
        settings.embedding_model,
        expected_dimension=settings.embedding_dimension,
        device=settings.embedding_device,
    )

    service = IngestionService(
        lambda: UnitOfWork(engine), embedder, graph_extractor=graph_extractor
    )
    report = service.ingest(documents)
    print_report(report)
    return 1 if report.failures else 0


def print_report(report: IngestionReport) -> None:
    print(
        f"Sources: {report.created} created, {report.updated} updated, "
        f"{report.deleted} deleted, {report.unchanged} unchanged, {report.ignored} ignored"
    )
    print(
        f"Duplicates: {report.duplicates} stored without indexing, "
        f"{report.collapsed_snapshots} repeated snapshots collapsed"
    )
    print(
        f"Index: {report.chunks_written} chunks written, {report.indexed_later} sources "
        f"indexed in the follow-up pass, {report.relationships_written} relationships"
    )
    if report.graph_failures:
        print(f"Graph extraction calls that failed (chunks still indexed): {report.graph_failures}")
    if report.graph_rejections:
        print(f"Graph proposals rejected by validation: {len(report.graph_rejections)}")
        for reason in report.graph_rejections[:10]:
            print(f"  - {reason}")
    for failure in report.failures:
        print(f"FAILED {failure}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest a SAYZO dataset into the database.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--as-of",
        type=datetime.fromisoformat,
        default=None,
        help="only use snapshots at or before this ISO time (with a timezone)",
    )
    parser.add_argument("--no-graph", action="store_true", help="skip LLM graph extraction")
    args = parser.parse_args()
    if args.as_of is not None and args.as_of.tzinfo is None:
        parser.error("--as-of needs a timezone, e.g. 2026-11-10T12:00:00+05:30")
    return args


if __name__ == "__main__":
    sys.exit(main())
