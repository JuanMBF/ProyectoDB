"""Engine and session factory."""

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def _unicode_lower(value: Any) -> Any:
    return value.lower() if isinstance(value, str) else value


def build_engine(database_url: str, **kwargs: Any) -> Engine:
    url = make_url(database_url)
    is_sqlite = url.get_backend_name() == "sqlite"
    if is_sqlite and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, pool_pre_ping=True, **kwargs)

    if is_sqlite:
        # Per-connection SQLite settings.
        @event.listens_for(engine, "connect")
        def _configure_connection(dbapi_connection: Any, _record: Any) -> None:
            # SQLite ignores foreign keys unless explicitly enabled per connection.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
            # SQLite's LOWER() only knows ASCII, so searching "azúcar" missed a product named "AZÚCAR".
            dbapi_connection.create_function("lower", 1, _unicode_lower, deterministic=True)

    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
