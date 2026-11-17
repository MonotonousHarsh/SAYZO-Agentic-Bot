"""Propose, create and manage tasks from the command line, as a given person.

Usage (from the backend folder):
    python scripts/tasks.py propose --as nex_meera "Create tasks for the Atlas sync action items"
    python scripts/tasks.py propose --as nex_meera --confirm "Create tasks for ..."  # create them
    python scripts/tasks.py propose --as nex_dev "Create a task for Rohan to fix billing"  # denied
    python scripts/tasks.py list --as nex_rohan [--mine]
    python scripts/tasks.py status --as nex_rohan <task-id> COMPLETED
    python scripts/tasks.py history --as nex_meera <task-id>

--confirm uses idempotency keys derived from the request, so running it twice does not create
duplicates: the second run reports the existing tasks.
"""

import argparse
import hashlib
import sys
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
    TaskNotFound,
    TaskStatus,
)


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
            base = hashlib.sha256(f"{access.member_id}:{args.request}".encode()).hexdigest()[:16]
            print()
            for n, draft in enumerate(proposal.drafts, start=1):
                result = tasks.create(access, draft, idempotency_key=f"cli-{base}-{n}")
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
