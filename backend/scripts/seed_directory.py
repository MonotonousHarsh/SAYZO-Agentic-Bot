"""Seed organisations, people, access groups and projects from a dataset into the database.

Usage (from the backend folder, after `alembic upgrade head`):
    python scripts/seed_directory.py                     # uses ../datasets/seed
    python scripts/seed_directory.py --dataset ../datasets/generated/sayzo_generated_v1

Safe to run repeatedly: records are upserted using ids derived from the dataset keys.
"""

import argparse
import sys
from pathlib import Path

from app.config import get_settings
from app.modules.identity import DirectorySeeder
from app.modules.synthetic_data import DatasetLoadError
from app.modules.synthetic_data.loader import load_raw_dataset
from app.shared.database import UnitOfWork, create_database_engine

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "datasets" / "seed"


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the SAYZO directory into the database.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()

    database_url = get_settings().database_url
    if database_url is None:
        print("DATABASE_URL is not set (add it to the .env file).", file=sys.stderr)
        return 1
    try:
        directory = load_raw_dataset(args.dataset).directory
    except DatasetLoadError as error:
        print(f"Could not load the dataset: {error}", file=sys.stderr)
        return 1

    engine = create_database_engine(database_url.get_secret_value())
    summary = DirectorySeeder(lambda: UnitOfWork(engine)).seed(directory)
    print(
        f"Seeded {summary.organisations} organisations, {summary.people} people, "
        f"{summary.access_groups} access groups, {summary.projects} projects, "
        f"{summary.group_memberships} group memberships."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
