"""Text helpers shared by modules that compare names written by people or models."""

import re
import unicodedata

_NON_WORD = re.compile(r"[^\w\s-]")
_SPACES = re.compile(r"\s+")


def normalise_name(name: str) -> str:
    """Case-fold, drop punctuation and collapse spaces: "Rohan's  API!" -> "rohan s api"."""
    text = unicodedata.normalize("NFKC", name).casefold()
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


_INVISIBLE = re.compile("[\u200b-\u200d\u2060\ufeff]")
_INLINE_SPACES = re.compile(r"[ \t]+")


def clean_text(text: str) -> str:
    """Make model output plain: NFKC turns narrow/no-break spaces and non-breaking hyphens
    into ordinary characters, zero-width characters are removed, runs of spaces collapse.
    Line breaks are kept."""
    text = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    lines = (_INLINE_SPACES.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(lines).strip()
