"""Read side: queries over the analytic views (see migrations/versions/*_analytics_views.py)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, desc, select, text
from sqlalchemy.orm import Session

from preciosdb.db.models import ScrapeRun
from preciosdb.db.types import UTCDateTime, money
from preciosdb.scrapers.base import comparable_ean

Row = dict[str, Any]

SQL_DIR = Path(__file__).resolve().parent.parent / "sql" / "consultas"

# Raw SQL returns driver values (SQLite: float/str, MySQL: Decimal/datetime). Columns with these names
# get a type, so amounts are always Decimal with two decimals and dates are UTC datetimes.
_MONEY_COLUMNS = (
    "price",
    "regular_price",
    "previous_price",
    "best_price",
    "diff_vs_best",
    "change_amount",
    # sql/consultas
    "total_canasta",
    "total_sin_descuentos",
    "precio_minimo",
    "precio_maximo",
    "brecha",
    "precio_regular",
    "precio_actual",
)
_TYPES: dict[str, Any] = {name: money() for name in _MONEY_COLUMNS} | {"captured_at": UTCDateTime()}


def _rows(session: Session, sql: str, **params: Any) -> list[Row]:
    statement = text(sql).columns(**{k: v for k, v in _TYPES.items() if k in sql})
    if "since" in params:
        statement = statement.bindparams(bindparam("since", type_=UTCDateTime()))
    return [dict(row._mapping) for row in session.execute(statement, params)]


def _like_literal(value: str) -> str:
    """Escapes the LIKE wildcards, so "3%" finds the products with "3%" and not everything with a 3.

    The escape character is "!" because a backslash literal is written differently in MySQL and SQLite.
    """
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _product_ean(ean: str) -> str:
    """The barcode as products.ean stores it: a 12-digit UPC-A is saved as EAN-13 with a leading zero."""
    return comparable_ean(ean) or ean.strip()


def compare(
    session: Session, search: str | None = None, ean: str | None = None, limit: int = 60
) -> list[Row]:
    """Same product in several supermarkets, cheapest first."""
    sql = """
        SELECT ean, product, supermarket, price, regular_price, promo_text,
               price_rank, diff_vs_best, pct_vs_best
        FROM v_price_comparison
        {where}
        ORDER BY product, ean, price_rank, supermarket
        LIMIT :limit
    """
    if ean:
        return _rows(session, sql.format(where="WHERE ean = :ean"), ean=_product_ean(ean), limit=limit)
    if search:
        where = "WHERE LOWER(product) LIKE :pattern ESCAPE '!'"
        return _rows(
            session, sql.format(where=where), pattern=f"%{_like_literal(search.lower())}%", limit=limit
        )
    return _rows(session, sql.format(where=""), limit=limit)


def history(session: Session, ean: str) -> list[Row]:
    """Every captured price of a barcode, in every supermarket."""
    sql = """
        SELECT s.captured_at, sm.code AS supermarket, l.name AS product,
               s.price, s.regular_price, s.promo_text
        FROM price_snapshots s
        JOIN listings l ON l.id = s.listing_id
        JOIN supermarkets sm ON sm.id = l.supermarket_id
        LEFT JOIN products p ON p.id = l.product_id
        WHERE l.ean = :ean OR p.ean = :product_ean
        ORDER BY s.captured_at, sm.code
    """
    return _rows(session, sql, ean=ean.strip(), product_ean=_product_ean(ean))


def changes(session: Session, since: datetime, limit: int = 30) -> list[Row]:
    """Price changes since a date, biggest first."""
    sql = """
        SELECT captured_at, supermarket, product, previous_price, price, change_amount, change_pct
        FROM v_price_changes
        WHERE captured_at >= :since
        ORDER BY ABS(change_pct) DESC, product
        LIMIT :limit
    """
    return _rows(session, sql, since=since, limit=limit)


def run_sql_file(session: Session, path: Path) -> list[Row]:
    """Runs one of the analytic queries of sql/consultas (or any read-only SELECT)."""
    return _rows(session, path.read_text(encoding="utf-8").strip().rstrip(";"))


def recent_runs(session: Session, limit: int = 15) -> list[ScrapeRun]:
    return list(session.scalars(select(ScrapeRun).order_by(desc(ScrapeRun.id)).limit(limit)))
