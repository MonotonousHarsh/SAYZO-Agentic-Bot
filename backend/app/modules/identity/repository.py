"""Writes directory records. Every method is an idempotent upsert keyed on the stable id.

The repository never commits: the caller's unit of work decides when the transaction ends.
"""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import Connection, text


class DirectoryRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def upsert_organisation(self, id_: UUID, external_key: str, name: str, slug: str) -> None:
        self._connection.execute(
            text(
                """
                INSERT INTO organisations (id, external_key, name, slug)
                VALUES (:id, :external_key, :name, :slug)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, slug = EXCLUDED.slug
                """
            ),
            {"id": id_, "external_key": external_key, "name": name, "slug": slug},
        )

    def upsert_profile(self, id_: UUID, external_key: str, full_name: str, email: str) -> None:
        self._connection.execute(
            text(
                """
                INSERT INTO profiles (id, external_key, full_name, email)
                VALUES (:id, :external_key, :full_name, :email)
                ON CONFLICT (id) DO UPDATE
                    SET full_name = EXCLUDED.full_name, email = EXCLUDED.email
                """
            ),
            {"id": id_, "external_key": external_key, "full_name": full_name, "email": email},
        )

    def upsert_member(
        self, id_: UUID, organisation_id: UUID, profile_id: UUID, role: str, title: str
    ) -> None:
        # Status is deliberately not overwritten: suspending someone survives a re-seed.
        self._connection.execute(
            text(
                """
                INSERT INTO organisation_members (id, organisation_id, profile_id, role, title)
                VALUES (:id, :organisation_id, :profile_id, :role, :title)
                ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role, title = EXCLUDED.title
                """
            ),
            {
                "id": id_,
                "organisation_id": organisation_id,
                "profile_id": profile_id,
                "role": role,
                "title": title,
            },
        )

    def upsert_access_group(
        self, id_: UUID, organisation_id: UUID, external_key: str, name: str
    ) -> None:
        self._connection.execute(
            text(
                """
                INSERT INTO access_groups (id, organisation_id, external_key, name)
                VALUES (:id, :organisation_id, :external_key, :name)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                """
            ),
            {
                "id": id_,
                "organisation_id": organisation_id,
                "external_key": external_key,
                "name": name,
            },
        )

    def upsert_project(
        self, id_: UUID, organisation_id: UUID, external_key: str, name: str
    ) -> None:
        self._connection.execute(
            text(
                """
                INSERT INTO projects (id, organisation_id, external_key, name)
                VALUES (:id, :organisation_id, :external_key, :name)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                """
            ),
            {
                "id": id_,
                "organisation_id": organisation_id,
                "external_key": external_key,
                "name": name,
            },
        )

    def replace_member_groups(
        self, organisation_id: UUID, member_id: UUID, group_ids: Collection[UUID]
    ) -> None:
        """Make the member's group memberships exactly `group_ids`."""
        self._connection.execute(
            text(
                """
                DELETE FROM access_group_members
                WHERE member_id = :member_id AND NOT (access_group_id = ANY(:group_ids))
                """
            ),
            {"member_id": member_id, "group_ids": list(group_ids)},
        )
        if not group_ids:
            return
        self._connection.execute(
            text(
                """
                INSERT INTO access_group_members (access_group_id, member_id, organisation_id)
                VALUES (:group_id, :member_id, :organisation_id)
                ON CONFLICT (access_group_id, member_id) DO NOTHING
                """
            ),
            [
                {"group_id": g, "member_id": member_id, "organisation_id": organisation_id}
                for g in group_ids
            ],
        )
