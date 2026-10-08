from datetime import UTC, datetime
from decimal import Decimal

import mongomock
import pytest

from preciosdb import cli
from preciosdb.raw_store import MongoRawStore
from preciosdb.samples import SampleClient


@pytest.fixture
def run(settings):
    """Runs the CLI and returns (exit code, output)."""

    def _run(*argv: str) -> tuple[int, str]:
        lines: list[str] = []
        code = cli.main(list(argv), settings=settings, out=lines.append)
        return code, "\n".join(lines)

    return _run


@pytest.fixture
def mongo(monkeypatch):
    store = MongoRawStore(mongomock.MongoClient(tz_aware=True), "cli_test")
    monkeypatch.setattr(cli, "build_raw_store", lambda url, database: store)
    return store


def test_migrate(run):
    assert run("migrate") == (0, "Base de datos actualizada.")


def test_demo_then_reports(run):
    code, out = run("demo")
    assert code == 0
    assert out.count("✔") == 6
    assert "MongoDB no configurado" in out

    code, out = run("compare", "manteca")
    assert code == 0
    assert "3.486,75" in out and "4.990,00" in out

    code, out = run("history", "7790742363008")
    assert "1.939,00" in out

    assert "50,3" in run("changes", "--days", "3")[1]
    assert "carrefour" in run("runs", "--limit", "1")[1]


def test_sql_by_name_and_unknown(run):
    run("demo")
    code, out = run("sql", "02")
    assert code == 0
    assert "veces_mas_barato" in out

    code, out = run("sql", "99_no_existe")
    assert code == 2
    assert "01_canasta_comparable" in out


def test_scrape_needs_terms(run):
    assert run("scrape")[0] == 2


def test_scrape_uses_the_real_client(run, monkeypatch):
    monkeypatch.setattr(cli, "RequestsClient", lambda *a, **k: SampleClient("day1"))
    code, out = run("scrape", "--super", "dia", "carrefour", "--term", "yerba", "--ean", "7790742363008")
    assert code == 0
    assert "dia" in out and "carrefour" in out


def test_scrape_reports_failures(run, monkeypatch):
    class Down:
        def get_json(self, url, params=None):
            raise TimeoutError("timeout")

    monkeypatch.setattr(cli, "RequestsClient", lambda *a, **k: Down())
    code, out = run("scrape", "--super", "coto", "--term", "leche")
    assert code == 1
    assert "TimeoutError: timeout" in out


def test_commands_that_need_mongodb(run):
    assert run("branches", "7790742363008")[0] == 2
    assert run("raw-stats")[0] == 2


def test_mongodb_commands(run, mongo):
    run("demo")
    code, out = run("branches", "7790742363008")
    assert code == 0
    assert "5 sucursales · mín. 2.750,00 · máx. 3.035,00" in out

    code, out = run("raw-stats")
    assert code == 0
    assert "coto" in out

    code, out = run("reprocess", "1")
    assert code == 0
    assert "corrida #1" in out
    assert run("reprocess", "999")[0] == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("2915.50"), "2.915,50"),
        (12.5, "12,5"),
        (3, "3"),
        (None, "-"),
        (True, "sí"),
        (datetime(2026, 10, 8, 9, 30, tzinfo=UTC), "2026-10-08 09:30"),
    ],
)
def test_fmt(value, expected):
    assert cli.fmt(value) == expected


def test_render_table():
    table = cli.render_table([{"name": "leche", "price": Decimal("10.00")}, {"name": "yerba", "price": None}])
    assert table.splitlines() == ["name   price", "-----  -----", "leche  10,00", "yerba      -"]
    assert cli.render_table([]) == "(sin resultados)"
