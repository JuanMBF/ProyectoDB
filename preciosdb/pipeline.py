"""Scrape -> raw zone (MongoDB) -> relational model (MySQL/SQLite).

Each page is committed as soon as it is processed: if a site fails halfway, what was already captured
stays saved and the run is marked as failed with the error.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from preciosdb.db.models import RunStatus, ScrapeRun, Supermarket
from preciosdb.db.repository import PriceRepository
from preciosdb.db.types import utcnow
from preciosdb.raw_store import RawStore
from preciosdb.scrapers.base import Scraper

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class RunSummary:
    run_id: int
    supermarket: str
    status: RunStatus
    pages: int
    products: int
    prices: int
    error: str | None = None

    @classmethod
    def of(cls, run: ScrapeRun, supermarket: str) -> RunSummary:
        return cls(run.id, supermarket, run.status, run.pages, run.products_seen, run.prices_saved, run.error)


def scrape(
    session: Session,
    scraper: Scraper,
    raw_store: RawStore,
    terms: Iterable[str] = (),
    eans: Iterable[str] = (),
    max_results: int = 100,
    clock: Clock = utcnow,
) -> RunSummary:
    terms, eans = list(terms), list(eans)
    repo = PriceRepository(session)
    supermarket = repo.supermarket(scraper.code, scraper.name, scraper.website)
    run = repo.start_run(supermarket, [*terms, *(f"ean:{e}" for e in eans)], clock())
    session.commit()

    pages = itertools.chain(
        itertools.chain.from_iterable(scraper.search(term, max_results) for term in terms),
        (scraper.find_by_ean(ean) for ean in eans),
    )
    try:
        for page in pages:
            fetched_at = clock()
            raw_ref = raw_store.save_page(run.id, page, fetched_at)
            repo.save_page(run, page.products, fetched_at, raw_ref)
            session.commit()
    except Exception as exc:  # network errors, blocked requests, API format changes...
        session.rollback()
        repo.finish_run(run, RunStatus.FAILED, clock(), error=f"{type(exc).__name__}: {exc}"[:2000])
    else:
        repo.finish_run(run, RunStatus.OK, clock())
    session.commit()
    return RunSummary.of(run, supermarket.code)


def reprocess(
    session: Session, run_id: int, raw_store: RawStore, scrapers: Mapping[str, Scraper]
) -> RunSummary:
    """Rebuilds the prices of a run from the JSON archived in MongoDB, without hitting the sites."""
    run = session.get(ScrapeRun, run_id)
    if run is None:
        raise LookupError(f"No existe la corrida {run_id}.")
    supermarket = session.get_one(Supermarket, run.supermarket_id)
    documents = list(raw_store.pages_for_run(run_id))
    if not documents:
        raise LookupError(f"La corrida {run_id} no tiene respuestas guardadas en MongoDB.")

    scraper = scrapers[supermarket.code]
    repo = PriceRepository(session)
    repo.reset_run(run)
    for document in documents:
        products = scraper.parse(document["payload"])
        repo.save_page(run, products, document["fetched_at"], str(document["_id"]))
    session.commit()
    return RunSummary.of(run, supermarket.code)
