from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from preciosdb.db import Base
from preciosdb.db.session import build_engine
from tests.conftest import alembic_config


def test_migrations_match_the_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    command.upgrade(alembic_config(url), "head")
    engine = build_engine(url)
    with engine.connect() as connection:
        diff = compare_metadata(
            MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata
        )
    engine.dispose()
    assert diff == []


def test_upgrade_downgrade_upgrade(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    engine = build_engine(url)
    assert inspect(engine).get_table_names() == ["alembic_version"]
    assert inspect(engine).get_view_names() == []
    engine.dispose()
    command.upgrade(config, "head")


def test_views_exist(engine):
    assert sorted(inspect(engine).get_view_names()) == [
        "v_latest_prices",
        "v_price_changes",
        "v_price_comparison",
    ]
