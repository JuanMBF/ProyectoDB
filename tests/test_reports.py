"""The analytic views and SQL files, run over two days of sample data."""

from datetime import timedelta
from decimal import Decimal

import pytest

from preciosdb import reports
from preciosdb.db.repository import PriceRepository
from preciosdb.raw_store import NullRawStore
from preciosdb.scrapers.base import ScrapedProduct
from tests.conftest import DAY1, DAY2
from tests.test_pipeline import load_both_days

LECHE, MANTECA, ACEITE = "7790742363008", "7793940054006", "7790272001029"


@pytest.fixture
def loaded(session):
    load_both_days(session, NullRawStore())
    return session


def prices(rows):
    return {r["supermarket"]: r["price"] for r in rows}


def test_compare_ranks_the_latest_price_of_each_supermarket(loaded):
    rows = reports.compare(loaded, ean=MANTECA)

    assert [r["supermarket"] for r in rows] == ["coto", "dia", "carrefour"]
    assert [r["price_rank"] for r in rows] == [1, 2, 3]
    coto, dia, carrefour = rows
    assert coto["price"] == Decimal("3486.75")  # 25 % off at Coto
    assert dia["regular_price"] == Decimal("5010.00")
    assert carrefour["price"] == Decimal("4990.00")  # day 2 price, not day 1
    assert carrefour["diff_vs_best"] == Decimal("1503.25")
    assert float(carrefour["pct_vs_best"]) == pytest.approx(43.1)


def test_ties_share_the_first_place(loaded):
    rows = reports.compare(loaded, ean=LECHE)

    assert prices(rows) == {
        "carrefour": Decimal("2915.00"),
        "coto": Decimal("2915.00"),
        "dia": Decimal("2915.00"),
    }
    assert {r["price_rank"] for r in rows} == {1}


def test_out_of_stock_supermarket_drops_out_of_the_comparison(loaded):
    assert set(prices(reports.compare(loaded, ean=ACEITE))) == {"coto", "dia"}


def test_compare_by_name_and_without_filter(loaded):
    by_name = reports.compare(loaded, search="DULCE de leche")
    assert {r["ean"] for r in by_name} == {"7790742625205"}
    assert len(reports.compare(loaded, limit=5)) == 5
    # Products sold by a single chain are not comparisons.
    assert reports.compare(loaded, search="queso") == []


def test_compare_takes_the_search_text_literally(loaded):
    # "%" and "_" are LIKE wildcards: unescaped, "5%" matched every product with a 5 in its name.
    assert {r["product"] for r in reports.compare(loaded, search="3%")} == {
        r["product"] for r in reports.compare(loaded, ean=LECHE)
    }
    assert reports.compare(loaded, search="5%") == []
    assert reports.compare(loaded, search="%") == reports.compare(loaded, search="3%")
    assert reports.compare(loaded, search="_") == []
    assert reports.compare(loaded, search="!") == []


def test_history_lists_every_capture(loaded):
    rows = reports.history(loaded, LECHE)

    assert len(rows) == 6
    assert rows[0]["captured_at"] == DAY1
    assert rows[-1]["captured_at"] == DAY2
    carrefour = [r["price"] for r in rows if r["supermarket"] == "carrefour"]
    assert carrefour == [Decimal("1939.00"), Decimal("2915.00")]


def test_history_also_finds_store_only_codes(loaded):
    rows = reports.history(loaded, "2000199000001")
    assert [r["price"] for r in rows] == [Decimal("10990.00"), Decimal("11490.00")]


UPC_A = "036000291452"  # the same barcode as EAN-13 "0036000291452"


@pytest.fixture
def upc_in_two_chains(session):
    """One chain publishes the 12-digit UPC-A and another the EAN-13 with the leading zero."""
    repo = PriceRepository(session)
    for code, ean, price in (("dia", UPC_A, "100"), ("carrefour", "0" + UPC_A, "120")):
        run = repo.start_run(repo.supermarket(code, code, f"https://{code}.example"), ["papel"], DAY1)
        product = ScrapedProduct(sku=f"{code}-1", name="Papel higiénico", ean=ean, price=Decimal(price))
        repo.save_page(run, [product], DAY1)
    session.commit()
    return session


@pytest.mark.parametrize("ean", [UPC_A, "0" + UPC_A, f" {UPC_A} "])
def test_compare_and_history_accept_either_form_of_the_barcode(upc_in_two_chains, ean):
    # products.ean keeps the 13-digit form, so a UPC-A typed as printed on the package found nothing.
    assert prices(reports.compare(upc_in_two_chains, ean=ean)) == {
        "dia": Decimal("100.00"),
        "carrefour": Decimal("120.00"),
    }
    assert prices(reports.history(upc_in_two_chains, ean)) == {
        "dia": Decimal("100.00"),
        "carrefour": Decimal("120.00"),
    }


def test_changes_since(loaded):
    rows = reports.changes(loaded, since=DAY2 - timedelta(hours=1))

    biggest = rows[0]
    assert (biggest["supermarket"], biggest["previous_price"], biggest["price"]) == (
        "carrefour",
        Decimal("1939.00"),
        Decimal("2915.00"),
    )
    assert float(biggest["change_pct"]) == pytest.approx(50.3)
    # The out-of-stock day is not a "price change" to zero.
    assert all(r["price"] is not None for r in rows)
    assert reports.changes(loaded, since=DAY2 + timedelta(days=1)) == []


SQL_FILES = sorted(reports.SQL_DIR.glob("*.sql"))


def test_there_are_sql_files():
    assert len(SQL_FILES) == 5


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.stem)
def test_every_sql_file_runs(loaded, path):
    assert reports.run_sql_file(loaded, path)


def test_comparable_basket(loaded):
    rows = reports.run_sql_file(loaded, reports.SQL_DIR / "01_canasta_comparable.sql")

    # Aceite is out of stock at Carrefour, so the basket common to the 3 chains has 5 products.
    assert {r["supermarket"]: r["productos"] for r in rows} == {"dia": 5, "coto": 5, "carrefour": 5}
    assert rows[0]["supermarket"] == "dia"
    assert rows[0]["total_canasta"] == Decimal("19755.00")


def test_price_variation_per_supermarket(loaded):
    rows = reports.run_sql_file(loaded, reports.SQL_DIR / "05_variacion_por_supermercado.sql")

    carrefour = next(r for r in rows if r["supermarket"] == "carrefour")
    assert carrefour["subieron"] == 4
    assert carrefour["bajaron"] == 0


def test_recent_runs(loaded):
    runs = reports.recent_runs(loaded, limit=2)
    assert [r.id for r in runs] == [6, 5]
