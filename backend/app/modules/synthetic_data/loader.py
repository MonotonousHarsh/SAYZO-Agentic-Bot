"""Reads and writes dataset folders.

A dataset folder holds one JSON file per source system plus the directory, manifest and
evaluation questions. `RawDataset` keeps each file in its raw format (needed to write data
back out); `SyntheticDataset` is the same data with every source converted into
`SourceDocument` snapshots (what validation and ingestion work with).
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.modules.sources import (
    Directory,
    MeetingExport,
    NotionExport,
    SlackExport,
    SourceDocument,
    SourceFormatError,
    meetings_to_documents,
    notion_to_documents,
    slack_to_documents,
)
from app.modules.synthetic_data.evaluation import EvaluationQuestion, EvaluationSet
from app.modules.synthetic_data.manifest import DatasetManifest

MANIFEST_FILE = "manifest.json"
DIRECTORY_FILE = "directory.json"
SLACK_FILE = "slack_export.json"
NOTION_FILE = "notion_pages.json"
MEETINGS_FILE = "meeting_transcripts.json"
EVALUATION_FILE = "evaluation_questions.json"

ModelT = TypeVar("ModelT", bound=BaseModel)


class DatasetLoadError(Exception):
    """A dataset file is missing, is not valid JSON or does not match its schema."""


@dataclass(frozen=True)
class RawDataset:
    manifest: DatasetManifest
    directory: Directory
    slack: SlackExport
    notion: NotionExport
    meetings: MeetingExport
    evaluation: EvaluationSet


@dataclass(frozen=True)
class SyntheticDataset:
    manifest: DatasetManifest
    directory: Directory
    documents: tuple[SourceDocument, ...]
    questions: tuple[EvaluationQuestion, ...]


def load_raw_dataset(folder: Path) -> RawDataset:
    return RawDataset(
        manifest=_read_model(folder / MANIFEST_FILE, DatasetManifest),
        directory=_read_model(folder / DIRECTORY_FILE, Directory),
        slack=_read_model(folder / SLACK_FILE, SlackExport),
        notion=_read_model(folder / NOTION_FILE, NotionExport),
        meetings=_read_model(folder / MEETINGS_FILE, MeetingExport),
        evaluation=_read_model(folder / EVALUATION_FILE, EvaluationSet),
    )


def to_synthetic_dataset(raw: RawDataset) -> SyntheticDataset:
    names = raw.directory.people_names()
    try:
        documents = (
            *slack_to_documents(raw.slack, names),
            *notion_to_documents(raw.notion),
            *meetings_to_documents(raw.meetings, names),
        )
    except SourceFormatError as error:
        raise DatasetLoadError(str(error)) from error

    return SyntheticDataset(
        manifest=raw.manifest,
        directory=raw.directory,
        documents=documents,
        questions=raw.evaluation.questions,
    )


def load_dataset(folder: Path) -> SyntheticDataset:
    return to_synthetic_dataset(load_raw_dataset(folder))


def write_raw_dataset(raw: RawDataset, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    files: dict[str, BaseModel] = {
        MANIFEST_FILE: raw.manifest,
        DIRECTORY_FILE: raw.directory,
        SLACK_FILE: raw.slack,
        NOTION_FILE: raw.notion,
        MEETINGS_FILE: raw.meetings,
        EVALUATION_FILE: raw.evaluation,
    }
    for name, model in files.items():
        write_json(folder / name, model.model_dump(mode="json"))


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_model(path: Path, model: type[ModelT]) -> ModelT:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise DatasetLoadError(f"missing dataset file: {path}") from error
    try:
        return model.model_validate(json.loads(raw))
    except json.JSONDecodeError as error:
        raise DatasetLoadError(f"{path.name}: invalid JSON ({error})") from error
    except ValidationError as error:
        raise DatasetLoadError(f"{path.name}: does not match its schema\n{error}") from error
