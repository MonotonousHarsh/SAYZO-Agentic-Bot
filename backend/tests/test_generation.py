from pathlib import Path

import pytest

from app.modules.synthetic_data import DatasetValidator
from app.modules.synthetic_data.generation import DatasetAugmenter, GenerationSpec
from app.modules.synthetic_data.generation.generator import ContentFilter, IdAllocator
from app.modules.synthetic_data.generation.schemas import (
    GeneratedMeeting,
    GeneratedMessage,
    GeneratedPage,
    GeneratedTopic,
    GeneratedUtterance,
)
from app.modules.synthetic_data.loader import (
    RawDataset,
    load_dataset,
    load_raw_dataset,
    to_synthetic_dataset,
    write_raw_dataset,
)
from app.shared.llm import LLMError
from tests.conftest import SEED_DATASET
from tests.fakes import ScriptedLLM

# Projects in the seed directory, in order, with people who can see their channel.
PROJECT_PEOPLE = {
    "nex_proj_atlas": ("nex_rohan", "nex_priya"),
    "nex_proj_beacon": ("nex_arjun", "nex_rohan"),
    "vtx_proj_atlas": ("vtx_sana", "vtx_vikram"),
}


@pytest.fixture(scope="module")
def seed_raw() -> RawDataset:
    return load_raw_dataset(SEED_DATASET)


def make_topic(project_id: str, number: int) -> GeneratedTopic:
    first, second = PROJECT_PEOPLE[project_id]
    tag = f"{project_id} topic {number}"
    return GeneratedTopic(
        topic=tag,
        messages=[
            GeneratedMessage(author_id=first, text=f"Reviewed the new filter panel for {tag}."),
            GeneratedMessage(author_id=second, text=f"Two flaky UI tests found in {tag}."),
        ],
        page=GeneratedPage(
            author_id=first, title=f"Notes: {tag}", content=f"Checklist for {tag}: run tests."
        ),
        meeting=GeneratedMeeting(
            title=f"Review of {tag}",
            utterances=[
                GeneratedUtterance(speaker_id=first, text=f"Let's walk through {tag}."),
                GeneratedUtterance(speaker_id=second, text=f"I fixed both tests for {tag}."),
            ],
        ),
    )


def scripted_topics(topics_per_project: int) -> list[GeneratedTopic]:
    return [
        make_topic(project_id, n)
        for project_id in PROJECT_PEOPLE
        for n in range(topics_per_project)
    ]


def test_augmented_dataset_is_valid_and_contains_new_records(seed_raw: RawDataset) -> None:
    llm = ScriptedLLM(scripted_topics(2))
    spec = GenerationSpec(dataset_name="test_generated", topics_per_project=2)

    result = DatasetAugmenter(llm, spec).augment(seed_raw)
    dataset = to_synthetic_dataset(result.dataset)

    report = DatasetValidator().validate(dataset)
    assert report.is_valid, "\n".join(str(issue) for issue in report.issues)
    generated = [d for d in dataset.documents if d.external_id.startswith("gen_")]
    assert len(generated) == 6 * 4  # 6 topics x (2 messages + 1 page + 1 meeting)
    assert result.kept_count == 24 and result.dropped_count == 0
    assert result.dataset.manifest.name == "test_generated"
    assert result.dataset.evaluation == seed_raw.evaluation


def test_generated_records_inherit_channel_access_and_stay_in_window(
    seed_raw: RawDataset,
) -> None:
    result = DatasetAugmenter(
        ScriptedLLM(scripted_topics(1)), GenerationSpec(dataset_name="t", topics_per_project=1)
    ).augment(seed_raw)
    dataset = to_synthetic_dataset(result.dataset)
    window = dataset.manifest.time_window

    for doc in (d for d in dataset.documents if d.external_id.startswith("gen_")):
        assert window.contains(doc.occurred_at)
        if doc.project_id == "nex_proj_atlas":
            assert set(doc.access_group_ids) == {"nex_g_engineering", "nex_g_product"}


def test_same_seed_and_replies_give_identical_datasets(seed_raw: RawDataset) -> None:
    spec = GenerationSpec(dataset_name="t", topics_per_project=1, seed=7)

    first = DatasetAugmenter(ScriptedLLM(scripted_topics(1)), spec).augment(seed_raw)
    second = DatasetAugmenter(ScriptedLLM(scripted_topics(1)), spec).augment(seed_raw)

    assert first.dataset == second.dataset


def test_unsafe_generated_items_are_dropped_with_reasons(seed_raw: RawDataset) -> None:
    topic = make_topic("nex_proj_atlas", 0).model_copy(
        update={
            "messages": [
                GeneratedMessage(author_id="nex_rohan", text="The launch date moved again."),
                GeneratedMessage(author_id="nex_kavya", text="HR cannot see this channel."),
                GeneratedMessage(author_id="vtx_sana", text="Wrong company entirely."),
                GeneratedMessage(author_id="nex_priya", text="Fixed the date picker bug."),
            ]
        }
    )
    llm = ScriptedLLM([topic, LLMError("rate limited"), LLMError("rate limited")])
    spec = GenerationSpec(dataset_name="t", topics_per_project=1)

    result = DatasetAugmenter(llm, spec).augment(seed_raw)

    reasons = [d["reason"] for d in result.log["topics"][0]["dropped"]]
    assert reasons[0] == "mentions forbidden subject 'launch'"
    assert "unauthorised person 'nex_kavya'" in reasons[1]
    assert "unauthorised person 'vtx_sana'" in reasons[2]
    assert len(result.log["failures"]) == 2
    assert DatasetValidator().validate(to_synthetic_dataset(result.dataset)).is_valid


def test_written_dataset_loads_back(seed_raw: RawDataset, tmp_path: Path) -> None:
    result = DatasetAugmenter(
        ScriptedLLM(scripted_topics(1)), GenerationSpec(dataset_name="t", topics_per_project=1)
    ).augment(seed_raw)

    write_raw_dataset(result.dataset, tmp_path)

    assert load_dataset(tmp_path) == to_synthetic_dataset(result.dataset)


def test_content_filter_rules() -> None:
    content_filter = ContentFilter(["budget"], max_length=50, existing_texts=["Already here."])
    allowed = {"a"}

    assert content_filter.rejection_reason("Fine text.", "a", allowed) is None
    assert content_filter.rejection_reason("Budgeting talk", "a", allowed) is not None
    assert content_filter.rejection_reason("already  here.", "a", allowed) is not None
    assert content_filter.rejection_reason("x" * 51, "a", allowed) == "too long"
    assert content_filter.rejection_reason("Fine text.", "b", allowed) is not None


def test_id_allocator_skips_taken_ids() -> None:
    ids = IdAllocator(["gen_msg_0001"])

    assert [ids.next("msg"), ids.next("msg"), ids.next("doc")] == [
        "gen_msg_0002",
        "gen_msg_0003",
        "gen_doc_0001",
    ]
