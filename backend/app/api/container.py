"""The services the API needs, built once per process from settings (or given by tests)."""

from collections.abc import Callable
from dataclasses import dataclass

from app.api.auth import Authenticator, DevAuthenticator, SupabaseAuthenticator
from app.config import Settings
from app.modules.conversations import ChatService
from app.modules.identity import AccessService
from app.modules.tasks import TaskService
from app.shared.database import UnitOfWork


@dataclass(frozen=True)
class Container:
    authenticator: Authenticator
    access: AccessService
    chat: ChatService
    tasks: TaskService
    auth_mode: str
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    dev_people: tuple[dict, ...] = ()  # type: ignore[type-arg]


def build_container(settings: Settings) -> Container:
    from app import bootstrap

    engine = bootstrap.database_engine(settings)
    unit_of_work: Callable[[], UnitOfWork] = bootstrap.unit_of_work_factory(engine)
    return Container(
        authenticator=authenticator(settings),
        access=AccessService(unit_of_work),
        chat=bootstrap.chat_service(settings, unit_of_work),
        tasks=bootstrap.task_service(settings, unit_of_work),
        auth_mode=settings.auth_mode,
        supabase_url=settings.supabase_url,
        supabase_publishable_key=settings.supabase_publishable_key,
        dev_people=_directory_people(engine) if settings.auth_mode == "dev" else (),
    )


def authenticator(settings: Settings) -> Authenticator:
    if settings.auth_mode == "dev":
        return DevAuthenticator()
    if not settings.supabase_url:
        from app.bootstrap import ConfigurationError

        raise ConfigurationError("SUPABASE_URL is not set (or start with --dev-auth)")
    return SupabaseAuthenticator(
        settings.supabase_url,
        settings.supabase_publishable_key,
        settings.supabase_jwt_secret.get_secret_value() if settings.supabase_jwt_secret else None,
    )


def _directory_people(engine) -> tuple[dict, ...]:  # type: ignore[no-untyped-def,type-arg]
    """Who can be picked on the dev sign-in screen."""
    from sqlalchemy import text

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT p.external_key AS key, p.full_name AS name, m.title, m.role,
                       o.name AS organisation
                FROM profiles p
                JOIN organisation_members m ON m.profile_id = p.id AND m.status = 'ACTIVE'
                JOIN organisations o ON o.id = m.organisation_id
                ORDER BY o.name, CASE m.role WHEN 'ADMIN' THEN 0 WHEN 'MEMBER' THEN 1 ELSE 2 END,
                         p.full_name
                """
            )
        ).mappings()
        return tuple(dict(row) for row in rows)
