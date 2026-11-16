from pathlib import Path

import pytest

from app.modules.synthetic_data import SyntheticDataset, load_dataset

SEED_DATASET = Path(__file__).resolve().parents[2] / "datasets" / "seed"


@pytest.fixture(scope="session")
def seed_dataset() -> SyntheticDataset:
    return load_dataset(SEED_DATASET)
