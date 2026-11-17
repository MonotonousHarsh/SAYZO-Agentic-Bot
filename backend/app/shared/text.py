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
