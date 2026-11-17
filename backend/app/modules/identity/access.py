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
from app.shared.text import normalise_name


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

    def profile_for_auth_user(self, auth_user_id: UUID, email: str) -> UUID | None:
        """The profile signed in as this Supabase user. On first sign-in the profile with the
        same email is linked to the auth user; a profile already linked to a different auth
        user is never taken over."""
        linked = self._db.execute(
            text("SELECT id FROM profiles WHERE auth_user_id = :uid"), {"uid": auth_user_id}
        ).scalar_one_or_none()
        if linked is not None:
            return linked
        return self._db.execute(
            text(
                "UPDATE profiles SET auth_user_id = :uid "
                "WHERE lower(email) = lower(:email) AND auth_user_id IS NULL RETURNING id"
            ),
            {"uid": auth_user_id, "email": email},
        ).scalar_one_or_none()

    def memberships_of_profile(self, profile_id: UUID) -> list[tuple[UUID, UUID, str]]:
        """(organisation id, member id, organisation name) for the profile's memberships."""
        rows = self._db.execute(
            text(
                """
                SELECT m.organisation_id, m.id, o.name FROM organisation_members m
                JOIN organisations o ON o.id = m.organisation_id
                WHERE m.profile_id = :profile AND m.status = 'ACTIVE'
                ORDER BY o.name
                """
            ),
            {"profile": profile_id},
        ).all()
        return [(row.organisation_id, row.id, row.name) for row in rows]

    def group_names(self, organisation_id: UUID, group_ids: frozenset[UUID]) -> list[str]:
        return list(
            self._db.execute(
                text(
                    "SELECT name FROM access_groups WHERE organisation_id = :org "
                    "AND id = ANY(CAST(:ids AS uuid[])) ORDER BY name"
                ),
                {"org": organisation_id, "ids": [str(g) for g in group_ids]},
            ).scalars()
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


@dataclass(frozen=True)
class MemberRef:
    member_id: UUID
    full_name: str


class MemberDirectory:
    """Active members of an organisation, and name resolution against them."""

    def __init__(self, connection: Connection) -> None:
        self._db = connection

    def active_members(self, organisation_id: UUID) -> list[MemberRef]:
        rows = self._db.execute(
            text(
                """
                SELECT m.id, p.full_name FROM organisation_members m
                JOIN profiles p ON p.id = m.profile_id
                WHERE m.organisation_id = :org AND m.status = 'ACTIVE'
                ORDER BY p.full_name, m.id
                """
            ),
            {"org": organisation_id},
        ).all()
        return [MemberRef(row.id, row.full_name) for row in rows]

    def is_active_member(self, organisation_id: UUID, member_id: UUID) -> bool:
        return any(m.member_id == member_id for m in self.active_members(organisation_id))


def resolve_member(name: str, members: list[MemberRef]) -> MemberRef | None:
    """Full name, or a first name that only one member has. Anything else is unresolved."""
    wanted = normalise_name(name)
    if not wanted:
        return None
    exact = [m for m in members if normalise_name(m.full_name) == wanted]
    if len(exact) == 1:
        return exact[0]
    by_first = [m for m in members if normalise_name(m.full_name).split(" ")[0] == wanted]
    return by_first[0] if len(by_first) == 1 else None


class AccessService:
    def __init__(self, unit_of_work: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    def for_member(self, organisation_id: UUID, member_id: UUID) -> AccessContext:
        with self._unit_of_work() as uow:
            context = AccessRepository(uow.connection).load(organisation_id, member_id)
        if context is None:
            raise AccessDenied("not an active member of this organisation")
        return context

    def for_auth_user(
        self, auth_user_id: UUID, email: str, organisation_id: UUID | None = None
    ) -> AccessContext:
        """Resolve a signed-in Supabase user to a member of one organisation."""
        with self._unit_of_work() as uow:
            repository = AccessRepository(uow.connection)
            profile = repository.profile_for_auth_user(auth_user_id, email)
            if profile is None:
                raise AccessDenied("this account is not in the company directory")
            memberships = repository.memberships_of_profile(profile)
            if organisation_id is not None:
                memberships = [m for m in memberships if m[0] == organisation_id]
            if not memberships:
                raise AccessDenied("not an active member of this organisation")
            if len(memberships) > 1:
                raise AccessDenied("member of several organisations; choose one")
            context = repository.load(memberships[0][0], memberships[0][1])
        if context is None:
            raise AccessDenied("not an active member of this organisation")
        return context

    def members(self, access: AccessContext) -> list["MemberRef"]:
        with self._unit_of_work() as uow:
            return MemberDirectory(uow.connection).active_members(access.organisation_id)

    def organisation_name_and_groups(self, access: AccessContext) -> tuple[str, list[str]]:
        with self._unit_of_work() as uow:
            connection = uow.connection
            name = connection.execute(
                text("SELECT name FROM organisations WHERE id = :id"),
                {"id": access.organisation_id},
            ).scalar_one()
            groups = AccessRepository(connection).group_names(
                access.organisation_id, access.access_group_ids
            )
        return name, groups

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
