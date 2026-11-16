"""The JSON the LLM must return for one topic.

Every field is required and no extra fields are allowed, which is what strict structured
output mode needs. Lengths and allowed people are checked afterwards, in code.
"""

from app.modules.sources.common import StrictModel


class GeneratedMessage(StrictModel):
    author_id: str
    text: str


class GeneratedPage(StrictModel):
    author_id: str
    title: str
    content: str


class GeneratedUtterance(StrictModel):
    speaker_id: str
    text: str


class GeneratedMeeting(StrictModel):
    title: str
    utterances: list[GeneratedUtterance]


class GeneratedTopic(StrictModel):
    topic: str
    messages: list[GeneratedMessage]
    page: GeneratedPage
    meeting: GeneratedMeeting
