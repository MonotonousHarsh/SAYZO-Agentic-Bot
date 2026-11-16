"""Validate a synthetic dataset folder and print a summary.

Usage (from the backend folder):
    python scripts/validate_dataset.py                  # validates ../datasets/seed
    python scripts/validate_dataset.py path/to/dataset

Exits with code 1 if the dataset has errors, so it can run in CI.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from app.modules.synthetic_data import (
    DatasetLoadError,
    DatasetValidator,
    SyntheticDataset,
    ValidationReport,
    load_dataset,
)

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "datasets" / "seed"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a SAYZO synthetic dataset.")
    parser.add_argument("dataset", nargs="?", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()

    try:
        dataset = load_dataset(args.dataset)
    except DatasetLoadError as error:
        print(f"Could not load dataset: {error}", file=sys.stderr)
        return 1

    report = DatasetValidator().validate(dataset)
    print_summary(dataset)
    print_report(report)
    return 0 if report.is_valid else 1


def print_summary(dataset: SyntheticDataset) -> None:
    manifest = dataset.manifest
    print(f"Dataset {manifest.name} v{manifest.version}")
    for org in dataset.directory.organisations:
        documents = [d for d in dataset.documents if d.organisation_id == org.id]
        by_type = Counter(d.source_type.value for d in documents)
        types = ", ".join(f"{count} {kind}" for kind, count in sorted(by_type.items()))
        print(
            f"  {org.name}: {len(org.people)} people, {len(org.access_groups)} groups, "
            f"{len(documents)} snapshots ({types})"
        )
    behaviours = Counter(q.expected_behaviour.value for q in dataset.questions)
    listed = ", ".join(f"{count} {kind}" for kind, count in sorted(behaviours.items()))
    print(f"  Evaluation questions: {len(dataset.questions)} ({listed})")
    cases = manifest.edge_cases
    print(
        f"  Edge cases: {len(cases.edits)} edits, {len(cases.deletions)} deletions, "
        f"{len(cases.duplicates)} duplicate groups, {len(cases.conflicts)} conflicts, "
        f"{len(cases.restricted)} restricted, {len(cases.prompt_injections)} prompt injections"
    )


def print_report(report: ValidationReport) -> None:
    for issue in report.issues:
        print(f"  {issue}")
    status = "VALID" if report.is_valid else "INVALID"
    print(f"Result: {status} ({len(report.errors)} errors, {len(report.warnings)} warnings)")


if __name__ == "__main__":
    sys.exit(main())
