"""Propose, create and manage tasks from the command line, as a given person.

Usage (from the backend folder):
    python scripts/tasks.py propose --as nex_meera "Create tasks for the Atlas sync action items"
    python scripts/tasks.py propose --as nex_meera --confirm "Create tasks for ..."  # create them
    python scripts/tasks.py propose --as nex_dev "Create a task for Rohan to fix billing"  # denied
    python scripts/tasks.py list --as nex_rohan [--mine]
    python scripts/tasks.py status --as nex_rohan <task-id> COMPLETED
    python scripts/tasks.py history --as nex_meera <task-id>

--confirm derives one idempotency key per task from its title, assignee and due date, so
confirming again does not create duplicates: the second run reports the existing tasks.
"""

import argparse
import hashlib
import sys
from datetime import date
from uuid import UUID

from app import bootstrap
from app.config import get_settings
from app.modules.identity import AccessDenied, AccessService
from app.modules.identity.ids import organisation_id
from app.modules.tasks import (
    ActionDenied,
    IdempotencyConflict,
    InvalidTask,
    Task,
    TaskDraft,
    TaskNotFound,
    TaskStatus,
)
from app.shared.text import normalise_name


def main() -> int:
    args = parse_args()
    settings = get_settings()
    try:
        unit_of_work = bootstrap.unit_of_work_factory(bootstrap.database_engine(settings))
        org = organisation_id(args.org) if args.org else None
        access = AccessService(unit_of_work).for_person(args.person, org)
    except (bootstrap.ConfigurationError, AccessDenied) as error:
        print(error, file=sys.stderr)
        return 1
    tasks = bootstrap.task_service(settings, unit_of_work)

    try:
        if args.command == "propose":
            proposals = bootstrap.task_proposal_service(settings, unit_of_work)
            proposal = proposals.propose(access, args.request)
            if proposal.is_empty:
                print(proposal.note)
                return 0
            print(f"Proposed by SAYZO for {access.full_name}:\n")
            for n, draft in enumerate(proposal.drafts, start=1):
                due = draft.due_date.isoformat() if draft.due_date else "no due date"
                print(
                    f"{n}. {draft.title}  [{draft.assignee_name or 'unassigned'}, {due}, "
                    f"{draft.priority.value}]"
                )
                if draft.evidence_title:
                    print(f"   from: {draft.evidence_title}")
                for warning in draft.warnings:
                    print(f"   ! {warning}")
            if not args.confirm:
                print("\nNothing was created. Re-run with --confirm to create these tasks.")
                return 0
            print()
            existing = {
                task_signature(t.title, t.assigned_to, t.due_date): t
                for t in tasks.list_tasks(access)
            }
            for draft in proposal.drafts:
                match = existing.get(
                    task_signature(draft.title, draft.assignee_member_id, draft.due_date)
                )
                if match is not None:
                    print(f"Already exists: {match.title} ({match.id})")
                    continue
                key = confirmation_key(access.member_id, draft)
                try:
                    result = tasks.create(access, draft, idempotency_key=key)
                except IdempotencyConflict:
                    # Same task (title, assignee, due date) confirmed before, but the model
                    # worded some detail differently this time: keep the original.
                    print(f"Already created earlier (kept the original): {draft.title}")
                    continue
                verb = "Already created" if result.replayed else "Created"
                print(f"{verb}: {result.task.title} ({result.task.id})")
        elif args.command == "list":
            for task in tasks.list_tasks(access, assigned_to_me=args.mine):
                print_task(task)
        elif args.command == "status":
            print_task(tasks.update_status(access, UUID(args.task_id), TaskStatus(args.status)))
        elif args.command == "history":
            for event in tasks.history(access, UUID(args.task_id)):
                print(
                    f"{event.created_at:%d %b %H:%M}  {event.event_type:<15} "
                    f"by {event.actor_name}: {event.old_value} -> {event.new_value}"
                )
    except ActionDenied as error:
        print(f"Denied: {error}", file=sys.stderr)
        return 2
    except (InvalidTask, IdempotencyConflict, TaskNotFound, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


def task_signature(title: str, assignee: UUID | None, due: date | None) -> str:
    """A task as the user sees it: what, who, when."""
    return "|".join([normalise_name(title), str(assignee), str(due)])


def confirmation_key(member: UUID, draft: TaskDraft) -> str:
    """One key per task signature, so confirming the same proposal again cannot create
    duplicates even when the model rewords the description."""
    signature = task_signature(draft.title, draft.assignee_member_id, draft.due_date)
    return "cli-" + hashlib.sha256(f"{member}|{signature}".encode()).hexdigest()[:32]


def print_task(task: Task) -> None:
    due = task.due_date.isoformat() if task.due_date else "-"
    print(
        f"{task.id}  {task.status.value:<11} {task.priority.value:<6} due {due:<10} "
        f"{task.assigned_to_name or 'unassigned':<14} {task.title}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("propose", "list", "status", "history"):
        command = sub.add_parser(name)
        command.add_argument("--as", dest="person", required=True)
        command.add_argument("--org")
        if name == "propose":
            command.add_argument("request")
            command.add_argument("--confirm", action="store_true")
        elif name == "list":
            command.add_argument("--mine", action="store_true")
        else:
            command.add_argument("task_id")
            if name == "status":
                command.add_argument("status", choices=[s.value for s in TaskStatus])
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
