from dataclasses import replace

from app.modules.synthetic_data import DatasetValidator, SyntheticDataset
from app.modules.synthetic_data.index import DatasetIndex


def codes_for(dataset: SyntheticDataset) -> set[str]:
    return DatasetValidator().validate(dataset).codes()


def replace_question(dataset: SyntheticDataset, question_id: str, **changes) -> SyntheticDataset:
    questions = tuple(
        q.model_copy(update=changes) if q.id == question_id else q for q in dataset.questions
    )
    return replace(dataset, questions=questions)


def test_seed_dataset_is_valid(seed_dataset: SyntheticDataset) -> None:
    report = DatasetValidator().validate(seed_dataset)

    assert report.is_valid, "\n".join(str(issue) for issue in report.issues)
    assert not report.warnings


def test_seed_dataset_current_state(seed_dataset: SyntheticDataset) -> None:
    index = DatasetIndex(seed_dataset)

    assert "23 November" in index.current["nex_doc_launch_plan"].content
    assert index.current["nex_doc_launch_plan"].version == 2
    assert not index.is_live("nex_msg_004")
    assert not index.can_view("nex_rohan", "nex_doc_hiring_plan")
    assert index.can_view("nex_meera", "nex_doc_hiring_plan")
    assert not index.can_view("nex_rohan", "vtx_doc_atlas_rollout")


def test_unknown_author_is_reported(seed_dataset: SyntheticDataset) -> None:
    broken = replace(seed_dataset.documents[0], author_ids=("ghost",))
    dataset = replace(seed_dataset, documents=(broken, *seed_dataset.documents[1:]))

    assert "UNKNOWN_PERSON" in codes_for(dataset)


def test_cross_tenant_access_group_is_reported(seed_dataset: SyntheticDataset) -> None:
    broken = replace(seed_dataset.documents[0], access_group_ids=("vtx_g_ops",))
    dataset = replace(seed_dataset, documents=(broken, *seed_dataset.documents[1:]))

    assert "FOREIGN_GROUP" in codes_for(dataset)


def test_undeclared_duplicate_is_reported(seed_dataset: SyntheticDataset) -> None:
    original = next(d for d in seed_dataset.documents if d.external_id == "nex_msg_008")
    copy = replace(original, external_id="nex_msg_999")
    dataset = replace(seed_dataset, documents=(*seed_dataset.documents, copy))

    assert "UNDECLARED_DUPLICATE" in codes_for(dataset)


def test_expected_source_hidden_from_asker_is_reported(seed_dataset: SyntheticDataset) -> None:
    dataset = replace_question(seed_dataset, "q13", asked_by="nex_arjun")

    assert "EXPECTED_SOURCE_NOT_VISIBLE" in codes_for(dataset)


def test_deleted_source_cannot_be_expected(seed_dataset: SyntheticDataset) -> None:
    dataset = replace_question(seed_dataset, "q02", expected_source_ids=("nex_msg_004",))

    assert "EXPECTED_SOURCE_NOT_LIVE" in codes_for(dataset)


def test_visible_live_source_cannot_be_forbidden(seed_dataset: SyntheticDataset) -> None:
    dataset = replace_question(seed_dataset, "q05", asked_by="nex_meera")

    assert "FORBIDDEN_SOURCE_IS_ALLOWED" in codes_for(dataset)


def test_denial_requires_a_viewer(seed_dataset: SyntheticDataset) -> None:
    dataset = replace_question(seed_dataset, "q12", asked_by="nex_rohan")

    assert "DENIAL_NOT_JUSTIFIED" in codes_for(dataset)
