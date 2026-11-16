"""Seeds the directory (organisations, people, groups, projects) into the database."""

from collections.abc import Callable
from dataclasses import dataclass

from app.modules.identity import ids
from app.modules.identity.repository import DirectoryRepository
from app.modules.sources import Directory
from app.shared.database import UnitOfWork


@dataclass(frozen=True)
class SeedSummary:
    organisations: int
    people: int
    access_groups: int
    projects: int
    group_memberships: int


class DirectorySeeder:
    """Writes a whole directory in one transaction: it is all seeded, or nothing is."""

    def __init__(self, unit_of_work: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work = unit_of_work

    def seed(self, directory: Directory) -> SeedSummary:
        people = groups = projects = memberships = 0
        with self._unit_of_work() as uow:
            repository = DirectoryRepository(uow.connection)
            for organisation in directory.organisations:
                org_id = ids.organisation_id(organisation.id)
                repository.upsert_organisation(
                    org_id, organisation.id, organisation.name, organisation.slug
                )
                for group in organisation.access_groups:
                    repository.upsert_access_group(
                        ids.access_group_id(group.id), org_id, group.id, group.name
                    )
                    groups += 1
                for project in organisation.projects:
                    repository.upsert_project(
                        ids.project_id(project.id), org_id, project.id, project.name
                    )
                    projects += 1
                for person in organisation.people:
                    member = ids.member_id(organisation.id, person.id)
                    profile = ids.profile_id(person.id)
                    repository.upsert_profile(profile, person.id, person.name, person.email)
                    repository.upsert_member(member, org_id, profile, person.role, person.title)
                    group_ids = [ids.access_group_id(g) for g in person.access_group_ids]
                    repository.replace_member_groups(org_id, member, group_ids)
                    people += 1
                    memberships += len(group_ids)

        return SeedSummary(
            organisations=len(directory.organisations),
            people=people,
            access_groups=groups,
            projects=projects,
            group_memberships=memberships,
        )
