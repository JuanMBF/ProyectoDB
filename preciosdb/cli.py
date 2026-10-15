"""Command line interface: python -m preciosdb <command> ..."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from preciosdb import pipeline, reports
from preciosdb.config import Settings
from preciosdb.db.models import RunStatus
from preciosdb.db.session import build_engine, session_factory
from preciosdb.db.types import utcnow
from preciosdb.http import RequestsClient
from preciosdb.raw_store import MongoRawStore, NullRawStore, RawStore, build_raw_store
from preciosdb.samples import DAYS, DEMO_TERMS, SampleClient
from preciosdb.scrapers import SUPERMARKETS, build_scrapers
from preciosdb.scrapers.base import to_money

ROOT = Path(__file__).resolve().parent.parent

Column = tuple[str, str]  # (row key, header)


# --------------------------------------------------------------------------- formatting


def fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, Decimal | float):
        # Argentine format (2.915,50), keeping the decimals the value comes with.
        number = Decimal(repr(value)) if isinstance(value, float) else value
        decimals = max(0, -int(number.as_tuple().exponent))
        return f"{number:,.{decimals}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def render_table(rows: Sequence[dict[str, Any]], columns: Sequence[Column] | None = None) -> str:
    if not rows:
        return "(sin resultados)"
    columns = columns or [(key, key) for key in rows[0]]
    cells = [[fmt(row.get(key)) for key, _ in columns] for row in rows]
    widths = [max(len(header), *(len(r[i]) for r in cells)) for i, (_, header) in enumerate(columns)]
    numeric = [
        all(_is_number(row.get(key)) for row in rows) and any(row.get(key) is not None for row in rows)
        for key, _ in columns
    ]

    def line(values: Sequence[str]) -> str:
        return "  ".join(
            v.rjust(w) if num else v.ljust(w) for v, w, num in zip(values, widths, numeric, strict=True)
        ).rstrip()

    header = line([h for _, h in columns])
    return "\n".join([header, line(["-" * w for w in widths]), *(line(r) for r in cells)])


def _is_number(value: Any) -> bool:
    return value is None or (isinstance(value, int | float | Decimal) and not isinstance(value, bool))


# --------------------------------------------------------------------------- commands


class App:
    def __init__(self, settings: Settings, out: Callable[[str], None] = print) -> None:
        self.settings = settings
        self.out = out
        self.engine = build_engine(settings.database_url)
        self.sessions = session_factory(self.engine)
        self._raw_store: RawStore | None = None

    @property
    def raw_store(self) -> RawStore:
        if self._raw_store is None:
            self._raw_store = build_raw_store(self.settings.mongo_url, self.settings.mongo_database)
        return self._raw_store

    def session(self) -> Session:
        return self.sessions()

    def is_migrated(self) -> bool:
        return inspect(self.engine).has_table("alembic_version")

    def close(self) -> None:
        self.engine.dispose()
        if isinstance(self._raw_store, MongoRawStore):
            self._raw_store.client.close()

    def migrate(self, _: argparse.Namespace) -> int:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", self.settings.database_url.replace("%", "%%"))
        command.upgrade(config, "head")
        self.out("Base de datos actualizada.")
        return 0

    def scrape(self, args: argparse.Namespace) -> int:
        if not args.term and not args.ean:
            self.out("Indicá al menos un --term o un --ean.")
            return 2
        client = RequestsClient(
            self.settings.user_agent,
            timeout=self.settings.request_timeout_seconds,
            delay=self.settings.request_delay_seconds,
        )
        scrapers = build_scrapers(client, self.settings)
        summaries = []
        with self.session() as session:
            for code in args.super:
                summary = pipeline.scrape(
                    session, scrapers[code], self.raw_store, args.term, args.ean, args.max
                )
                summaries.append(summary)
                self._print_summary(summary)
        return 0 if all(s.status is RunStatus.OK for s in summaries) else 1

    def demo(self, _: argparse.Namespace) -> int:
        """Runs the full pipeline over the bundled samples: two captures one day apart."""
        now = utcnow()
        with self.session() as session:
            for offset, day in zip((timedelta(days=1), timedelta(0)), DAYS, strict=True):
                scrapers = build_scrapers(SampleClient(day), self.settings)
                captured_at = now - offset
                for code in SUPERMARKETS:
                    summary = pipeline.scrape(
                        session, scrapers[code], self.raw_store, DEMO_TERMS, clock=lambda c=captured_at: c
                    )
                    self._print_summary(summary)
        if isinstance(self.raw_store, NullRawStore):
            self.out("MongoDB no configurado (MONGO_URL vacío): no se archivaron las respuestas crudas.")
        self.out("\nProbá: python -m preciosdb compare leche")
        return 0

    def compare(self, args: argparse.Namespace) -> int:
        with self.session() as session:
            rows = reports.compare(session, search=args.search, ean=args.ean, limit=args.limit)
        self.out(
            render_table(
                rows,
                [
                    ("ean", "EAN"),
                    ("product", "Producto"),
                    ("supermarket", "Súper"),
                    ("price", "Precio"),
                    ("regular_price", "Regular"),
                    ("diff_vs_best", "Dif. vs. mejor"),
                    ("pct_vs_best", "%"),
                    ("promo_text", "Otras promos"),
                ],
            )
        )
        return 0

    def history(self, args: argparse.Namespace) -> int:
        with self.session() as session:
            rows = reports.history(session, args.ean)
        self.out(
            render_table(
                rows,
                [
                    ("captured_at", "Fecha (UTC)"),
                    ("supermarket", "Súper"),
                    ("product", "Producto"),
                    ("price", "Precio"),
                    ("regular_price", "Regular"),
                    ("promo_text", "Otras promos"),
                ],
            )
        )
        return 0

    def changes(self, args: argparse.Namespace) -> int:
        with self.session() as session:
            rows = reports.changes(session, utcnow() - timedelta(days=args.days), args.limit)
        self.out(
            render_table(
                rows,
                [
                    ("captured_at", "Fecha (UTC)"),
                    ("supermarket", "Súper"),
                    ("product", "Producto"),
                    ("previous_price", "Antes"),
                    ("price", "Ahora"),
                    ("change_pct", "Var. %"),
                ],
            )
        )
        return 0

    def sql(self, args: argparse.Namespace) -> int:
        path = Path(args.file)
        if not path.is_file():
            # Compared by name: what the user typed is not a glob pattern (it can be a path, or have "[").
            available = sorted(reports.SQL_DIR.glob("*.sql"))
            matches = [query for query in available if args.file and query.name.startswith(args.file)]
            if not matches:
                self.out(f"No encontré {args.file}. Consultas disponibles:")
                for query in available:
                    self.out(f"  {query.stem}")
                return 2
            path = matches[0]
        with self.session() as session:
            self.out(render_table(reports.run_sql_file(session, path)))
        return 0

    def runs(self, args: argparse.Namespace) -> int:
        with self.session() as session:
            rows = [
                {
                    "id": run.id,
                    "supermarket": run.supermarket.code,
                    "status": run.status.value,
                    "started_at": run.started_at,
                    "pages": run.pages,
                    "prices": run.prices_saved,
                    "terms": run.terms,
                    "error": (run.error or "")[:60] or None,
                }
                for run in reports.recent_runs(session, args.limit)
            ]
        self.out(render_table(rows))
        return 0

    def reprocess(self, args: argparse.Namespace) -> int:
        scrapers = build_scrapers(SampleClient(), self.settings)  # only `parse` is used: no requests
        with self.session() as session:
            try:
                summary = pipeline.reprocess(session, args.run_id, self.raw_store, scrapers)
            except LookupError as exc:
                self.out(str(exc))
                return 1
        self._print_summary(summary)
        return 0

    def branches(self, args: argparse.Namespace) -> int:
        store = self.raw_store
        if not isinstance(store, MongoRawStore):
            self.out("Este reporte lee la zona raw: configurá MONGO_URL.")
            return 2
        rows = [{**row, "price": to_money(row["price"])} for row in store.coto_branch_prices(args.ean)]
        self.out(render_table(rows, [("branch", "Sucursal"), ("price", "Precio"), ("product", "Producto")]))
        prices = [row["price"] for row in rows if row["price"] is not None]
        if prices:
            self.out(f"\n{len(rows)} sucursales · mín. {fmt(min(prices))} · máx. {fmt(max(prices))}")
        return 0

    def raw_stats(self, _: argparse.Namespace) -> int:
        store = self.raw_store
        if not isinstance(store, MongoRawStore):
            self.out("MongoDB no configurado (MONGO_URL vacío).")
            return 2
        self.out(render_table(store.stats()))
        return 0

    def _print_summary(self, summary: pipeline.RunSummary) -> None:
        mark = "✔" if summary.status is RunStatus.OK else "✘"
        line = (
            f"{mark} {summary.supermarket:<10} corrida #{summary.run_id}: {summary.pages} páginas, "
            f"{summary.products} productos, {summary.prices} precios guardados"
        )
        if summary.error:
            line += f"\n    error: {summary.error}"
        self.out(line)


# 100 years back is plenty, and much further the date no longer fits in a datetime (OverflowError).
MAX_DAYS = 36_500


def positive_int(maximum: int | None = None) -> Callable[[str], int]:
    """argparse type: a whole number from 1 up to `maximum`, checked before touching the database."""

    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"'{text}' no es un número entero") from None
        if value < 1:
            raise argparse.ArgumentTypeError(f"tiene que ser mayor que cero (se recibió {value})")
        if maximum is not None and value > maximum:
            raise argparse.ArgumentTypeError(f"tiene que ser como mucho {maximum} (se recibió {value})")
        return value

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="preciosdb", description="Precios de supermercados argentinos: Coto, Día y Carrefour."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="crea/actualiza las tablas y vistas (Alembic)")

    p = sub.add_parser("scrape", help="busca productos en los sitios y guarda los precios")
    p.add_argument("--super", nargs="+", choices=SUPERMARKETS, default=list(SUPERMARKETS))
    p.add_argument("--term", action="append", default=[], help="texto a buscar (se puede repetir)")
    p.add_argument("--ean", action="append", default=[], help="código de barras a buscar (se puede repetir)")
    p.add_argument(
        "--max", type=positive_int(), default=100, help="máximo de productos por búsqueda (default 100)"
    )

    sub.add_parser("demo", help="carga datos de ejemplo sin conexión (dos capturas, un día de diferencia)")

    p = sub.add_parser("compare", help="compara el mismo producto entre supermercados")
    p.add_argument("search", nargs="?", help="parte del nombre del producto")
    p.add_argument("--ean")
    p.add_argument("--limit", type=positive_int(), default=60)

    p = sub.add_parser("history", help="historial de precios de un código de barras")
    p.add_argument("ean")

    p = sub.add_parser("changes", help="cambios de precio recientes")
    p.add_argument("--days", type=positive_int(MAX_DAYS), default=7)
    p.add_argument("--limit", type=positive_int(), default=30)

    p = sub.add_parser("sql", help="ejecuta una consulta de sql/consultas (por nombre o ruta)")
    p.add_argument("file", help="ej.: 01_canasta_comparable")

    p = sub.add_parser("runs", help="últimas corridas de scraping")
    p.add_argument("--limit", type=positive_int(), default=15)

    p = sub.add_parser("reprocess", help="reconstruye los precios de una corrida desde MongoDB")
    p.add_argument("run_id", type=int)

    p = sub.add_parser("branches", help="precio de un producto en cada sucursal de Coto (MongoDB)")
    p.add_argument("ean")

    sub.add_parser("raw-stats", help="resumen de la zona raw en MongoDB")
    return parser


def _ensure_utf8_output() -> None:
    """On Windows, redirected output uses a legacy code page (cp1252) that cannot encode "✔"."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main(
    argv: Sequence[str] | None = None, settings: Settings | None = None, out: Callable[[str], None] = print
) -> int:
    _ensure_utf8_output()
    args = build_parser().parse_args(argv)
    app = App(settings or Settings(), out)
    handler = getattr(app, args.command.replace("-", "_"))
    try:
        return handler(args)
    except (OperationalError, ProgrammingError):  # a missing table is one in SQLite and the other in MySQL
        if args.command == "migrate" or app.is_migrated():
            raise
        out("La base de datos todavía no tiene las tablas. Corré primero: python -m preciosdb migrate")
        return 2
    finally:
        app.close()


if __name__ == "__main__":
    sys.exit(main())
