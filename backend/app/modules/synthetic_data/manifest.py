"""Dataset manifest: what the dataset is, its time window and the edge cases it contains."""

from datetime import datetime

from pydantic import AwareDatetime, Field, model_validator

from app.modules.sources.common import StrictModel


class TimeWindow(StrictModel):
    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def start_before_end(self) -> "TimeWindow":
        if self.start >= self.end:
            raise ValueError("time window start must be before end")
        return self

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end


class EdgeCases(StrictModel):
    """Deliberate test situations, declared so the validator can check they really exist."""

    edits: tuple[str, ...] = ()
    deletions: tuple[str, ...] = ()
    # Each group lists the external ids whose snapshots share identical content.
    # A single id means the same snapshot appears twice (e.g. a re-exported message).
    duplicates: tuple[tuple[str, ...], ...] = ()
    conflicts: tuple[tuple[str, ...], ...] = ()
    restricted: tuple[str, ...] = ()
    prompt_injections: tuple[str, ...] = ()


class DatasetManifest(StrictModel):
    name: str
    version: str
    description: str
    time_window: TimeWindow
    edge_cases: EdgeCases = Field(default_factory=EdgeCases)
