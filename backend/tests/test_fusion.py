import pytest

from app.modules.retrieval.fusion import reciprocal_rank_fusion


def test_items_found_by_several_lists_rank_first() -> None:
    fused = reciprocal_rank_fusion({"a": ["x", "y", "z"], "b": ["z", "w"]}, k=60)

    assert [f.item for f in fused][:1] == ["z"]
    assert fused[0].ranks == {"a": 3, "b": 1}


def test_scores_use_only_positions() -> None:
    (only,) = reciprocal_rank_fusion({"a": ["x"]}, k=10)
    assert only.score == pytest.approx(1 / 11)


def test_ties_are_broken_deterministically() -> None:
    first = reciprocal_rank_fusion({"a": ["p", "q"], "b": ["q", "p"]})
    second = reciprocal_rank_fusion({"b": ["q", "p"], "a": ["p", "q"]})
    assert [f.item for f in first] == [f.item for f in second] == ["p", "q"]


def test_a_list_cannot_vote_twice_for_one_item() -> None:
    fused = reciprocal_rank_fusion({"a": ["x", "x", "y"]}, k=60)
    assert {f.item: f.ranks["a"] for f in fused} == {"x": 1, "y": 3}


def test_weights_and_validation() -> None:
    fused = reciprocal_rank_fusion({"a": ["x"], "b": ["y"]}, weights={"b": 2.0})
    assert fused[0].item == "y"
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"a": ["x"]}, k=0)
    assert reciprocal_rank_fusion({}) == []
