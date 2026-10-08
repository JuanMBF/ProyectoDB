"""Writes scraped pages into the relational model.

Uses plain SELECT + INSERT/UPDATE instead of dialect-specific upserts, so the same code runs on MySQL
and SQLite. Each page is loaded with two batched SELECTs (listings by SKU, products by EAN).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from preciosdb.db.models import Listing, PriceSnapshot, Product, RunStatus, ScrapeRun, Supermarket
from preciosdb.scrapers.base import ScrapedProduct, comparable_ean


class PriceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._saved_in_run: dict[int, set[int]] = {}

    def supermarket(self, code: str, name: str, website: str) -> Supermarket:
        supermarket = self.session.scalar(select(Supermarket).where(Supermarket.code == code))
        if supermarket is None:
            supermarket = Supermarket(code=code, name=name, website=website)
            self.session.add(supermarket)
            self.session.flush()
        return supermarket

    def start_run(self, supermarket: Supermarket, terms: Iterable[str], started_at: datetime) -> ScrapeRun:
        run = ScrapeRun(
            supermarket_id=supermarket.id,
            terms=", ".join(terms)[:500],
            started_at=started_at,
            status=RunStatus.RUNNING,
            pages=0,
            products_seen=0,
            prices_saved=0,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def finish_run(
        self, run: ScrapeRun, status: RunStatus, finished_at: datetime, error: str | None = None
    ) -> None:
        run.status = status
        run.finished_at = finished_at
        run.error = error

    def reset_run(self, run: ScrapeRun) -> None:
        """Deletes the prices of a run so they can be rebuilt from the raw zone."""
        self.session.execute(delete(PriceSnapshot).where(PriceSnapshot.scrape_run_id == run.id))
        self._saved_in_run.pop(run.id, None)
        run.pages = run.products_seen = run.prices_saved = 0

    def save_page(
        self,
        run: ScrapeRun,
        products: list[ScrapedProduct],
        captured_at: datetime,
        raw_ref: str | None = None,
    ) -> int:
        """Upserts listings/products and appends one price snapshot per listing. Returns prices saved."""
        listings = self._listings(run.supermarket_id, products, captured_at)
        saved_in_run = self._saved_in_run.setdefault(run.id, set())
        saved = 0
        for scraped in products:
            listing = listings[scraped.sku]
            # The same SKU can show up for two search terms: keep a single price per run.
            if listing.id in saved_in_run:
                continue
            self.session.add(
                PriceSnapshot(
                    listing_id=listing.id,
                    scrape_run_id=run.id,
                    captured_at=captured_at,
                    price=scraped.price,
                    regular_price=scraped.regular_price or scraped.price,
                    promo_text=scraped.promo_text,
                    is_available=scraped.is_available and scraped.price is not None,
                    raw_ref=raw_ref,
                )
            )
            saved_in_run.add(listing.id)
            saved += 1
        run.pages += 1
        run.products_seen += len(products)
        run.prices_saved += saved
        self.session.flush()
        return saved

    def _listings(
        self, supermarket_id: int, products: list[ScrapedProduct], seen_at: datetime
    ) -> dict[str, Listing]:
        skus = {p.sku for p in products}
        existing = {
            listing.sku: listing
            for listing in self.session.scalars(
                select(Listing).where(Listing.supermarket_id == supermarket_id, Listing.sku.in_(skus))
            )
        }
        catalog = self._products(products)
        for scraped in products:
            listing = existing.get(scraped.sku)
            if listing is None:
                listing = Listing(supermarket_id=supermarket_id, sku=scraped.sku, first_seen_at=seen_at)
                self.session.add(listing)
                existing[scraped.sku] = listing
            ean = comparable_ean(scraped.ean)
            listing.product = catalog.get(ean) if ean else None
            listing.ean = (scraped.ean or "")[:14] or None
            listing.name = scraped.name[:255]
            listing.brand = scraped.brand[:120] if scraped.brand else None
            listing.category = scraped.category[:255] if scraped.category else None
            listing.unit = scraped.unit[:40] if scraped.unit else None
            listing.unit_quantity = scraped.unit_quantity
            listing.url = scraped.url[:500] if scraped.url else None
            listing.image_url = scraped.image_url[:500] if scraped.image_url else None
            listing.last_seen_at = max(seen_at, listing.last_seen_at or seen_at)
        self.session.flush()
        return existing

    def _products(self, products: list[ScrapedProduct]) -> dict[str, Product]:
        by_ean = {ean: p for p in products if (ean := comparable_ean(p.ean))}
        if not by_ean:
            return {}
        catalog = {
            product.ean: product
            for product in self.session.scalars(select(Product).where(Product.ean.in_(by_ean)))
        }
        for ean, scraped in by_ean.items():
            if ean not in catalog:
                catalog[ean] = Product(
                    ean=ean, name=scraped.name[:255], brand=scraped.brand[:120] if scraped.brand else None
                )
                self.session.add(catalog[ean])
        return catalog
