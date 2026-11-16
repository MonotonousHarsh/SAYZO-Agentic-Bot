"""Deterministic UUIDs for records that come from an external key (e.g. 'nex_rohan').

Re-running a seed or an import produces the same ids, which makes those runs idempotent:
an upsert keyed on the id updates the existing row instead of creating a new one.
"""

from uuid import UUID, uuid5

SAYZO_NAMESPACE = UUID("6f1b6c1e-4d0a-4f6e-9a3b-5a2c7e9d1b40")


def stable_id(kind: str, *key_parts: str) -> UUID:
    return uuid5(SAYZO_NAMESPACE, ":".join((kind, *key_parts)))
