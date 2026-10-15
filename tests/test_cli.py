import io
import sys
from datetime import UTC, datetime
from decimal import Decimal

import mongomock
import pytest
from sqlalchemy.exc import OperationalError

from preciosdb import cli
from preciosdb.config import Settings
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


def test_output_does_not_fail_on_a_legacy_windows_code_page(settings, monkeypatch):
    # What Python uses on Windows when the output is redirected to a file or a pipe.
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stdout)

    assert cli.main(["demo"], settings=settings) == 0

    stdout.flush()
    assert "✔ coto" in stdout.buffer.getvalue().decode("utf-8")


@pytest.mark.parametrize("argv", [["demo"], ["compare", "leche"], ["changes"], ["runs"], ["sql", "01"]])
def test_commands_before_migrate_say_what_is_missing(tmp_path, argv):
    # They used to end in a SQLAlchemy traceback ("no such table").
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'empty.db'}", mongo_url="")
    lines: list[str] = []

    assert cli.main(argv, settings=settings, out=lines.append) == 2
    assert "python -m preciosdb migrate" in lines[-1]

    assert cli.main(["migrate"], settings=settings, out=lines.append) == 0
    assert cli.main(argv, settings=settings, out=lines.append) == 0


def test_other_database_errors_are_not_hidden(run, tmp_path):
    query = tmp_path / "rota.sql"
    query.write_text("SELECT * FROM tabla_que_no_existe", encoding="utf-8")

    with pytest.raises(OperationalError):
        run("sql", str(query))


def test_sql_by_name_and_unknown(run):
    run("demo")
    code, out = run("sql", "02")
    assert code == 0
    assert "veces_mas_barato" in out

    code, out = run("sql", "99_no_existe")
    assert code == 2
    assert "01_canasta_comparable" in out


def test_sql_with_a_path_that_is_not_a_query_file(run, tmp_path):
    # An absolute path with a typo used to end in a pathlib traceback (NotImplementedError: "Non-relative
    # patterns are unsupported"), and a folder in a PermissionError / IsADirectoryError.
    for target in (tmp_path / "no_existe.sql", tmp_path):
        code, out = run("sql", str(target))

        assert code == 2
        assert f"No encontré {target}" in out
        assert "01_canasta_comparable" in out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["changes", "--days", "1000000"], "como mucho 36500"),  # was an OverflowError traceback
        (["changes", "--days", "-3"], "mayor que cero"),  # silently showed "(sin resultados)"
        (["compare", "--limit", "-1"], "mayor que cero"),  # no limit in SQLite, LIMIT -1 error in MySQL
        (["runs", "--limit", "0"], "mayor que cero"),
        (["scrape", "--term", "leche", "--max", "0"], "mayor que cero"),  # ran with no requests at all
        (["changes", "--days", "una semana"], "no es un número entero"),
    ],
)
def test_numeric_options_out_of_range_are_rejected(run, capsys, argv, message):
    with pytest.raises(SystemExit) as exit_info:
        run(*argv)

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err


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
