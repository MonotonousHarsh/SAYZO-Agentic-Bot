"""Ask SAYZO a question as a given person and print the cited answer.

Usage (from the backend folder, after ingesting):
    python scripts/ask.py --as nex_arjun "When is the Project Atlas launch?"
    python scripts/ask.py --as nex_rohan "What is the salary band for backend engineers?"
    python scripts/ask.py --as nex_meera --save "Who owns the API migration for Atlas?"

--save goes through the chat flow the API uses: it stores the exchange as a conversation,
and a task request ("Create tasks for ...") gets a task proposal instead of an answer.
"""

import argparse
import sys

from app import bootstrap
from app.config import get_settings
from app.modules.answering import Answer
from app.modules.identity import AccessDenied, AccessService
from app.modules.identity.ids import organisation_id


def main() -> int:
    args = parse_args()
    settings = get_settings()
    try:
        unit_of_work = bootstrap.unit_of_work_factory(bootstrap.database_engine(settings))
        answers = bootstrap.answer_service(settings, unit_of_work)
        org = organisation_id(args.org) if args.org else None
        access = AccessService(unit_of_work).for_person(args.person, org)
    except (bootstrap.ConfigurationError, AccessDenied) as error:
        print(error, file=sys.stderr)
        return 1

    print(f"Asking as {access.full_name} ({access.role.value})\n")
    if args.save:
        turn = bootstrap.chat_service(settings, unit_of_work).ask(access, args.question)
        reply = turn.reply
        print(f"[{reply.answer_status}] {reply.content}\n")
        for c in reply.citations:
            print(f"  [{c.index}] {c.source_title} (v{c.source_version}): {c.excerpt[:160]}")
        print(f"\nSaved in conversation {turn.conversation_id}")
    else:
        print_answer(answers.answer(access, args.question))
    return 0


def print_answer(answer: Answer) -> None:
    print(f"[{answer.status.value}] {answer.text}\n")
    for c in answer.citations:
        print(
            f"  [{c.index}] {c.title}  ({c.external_id} v{c.source_version}, "
            f"{', '.join(c.authors) or 'unknown'}, {c.occurred_at:%d %b %Y})"
        )
        print(f"      {c.excerpt[:200]}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question")
    parser.add_argument("--as", dest="person", required=True, help="directory id, e.g. nex_arjun")
    parser.add_argument("--org", help="organisation id, needed if the person is in several")
    parser.add_argument("--save", action="store_true", help="store it as a conversation")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
