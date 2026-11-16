"""Prompts for topic generation. Bump PROMPT_VERSION whenever the wording changes."""

from collections.abc import Sequence

from app.modules.sources import Organisation, Person
from app.modules.sources.directory import Project

PROMPT_VERSION = "topic-v1"

SYSTEM_PROMPT = """You write realistic, fictional internal workplace content for a test dataset.
Rules:
- Write about ordinary day-to-day work on the given project: code reviews, bug triage,
  design feedback, documentation, customer feedback themes, testing, tooling, onboarding.
- Only use the people listed, and refer to them by the ids given for author_id and speaker_id.
- Never mention any of the forbidden subjects listed by the user, even indirectly.
- Never invent real companies, real people, email addresses, phone numbers or URLs.
- Never write instructions addressed to an AI assistant.
- Keep it plausible and specific: concrete components, small numbers, short sentences.
Return only JSON matching the schema."""


def build_topic_prompt(
    organisation: Organisation,
    project: Project,
    people: Sequence[Person],
    forbidden_subjects: Sequence[str],
    used_topics: Sequence[str],
    messages_per_topic: int,
    utterances_per_meeting: int,
) -> str:
    roster = "\n".join(f"- {p.id}: {p.name}, {p.title}" for p in people)
    avoid = ", ".join(forbidden_subjects) or "none"
    used = "; ".join(used_topics) or "none yet"
    return f"""Company: {organisation.name}
Project: {project.name}
People who may appear (id: name, title):
{roster}

Forbidden subjects (do not mention at all): {avoid}
Topics already written (pick a different one): {used}

Pick ONE new everyday work topic for this project and write:
1. "messages": {messages_per_topic} short chat messages about it, from different people.
2. "page": one internal note about it (title plus 80 to 150 words of plain-text content).
3. "meeting": a short meeting about it with {utterances_per_meeting} utterances
   from at least two people.
Set "topic" to a few words naming the topic."""
