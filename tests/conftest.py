import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mongomock
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from preciosdb.config import Settings
from preciosdb.db.session import build_engine, session_factory
from preciosdb.http import JsonResponse
from preciosdb.raw_store import MongoRawStore

ROOT = Path(__file__).resolve().parent.parent
DAY1 = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
DAY2 = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A SQLite file with every migration applied, copied for each test (much faster than migrating)."""
    path = tmp_path_factory.mktemp("template") / "template.db"
    command.upgrade(alembic_config(f"sqlite:///{path}"), "head")
    return path


@pytest.fixture
def database_url(tmp_path: Path, migrated_template: Path) -> str:
    path = tmp_path / "test.db"
    shutil.copy(migrated_template, path)
    return f"sqlite:///{path}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    engine = build_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with session_factory(engine)() as session:
        yield session


@pytest.fixture
def raw_store() -> MongoRawStore:
    store = MongoRawStore(mongomock.MongoClient(tz_aware=True), "preciosdb_test")
    store.ensure_indexes()
    return store


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(_env_file=None, database_url=database_url, mongo_url="", request_delay_seconds=0)


class FakeClient:
    """HttpClient that returns queued payloads and records the URLs it was asked for."""

    def __init__(self, *payloads: Any) -> None:
        self.payloads = list(payloads)
        self.urls: list[str] = []

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> JsonResponse:
        self.urls.append(url)
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return JsonResponse(url=url, status=200, data=payload)
