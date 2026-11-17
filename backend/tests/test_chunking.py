import pytest

from app.modules.ingestion import Chunker


def test_short_text_is_one_chunk() -> None:
    (chunk,) = Chunker().split("Atlas launch moves to Monday 23 November.")

    assert chunk.index == 0
    assert chunk.content == "Atlas launch moves to Monday 23 November."
    assert chunk.token_count == 10  # 7 words x 1.3 = 9.1, rounded up


def test_empty_text_has_no_chunks() -> None:
    assert Chunker().split("  \n\n ") == []


def test_headings_stay_with_their_text() -> None:
    chunker = Chunker(target_words=8, max_words=20, overlap_words=0)

    chunks = chunker.split("## Owners\nArjun owns Atlas.\n## Risks\nBilling is the riskiest part.")

    assert [c.content for c in chunks] == [
        "## Owners\nArjun owns Atlas.",
        "## Risks\nBilling is the riskiest part.",
    ]


def test_chunks_split_on_unit_boundaries_with_overlap() -> None:
    lines = [f"Line {n} says something short." for n in range(10)]  # 5 words each
    chunker = Chunker(target_words=20, max_words=30, overlap_words=5)

    chunks = chunker.split("\n".join(lines))

    assert len(chunks) > 1
    for previous, current in zip(chunks, chunks[1:], strict=False):
        # The last line of a chunk is repeated as the first line of the next one.
        assert current.content.splitlines()[0] == previous.content.splitlines()[-1]
    joined = {line for c in chunks for line in c.content.splitlines()}
    assert joined == set(lines)


def test_overlong_unit_is_split_into_windows() -> None:
    chunker = Chunker(target_words=10, max_words=12, overlap_words=2)

    chunks = chunker.split(" ".join(f"w{n}" for n in range(30)))

    assert all(len(c.content.split()) <= 12 for c in chunks)
    assert chunks[0].content.split()[-2:] == chunks[1].content.split()[:2]


def test_invalid_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        Chunker(target_words=10, max_words=5)
