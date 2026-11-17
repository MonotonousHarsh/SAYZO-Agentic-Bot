"""Splits source text into chunks small enough to embed and precise enough to cite.

The splitter respects the text's own structure: it never cuts inside a paragraph, list item
or meeting utterance unless that single unit is longer than the maximum, and it keeps a
heading together with the text that follows it. Consecutive chunks overlap by whole units so
a fact that spans a boundary is still readable in at least one chunk.
"""

import math
from dataclasses import dataclass

# Rough tokens-per-word ratio for English with WordPiece/BPE tokenizers. Only used for sizing.
TOKENS_PER_WORD = 1.3


@dataclass(frozen=True)
class Chunk:
    index: int
    content: str
    token_count: int


class Chunker:
    def __init__(
        self, target_words: int = 180, max_words: int = 260, overlap_words: int = 40
    ) -> None:
        if not 0 <= overlap_words < target_words <= max_words:
            raise ValueError("need 0 <= overlap_words < target_words <= max_words")
        self._target = target_words
        self._max = max_words
        self._overlap = overlap_words

    def split(self, text: str) -> list[Chunk]:
        units = self._units(text)
        if not units:
            return []

        groups: list[list[str]] = []
        current: list[str] = []
        for unit in units:
            if current and _words(current) + _word_count(unit) > self._target:
                groups.append(current)
                current = self._overlap_tail(current)
            current.append(unit)
        groups.append(current)

        return [
            Chunk(index=i, content="\n".join(group), token_count=_estimate_tokens(group))
            for i, group in enumerate(groups)
        ]

    def _units(self, text: str) -> list[str]:
        """Non-empty lines, headings merged into the next line, long lines split into windows."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        merged: list[str] = []
        pending_heading = ""
        for line in lines:
            if line.startswith("#"):
                pending_heading = f"{pending_heading}\n{line}".strip()
                continue
            merged.append(f"{pending_heading}\n{line}" if pending_heading else line)
            pending_heading = ""
        if pending_heading:
            merged.append(pending_heading)

        units: list[str] = []
        for unit in merged:
            words = unit.split()
            if len(words) <= self._max:
                units.append(unit)
                continue
            step = self._max - self._overlap
            for start in range(0, len(words), step):
                units.append(" ".join(words[start : start + self._max]))
                if start + self._max >= len(words):
                    break
        return units

    def _overlap_tail(self, group: list[str]) -> list[str]:
        """The last whole units of a chunk whose total length fits in the overlap budget."""
        tail: list[str] = []
        for unit in reversed(group):
            if _words(tail) + _word_count(unit) > self._overlap:
                break
            tail.insert(0, unit)
        # Repeating the entire previous chunk would only duplicate it.
        return [] if len(tail) == len(group) else tail


def _word_count(unit: str) -> int:
    return len(unit.split())


def _words(units: list[str]) -> int:
    return sum(_word_count(unit) for unit in units)


def _estimate_tokens(units: list[str]) -> int:
    return math.ceil(_words(units) * TOKENS_PER_WORD)
