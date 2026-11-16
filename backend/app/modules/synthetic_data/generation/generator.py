"""Augments a base dataset with LLM-written topics.

Responsibilities are split so each piece can be tested on its own:
- `ContentFilter` decides whether a piece of generated text may be kept.
- `IdAllocator` hands out ids that cannot clash with existing ones.
- `DatasetAugmenter` asks the LLM for topics and turns the kept text into raw source records.
"""

import random
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.modules.sources import (
    MeetingExport,
    NotionExport,
    Organisation,
    Person,
    SlackExport,
)
from app.modules.sources.common import normalise_text
from app.modules.sources.directory import Project
from app.modules.synthetic_data.generation.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_topic_prompt,
)
from app.modules.synthetic_data.generation.schemas import GeneratedTopic
from app.modules.synthetic_data.loader import RawDataset, to_synthetic_dataset
from app.modules.synthetic_data.manifest import TimeWindow
from app.shared.llm import LLMError, LLMProvider

# Subjects the ground-truth questions depend on. Generated text that touches them could
# silently change the correct answers, so it is rejected (and the LLM is told to avoid them).
DEFAULT_FORBIDDEN_SUBJECTS: tuple[str, ...] = (
    "launch",
    "go live",
    "go-live",
    "pilot",
    "salary",
    "hiring",
    "budget",
    "owner",
    "owns",
    "ownership",
    "migration",
    "database",
    "postgres",
    "redis",
    "session",
    "cloudkite",
)

MIN_WINDOW = timedelta(days=3)
TOPIC_SPAN = timedelta(days=2)


@dataclass(frozen=True)
class GenerationSpec:
    dataset_name: str
    topics_per_project: int = 2
    messages_per_topic: int = 4
    utterances_per_meeting: int = 6
    seed: int = 42
    temperature: float = 0.8
    max_text_length: int = 1500
    forbidden_subjects: tuple[str, ...] = DEFAULT_FORBIDDEN_SUBJECTS


@dataclass(frozen=True)
class DroppedItem:
    kind: str
    reason: str
    text: str


@dataclass
class TopicLog:
    organisation_id: str
    project_id: str
    topic: str
    response: dict[str, Any]
    kept_ids: list[str] = field(default_factory=list)
    dropped: list[DroppedItem] = field(default_factory=list)


@dataclass(frozen=True)
class GenerationResult:
    dataset: RawDataset
    log: dict[str, Any]

    @property
    def kept_count(self) -> int:
        return sum(len(topic["kept_ids"]) for topic in self.log["topics"])

    @property
    def dropped_count(self) -> int:
        return sum(len(topic["dropped"]) for topic in self.log["topics"])


class ContentFilter:
    """Rejects generated text that is unsafe for the dataset. Returns a reason, or None."""

    def __init__(
        self,
        forbidden_subjects: Sequence[str],
        max_length: int,
        existing_texts: Iterable[str],
    ) -> None:
        self._forbidden = [
            (subject, re.compile(rf"\b{re.escape(subject)}", re.IGNORECASE))
            for subject in forbidden_subjects
        ]
        self._max_length = max_length
        self._seen = {normalise_text(text).casefold() for text in existing_texts}

    def rejection_reason(self, text: str, author_id: str, allowed_ids: set[str]) -> str | None:
        normalised = normalise_text(text)
        if author_id not in allowed_ids:
            return f"unknown or unauthorised person {author_id!r}"
        if not normalised:
            return "empty"
        if len(normalised) > self._max_length:
            return "too long"
        for subject, pattern in self._forbidden:
            if pattern.search(normalised):
                return f"mentions forbidden subject {subject!r}"
        if normalised.casefold() in self._seen:
            return "duplicate of existing text"
        return None

    def remember(self, text: str) -> None:
        self._seen.add(normalise_text(text).casefold())


class IdAllocator:
    """Sequential ids per kind (gen_msg_0001, ...) that skip any id already in use."""

    def __init__(self, taken: Iterable[str]) -> None:
        self._taken = set(taken)
        self._counters: dict[str, int] = {}

    def next(self, kind: str) -> str:
        while True:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            candidate = f"gen_{kind}_{self._counters[kind]:04d}"
            if candidate not in self._taken:
                self._taken.add(candidate)
                return candidate


class DatasetAugmenter:
    def __init__(self, llm: LLMProvider, spec: GenerationSpec) -> None:
        self._llm = llm
        self._spec = spec

    def augment(self, base: RawDataset) -> GenerationResult:
        window = base.manifest.time_window
        if window.end - window.start < MIN_WINDOW:
            raise ValueError("the base dataset's time window is too short to place new topics")

        state = _AugmentState.from_base(base, self._spec)
        topic_logs: list[TopicLog] = []
        failures: list[dict[str, str]] = []

        for organisation in base.directory.organisations:
            for project in organisation.projects:
                channel = state.find_channel(organisation.id, project.id)
                if channel is None:
                    failures.append({"project_id": project.id, "error": "no Slack channel"})
                    continue
                people = _people_with_access(organisation, channel["access_group_ids"])
                if len(people) < 2:
                    failures.append({"project_id": project.id, "error": "fewer than 2 people"})
                    continue
                for _ in range(self._spec.topics_per_project):
                    try:
                        topic = self._ask_for_topic(organisation, project, people, state)
                    except LLMError as error:
                        failures.append({"project_id": project.id, "error": str(error)})
                        continue
                    topic_logs.append(
                        state.add_topic(organisation, project, channel, people, topic, window)
                    )

        dataset = state.build_dataset(self._llm.model_name)
        return GenerationResult(
            dataset=dataset,
            log=self._build_log(base, topic_logs, failures),
        )

    def _ask_for_topic(
        self,
        organisation: Organisation,
        project: Project,
        people: Sequence[Person],
        state: "_AugmentState",
    ) -> GeneratedTopic:
        prompt = build_topic_prompt(
            organisation=organisation,
            project=project,
            people=people,
            forbidden_subjects=self._spec.forbidden_subjects,
            used_topics=state.used_topics.get(project.id, []),
            messages_per_topic=self._spec.messages_per_topic,
            utterances_per_meeting=self._spec.utterances_per_meeting,
        )
        return self._llm.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            response_schema=GeneratedTopic,
            temperature=self._spec.temperature,
        )

    def _build_log(
        self, base: RawDataset, topics: list[TopicLog], failures: list[dict[str, str]]
    ) -> dict[str, Any]:
        return {
            "dataset_name": self._spec.dataset_name,
            "base_dataset": base.manifest.name,
            "model": self._llm.model_name,
            "prompt_version": PROMPT_VERSION,
            "spec": asdict(self._spec),
            "generated_at": datetime.now(UTC).isoformat(),
            "topics": [asdict(topic) for topic in topics],
            "failures": failures,
        }


def _people_with_access(organisation: Organisation, group_ids: Sequence[str]) -> list[Person]:
    allowed = set(group_ids)
    return [person for person in organisation.people if allowed & set(person.access_group_ids)]


class _AugmentState:
    """Mutable working copy of the raw exports while topics are being added."""

    def __init__(
        self,
        base: RawDataset,
        spec: GenerationSpec,
        content_filter: ContentFilter,
        ids: IdAllocator,
    ) -> None:
        self.base = base
        self.spec = spec
        self.filter = content_filter
        self.ids = ids
        self.rng = random.Random(spec.seed)
        self.slack = base.slack.model_dump(mode="json")
        self.pages: list[dict[str, Any]] = base.notion.model_dump(mode="json")["pages"]
        self.meetings: list[dict[str, Any]] = base.meetings.model_dump(mode="json")["meetings"]
        self.used_topics: dict[str, list[str]] = {}

    @classmethod
    def from_base(cls, base: RawDataset, spec: GenerationSpec) -> "_AugmentState":
        documents = to_synthetic_dataset(base).documents
        content_filter = ContentFilter(
            spec.forbidden_subjects,
            spec.max_text_length,
            existing_texts=(doc.content for doc in documents),
        )
        ids = IdAllocator(doc.external_id for doc in documents)
        return cls(base, spec, content_filter, ids)

    def find_channel(self, organisation_id: str, project_id: str) -> dict[str, Any] | None:
        for workspace in self.slack["workspaces"]:
            if workspace["organisation_id"] != organisation_id:
                continue
            for channel in workspace["channels"]:
                if channel.get("project_id") == project_id:
                    return channel
        return None

    def add_topic(
        self,
        organisation: Organisation,
        project: Project,
        channel: dict[str, Any],
        people: Sequence[Person],
        topic: GeneratedTopic,
        window: TimeWindow,
    ) -> TopicLog:
        log = TopicLog(organisation.id, project.id, topic.topic, topic.model_dump(mode="json"))
        self.used_topics.setdefault(project.id, []).append(topic.topic)
        allowed = {person.id for person in people}
        start = self._topic_start(window.start, window.end)

        self._add_messages(channel, topic, allowed, start, log)
        self._add_page(organisation, project, channel, topic, allowed, start, log)
        self._add_meeting(organisation, project, channel, topic, allowed, start, log)
        return log

    def build_dataset(self, model_name: str) -> RawDataset:
        base_manifest = self.base.manifest
        manifest = base_manifest.model_copy(
            update={
                "name": self.spec.dataset_name,
                "description": (
                    f"{base_manifest.description} Augmented with LLM-written topics "
                    f"(model {model_name}, prompt {PROMPT_VERSION}, seed {self.spec.seed})."
                ),
            }
        )
        return RawDataset(
            manifest=manifest,
            directory=self.base.directory,
            slack=SlackExport.model_validate(self.slack),
            notion=NotionExport.model_validate({"pages": self.pages}),
            meetings=MeetingExport.model_validate({"meetings": self.meetings}),
            evaluation=self.base.evaluation,
        )

    def _topic_start(self, window_start: datetime, window_end: datetime) -> datetime:
        latest = (window_end - TOPIC_SPAN) - window_start
        offset = self.rng.uniform(0, latest.total_seconds())
        return (window_start + timedelta(seconds=offset)).replace(microsecond=0)

    def _keep(self, text: str, author_id: str, allowed: set[str], kind: str, log: TopicLog) -> bool:
        reason = self.filter.rejection_reason(text, author_id, allowed)
        if reason is not None:
            log.dropped.append(DroppedItem(kind, reason, text))
            return False
        self.filter.remember(text)
        return True

    def _add_messages(
        self,
        channel: dict[str, Any],
        topic: GeneratedTopic,
        allowed: set[str],
        start: datetime,
        log: TopicLog,
    ) -> None:
        moment = start
        for message in topic.messages:
            moment += timedelta(minutes=self.rng.randint(2, 45))
            if not self._keep(message.text, message.author_id, allowed, "message", log):
                continue
            message_id = self.ids.next("msg")
            channel["events"].append(
                {
                    "type": "message",
                    "client_msg_id": message_id,
                    "user": message.author_id,
                    "ts": f"{int(moment.timestamp())}.{self.rng.randint(0, 999_999):06d}",
                    "text": normalise_text(message.text),
                }
            )
            log.kept_ids.append(message_id)

    def _add_page(
        self,
        organisation: Organisation,
        project: Project,
        channel: dict[str, Any],
        topic: GeneratedTopic,
        allowed: set[str],
        start: datetime,
        log: TopicLog,
    ) -> None:
        page = topic.page
        text = f"{page.title}\n{page.content}"
        if not page.title.strip() or not self._keep(text, page.author_id, allowed, "page", log):
            return
        page_id = self.ids.next("doc")
        edited = (start + timedelta(hours=self.rng.randint(3, 20))).isoformat()
        self.pages.append(
            {
                "id": page_id,
                "organisation_id": organisation.id,
                "title": normalise_text(page.title),
                "project_id": project.id,
                "access_group_ids": channel["access_group_ids"],
                "created_by": page.author_id,
                "created_time": edited,
                "last_edited_by": page.author_id,
                "last_edited_time": edited,
                "version": 1,
                "archived": False,
                "content": normalise_text(page.content),
            }
        )
        log.kept_ids.append(page_id)

    def _add_meeting(
        self,
        organisation: Organisation,
        project: Project,
        channel: dict[str, Any],
        topic: GeneratedTopic,
        allowed: set[str],
        start: datetime,
        log: TopicLog,
    ) -> None:
        meeting = topic.meeting
        utterances: list[dict[str, Any]] = []
        offset = 0
        for utterance in meeting.utterances:
            if self._keep(utterance.text, utterance.speaker_id, allowed, "utterance", log):
                utterances.append(
                    {
                        "speaker": utterance.speaker_id,
                        "offset_seconds": offset,
                        "text": normalise_text(utterance.text),
                    }
                )
                offset += self.rng.randint(10, 60)

        speakers = list(dict.fromkeys(u["speaker"] for u in utterances))
        if len(utterances) < 2 or len(speakers) < 2 or not meeting.title.strip():
            log.dropped.append(
                DroppedItem("meeting", "fewer than 2 usable utterances or speakers", meeting.title)
            )
            return
        meeting_id = self.ids.next("mtg")
        self.meetings.append(
            {
                "id": meeting_id,
                "organisation_id": organisation.id,
                "title": normalise_text(meeting.title),
                "project_id": project.id,
                "access_group_ids": channel["access_group_ids"],
                "started_at": (start + timedelta(days=1)).isoformat(),
                "participants": speakers,
                "utterances": utterances,
            }
        )
        log.kept_ids.append(meeting_id)
