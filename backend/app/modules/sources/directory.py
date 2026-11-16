"""Company directory: organisations, their people, access groups and projects."""

from collections.abc import Iterator
from enum import StrEnum

from pydantic import Field

from app.modules.sources.common import StrictModel


class Role(StrEnum):
    """What a person may DO in SAYZO. Access groups decide what they may SEE."""

    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    VIEWER = "VIEWER"


class Person(StrictModel):
    id: str
    name: str
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    title: str
    role: Role
    access_group_ids: tuple[str, ...] = Field(min_length=1)


class AccessGroup(StrictModel):
    id: str
    name: str


class Project(StrictModel):
    id: str
    name: str


class Organisation(StrictModel):
    id: str
    name: str
    slug: str
    people: tuple[Person, ...]
    access_groups: tuple[AccessGroup, ...]
    projects: tuple[Project, ...]


class Directory(StrictModel):
    organisations: tuple[Organisation, ...]

    def people(self) -> Iterator[tuple[Organisation, Person]]:
        for organisation in self.organisations:
            for person in organisation.people:
                yield organisation, person

    def people_names(self) -> dict[str, str]:
        return {person.id: person.name for _, person in self.people()}
