"""Database ids for directory records, derived from their external keys in the dataset.

The same dataset always maps to the same ids, so seeding can be re-run safely.
"""

from uuid import UUID

from app.shared.database import stable_id


def organisation_id(organisation_key: str) -> UUID:
    return stable_id("organisation", organisation_key)


def profile_id(person_key: str) -> UUID:
    return stable_id("profile", person_key)


def member_id(organisation_key: str, person_key: str) -> UUID:
    return stable_id("member", organisation_key, person_key)


def access_group_id(group_key: str) -> UUID:
    return stable_id("access_group", group_key)


def project_id(project_key: str) -> UUID:
    return stable_id("project", project_key)
