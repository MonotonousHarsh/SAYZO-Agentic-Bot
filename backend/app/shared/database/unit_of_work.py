"""One database transaction around one use case.

Repositories run statements on the connection; only the unit of work commits. Everything
inside the `with` block is committed together, or rolled back together if anything raises.
"""

from types import TracebackType

from sqlalchemy import Connection, Engine, RootTransaction


class UnitOfWork:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None
        self._transaction: RootTransaction | None = None

    @property
    def connection(self) -> Connection:
        if self._connection is None:
            raise RuntimeError("UnitOfWork used outside a `with` block")
        return self._connection

    def __enter__(self) -> "UnitOfWork":
        self._connection = self._engine.connect()
        self._transaction = self._connection.begin()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self._connection is not None and self._transaction is not None
        try:
            if exc_type is None:
                self._transaction.commit()
            else:
                self._transaction.rollback()
        finally:
            self._connection.close()
            self._connection = None
            self._transaction = None
