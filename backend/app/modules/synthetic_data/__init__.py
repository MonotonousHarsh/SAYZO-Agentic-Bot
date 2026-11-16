"""Synthetic company dataset: loading, ground-truth evaluation questions and validation."""

from app.modules.synthetic_data.loader import DatasetLoadError, SyntheticDataset, load_dataset
from app.modules.synthetic_data.validation import (
    DatasetValidator,
    Severity,
    ValidationIssue,
    ValidationReport,
)

__all__ = [
    "DatasetLoadError",
    "DatasetValidator",
    "Severity",
    "SyntheticDataset",
    "ValidationIssue",
    "ValidationReport",
    "load_dataset",
]
