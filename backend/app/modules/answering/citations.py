"""Check the model's citations against the sources it was actually given.

A reply is rejected if it cites a number that was not provided, answers without citing, or
reports a conflict without citing at least two sources. Accepted replies are renumbered so
citations read [1], [2], ... in order of first use.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.modules.answering.prompt import DraftAnswer
from app.shared.text import clean_text

_GROUP = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
# "Source 3 says..." breaks once citations are renumbered; only [n] markers are allowed.
_NUMBERED_MENTION = re.compile(r"\bsources?\s*#?\d", re.IGNORECASE)


@dataclass(frozen=True)
class CheckedAnswer:
    status: str
    text: str
    used: tuple[int, ...]  # original source numbers, in the order of the new numbering


def cited_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    for group in _GROUP.findall(text):
        for value in group.split(","):
            number = int(value)
            if number not in numbers:
                numbers.append(number)
    return numbers


def problems(draft: DraftAnswer, source_count: int) -> list[str]:
    found = []
    text = draft.answer.strip()
    numbers = cited_numbers(text)
    if not text:
        found.append("the answer is empty")
    invalid = [n for n in numbers if not 1 <= n <= source_count]
    if invalid:
        found.append(
            f"cited source numbers {invalid} do not exist; only 1-{source_count} were given"
        )
    if draft.status == "ANSWERED" and not numbers:
        found.append("an ANSWERED reply must cite at least one source as [n]")
    if draft.status == "CONFLICTING_EVIDENCE" and len(set(numbers)) < 2:
        found.append("a CONFLICTING_EVIDENCE reply must cite each disagreeing source")
    if _NUMBERED_MENTION.search(text):
        found.append(
            "do not write 'Source N' in the text; name the source by its title or author "
            "and cite it only with [n] markers"
        )
    return found


def renumber(draft: DraftAnswer) -> CheckedAnswer:
    used = cited_numbers(draft.answer)
    mapping = {old: new for new, old in enumerate(used, start=1)}

    def replace(match: re.Match[str]) -> str:
        values = dict.fromkeys(mapping[int(v)] for v in match.group(1).split(","))
        return "".join(f"[{v}]" for v in values)

    return CheckedAnswer(draft.status, _GROUP.sub(replace, draft.answer.strip()), tuple(used))


def check(draft: DraftAnswer, source_count: int) -> tuple[CheckedAnswer | None, Sequence[str]]:
    draft = DraftAnswer(status=draft.status, answer=clean_text(draft.answer))
    found = problems(draft, source_count)
    return (None, found) if found else (renumber(draft), ())
