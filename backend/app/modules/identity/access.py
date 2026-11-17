"""Who is asking, and what they may see: the access context every read is checked against.

The context is loaded from the database on each request rather than trusted from the client,
so a removed group membership or a suspended member takes effect immediately.
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Connection, text

from app.modules.sources import Role
from app.shared.database import UnitOfWork


class AccessDenied(Exception):
    """The person is not an active member of the organisation they are acting in."""


@dataclass(frozen=True)
class AccessContext:
    organisation_id: UUID
    member_id: UUID
    full_name: str
    role: Role
    access_group_ids: frozenset[UUID]


class AccessRepository:
    def __init__(self, connection: Connection) -> None:
        self._db = connection

    def load(self, organisation_id: UUID, member_id: UUID) -> AccessContext | None:
        row = self._db.execute(
            text(
                """
                SELECT m.id, m.organisation_id, m.role, p.full_name,
                       COALESCE(array_agg(g.access_group_id)
                                FILTER (WHERE g.access_group_id IS NOT NULL), '{}') AS groups
                FROM organisation_members m
                JOIN profiles p ON p.id = m.profile_id
                LEFT JOIN access_group_members g
                       ON g.member_id = m.id AND g.organisation_id = m.organisation_id
                WHERE m.id = :member AND m.organisation_id = :org AND m.status = 'ACTIVE'
                GROUP BY m.id, m.organisation_id, m.role, p.full_name
                """
            ),
            {"member": member_id, "org": organisation_id},
        ).one_or_none()
        if row is None:
            return None
        return AccessContext(
            organisation_id=row.organisation_id,
            member_id=row.id,
            full_name=row.full_name,
            role=Role(row.role),
            access_group_ids=frozenset(row.groups),
        )

    def memberships(self, person_key: str) -> list[tuple[UUID, UUID]]:
        """(organisation id, member id) for every organisation the person belongs to."""
        rows = self._db.execute(
            text(
                """
                SELECT m.organisation_id, m.id FROM organisation_members m
                JOIN profiles p ON p.id = m.profile_id
                WHERE p.external_key = :person ORDER BY m.organisation_id
                """
            ),
            {"person": person_key},
        ).all()
        return [(row.organisation_id, row.id) for row in rows]


class AccessService:
    def __init__(self, unit_of_work: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    def for_member(self, organisation_id: UUID, member_id: UUID) -> AccessContext:
        with self._unit_of_work() as uow:
            context = AccessRepository(uow.connection).load(organisation_id, member_id)
        if context is None:
            raise AccessDenied("not an active member of this organisation")
        return context

    def for_person(self, person_key: str, organisation_id: UUID | None = None) -> AccessContext:
        """Resolve a directory person (e.g. "nex_arjun"). Used by scripts and evaluation;
        the API will resolve the signed-in user instead."""
        with self._unit_of_work() as uow:
            repository = AccessRepository(uow.connection)
            memberships = repository.memberships(person_key)
            if organisation_id is not None:
                memberships = [m for m in memberships if m[0] == organisation_id]
            if len(memberships) != 1:
                raise AccessDenied(
                    f"{person_key!r} has {len(memberships)} matching memberships; "
                    "pass the organisation"
                )
            context = repository.load(*memberships[0])
        if context is None:
            raise AccessDenied(f"{person_key!r} is not an active member")
        return context
