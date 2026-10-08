"""Integration tests against real MySQL 8 and a MongoDB replica set.

    docker compose run --rm --build test

They load the same sample data into MySQL and into SQLite and check that every view and every query in
sql/consultas returns the same results on both engines.
"""

import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from bson import ObjectId
from pymongo import MongoClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from preciosdb import pipeline, reports
from preciosdb.db.models import Listing, PriceSnapshot
from preciosdb.db.session import build_engine, session_factory
from preciosdb.raw_store import MongoRawStore, NullRawStore
from preciosdb.samples import DEMO_TERMS
from tests.conftest import DAY1, DAY2, alembic_config
from tests.test_pipeline import load_both_days, scrapers

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("DATABASE_URL", "")
MONGO_URL = os.environ.get("MONGO_URL", "")
MONGO_DATABASE = os.environ.get("MONGO_DATABASE", "preciosdb_test")


@pytest.fixture
def mysql(tmp_path: Path) -> Iterator[Session]:
    if not DATABASE_URL.startswith("mysql"):
        pytest.skip("DATABASE_URL no apunta a MySQL")
    config = alembic_config(DATABASE_URL)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = build_engine(DATABASE_URL)
    with session_factory(engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def mongo() -> Iterator[MongoRawStore]:
    if not MONGO_URL:
        pytest.skip("MONGO_URL no configurado")
    client: MongoClient[dict[str, Any]] = MongoClient(MONGO_URL, tz_aware=True)
    client.drop_database(MONGO_DATABASE)
    store = MongoRawStore(client, MONGO_DATABASE)
    store.ensure_indexes()
    yield store
    client.close()


def normalized(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """MySQL returns DECIMAL where SQLite returns float: compare numbers rounded to cents."""
    return [{k: _number(v) for k, v in row.items()} for row in rows]


def _number(value: Any) -> Any:
    return round(float(value), 2) if isinstance(value, Decimal | float) else value


def test_mongodb_is_a_replica_set(mongo):
    status = mongo.client.admin.command("replSetGetStatus")
    states = sorted(m["stateStr"] for m in status["members"])

    assert status["set"] == "rs0"
    assert states == ["PRIMARY", "SECONDARY", "SECONDARY"]


def test_mysql_and_sqlite_give_the_same_answers(mysql, mongo, session):
    load_both_days(mysql, mongo)
    load_both_days(session, NullRawStore())

    assert normalized(reports.compare(mysql, limit=200)) == normalized(reports.compare(session, limit=200))
    assert normalized(reports.history(mysql, "7790742363008")) == normalized(
        reports.history(session, "7790742363008")
    )
    since = DAY2.replace(hour=0)
    assert normalized(reports.changes(mysql, since)) == normalized(reports.changes(session, since))
    for path in sorted(reports.SQL_DIR.glob("*.sql")):
        mysql_rows = reports.run_sql_file(mysql, path)
        assert mysql_rows, path.name
        assert normalized(mysql_rows) == normalized(reports.run_sql_file(session, path)), path.name


def test_prices_are_exact_decimals_in_mysql(mysql):
    pipeline.scrape(mysql, scrapers()["coto"], NullRawStore(), ["manteca"], clock=lambda: DAY1)

    assert mysql.scalar(select(PriceSnapshot.price)) == Decimal("3486.75")
    column = mysql.execute(
        text(
            "SELECT DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'price_snapshots' AND COLUMN_NAME = 'price'"
        )
    ).one()
    assert tuple(column) == ("decimal", 12, 2)


def test_unique_sku_per_supermarket_is_enforced(mysql):
    pipeline.scrape(mysql, scrapers()["dia"], NullRawStore(), ["yerba"], clock=lambda: DAY1)
    existing = mysql.scalars(select(Listing)).first()

    mysql.add(
        Listing(
            supermarket_id=existing.supermarket_id,
            sku=existing.sku,
            name="duplicado",
            first_seen_at=DAY1,
            last_seen_at=DAY1,
        )
    )
    with pytest.raises(IntegrityError):
        mysql.flush()
    mysql.rollback()


def test_every_price_points_to_its_raw_document(mysql, mongo):
    summary = pipeline.scrape(mysql, scrapers()["carrefour"], mongo, DEMO_TERMS, clock=lambda: DAY1)

    refs = set(mysql.scalars(select(PriceSnapshot.raw_ref)))
    found = mongo.collection.count_documents({"_id": {"$in": [ObjectId(r) for r in refs]}})
    assert found == len(refs) > 0
    assert mongo.collection.count_documents({"run_id": summary.run_id}) == summary.pages


def test_reprocess_from_mongodb_into_mysql(mysql, mongo):
    summary = pipeline.scrape(mysql, scrapers()["coto"], mongo, DEMO_TERMS, clock=lambda: DAY1)
    before = mysql.scalars(select(PriceSnapshot.price).order_by(PriceSnapshot.listing_id)).all()

    again = pipeline.reprocess(mysql, summary.run_id, mongo, scrapers())

    assert again.prices == summary.prices
    assert mysql.scalars(select(PriceSnapshot.price).order_by(PriceSnapshot.listing_id)).all() == before


def test_branch_prices_aggregation(mongo, mysql):
    load_both_days(mysql, mongo)

    rows = mongo.coto_branch_prices("7790742363008")

    assert [(r["branch"], r["price"]) for r in rows][:2] == [("061", 2750), ("109", 2915)]
    assert {r["fetched_at"] for r in rows} == {DAY2}
