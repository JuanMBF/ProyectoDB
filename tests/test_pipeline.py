from decimal import Decimal

import pytest
from sqlalchemy import func, select

from preciosdb import pipeline
from preciosdb.config import Settings
from preciosdb.db.models import Listing, PriceSnapshot, Product, RunStatus, ScrapeRun, Supermarket
from preciosdb.raw_store import NullRawStore
from preciosdb.samples import DEMO_TERMS, SampleClient
from preciosdb.scrapers import SUPERMARKETS, build_scrapers
from tests.conftest import DAY1, DAY2, FakeClient


def scrapers(day: str = "day1"):
    return build_scrapers(SampleClient(day), Settings(_env_file=None))


def load_both_days(session, raw_store):
    for day, when in (("day1", DAY1), ("day2", DAY2)):
        for code in SUPERMARKETS:
            pipeline.scrape(session, scrapers(day)[code], raw_store, DEMO_TERMS, clock=lambda w=when: w)


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_scrape_saves_listings_prices_and_raw_pages(session, raw_store):
    summary = pipeline.scrape(session, scrapers()["coto"], raw_store, ["leche", "queso"], clock=lambda: DAY1)

    assert summary.status is RunStatus.OK
    assert (summary.pages, summary.products, summary.prices) == (2, 3, 3)
    assert count(session, Listing) == 3
    run = session.get(ScrapeRun, summary.run_id)
    assert run.terms == "leche, queso"
    assert run.started_at == run.finished_at == DAY1
    assert raw_store.collection.count_documents({"run_id": summary.run_id}) == 2
    snapshot = session.scalars(select(PriceSnapshot)).first()
    assert snapshot.raw_ref is not None
    assert raw_store.collection.find_one({"run_id": summary.run_id, "term": "leche"}) is not None


def test_products_are_shared_by_barcode_across_supermarkets(session):
    for code in SUPERMARKETS:
        pipeline.scrape(session, scrapers()[code], NullRawStore(), DEMO_TERMS, clock=lambda: DAY1)

    # 6 products have a real barcode; Coto's weighed cheese and Día's own milk without EAN are not linked.
    assert count(session, Product) == 6
    assert count(session, Listing) == 20
    unlinked = session.scalars(select(Listing.name).where(Listing.product_id.is_(None))).all()
    assert sorted(unlinked) == ["Leche Descremada DIA 1 Lt.", "Queso Cremoso LA PAULINA Xkg"]
    leche = session.scalar(select(Product).where(Product.ean == "7790742363008"))
    chains = session.scalars(
        select(Supermarket.code)
        .join(Listing)
        .where(Listing.product_id == leche.id)
        .order_by(Supermarket.code)
    ).all()
    assert chains == ["carrefour", "coto", "dia"]


def test_same_sku_found_by_two_terms_gets_one_price_per_run(session):
    summary = pipeline.scrape(
        session, scrapers()["dia"], NullRawStore(), ["leche", "leche clasica"], clock=lambda: DAY1
    )

    assert summary.products > summary.prices
    repeated = session.scalars(
        select(PriceSnapshot.listing_id).group_by(PriceSnapshot.listing_id).having(func.count() > 1)
    ).all()
    assert repeated == []


def test_second_capture_appends_history_and_updates_listing(session):
    load_both_days(session, NullRawStore())

    listing = session.scalar(select(Listing).where(Listing.sku == "144373"))  # Carrefour milk
    prices = session.scalars(
        select(PriceSnapshot.price)
        .where(PriceSnapshot.listing_id == listing.id)
        .order_by(PriceSnapshot.captured_at)
    ).all()
    assert prices == [Decimal("1939.00"), Decimal("2915.00")]
    assert listing.first_seen_at == DAY1
    assert listing.last_seen_at == DAY2


def test_out_of_stock_is_recorded_without_price(session):
    load_both_days(session, NullRawStore())

    aceite = session.scalar(select(Listing).where(Listing.sku == "31792"))  # Carrefour, no stock on day 2
    last = session.scalar(
        select(PriceSnapshot)
        .where(PriceSnapshot.listing_id == aceite.id)
        .order_by(PriceSnapshot.captured_at.desc())
    )
    assert last.price is None
    assert not last.is_available


def test_failure_marks_the_run_and_keeps_pages_already_saved(session):
    good_page = {
        "response": {"results": [{"value": "Leche", "data": {"sku_plu": 1, "product_list_price": 10}}]}
    }
    client = FakeClient(good_page, ConnectionError("sitio caído"))
    coto = build_scrapers(client, Settings(_env_file=None))["coto"]

    summary = pipeline.scrape(session, coto, NullRawStore(), ["leche", "yerba"], clock=lambda: DAY1)

    assert summary.status is RunStatus.FAILED
    assert summary.error == "ConnectionError: sitio caído"
    assert count(session, PriceSnapshot) == 1
    assert session.get(ScrapeRun, summary.run_id).finished_at == DAY1


def test_scrape_by_ean(session):
    summary = pipeline.scrape(
        session, scrapers()["carrefour"], NullRawStore(), eans=["7790742363008"], clock=lambda: DAY1
    )

    assert summary.prices == 1
    assert session.get(ScrapeRun, summary.run_id).terms == "ean:7790742363008"


def test_reprocess_rebuilds_prices_from_mongodb(session, raw_store):
    summary = pipeline.scrape(session, scrapers()["coto"], raw_store, DEMO_TERMS, clock=lambda: DAY1)
    before = session.scalars(select(PriceSnapshot.price).order_by(PriceSnapshot.listing_id)).all()

    again = pipeline.reprocess(session, summary.run_id, raw_store, scrapers())

    after = session.scalars(select(PriceSnapshot.price).order_by(PriceSnapshot.listing_id)).all()
    assert after == before
    assert (again.pages, again.prices) == (summary.pages, summary.prices)
    refs = session.scalars(select(PriceSnapshot.raw_ref).distinct()).all()
    assert all(refs)
    assert len(refs) == summary.pages


def test_reprocess_errors(session, raw_store):
    with pytest.raises(LookupError, match="No existe"):
        pipeline.reprocess(session, 999, raw_store, scrapers())

    summary = pipeline.scrape(session, scrapers()["dia"], NullRawStore(), ["leche"], clock=lambda: DAY1)
    with pytest.raises(LookupError, match="no tiene respuestas"):
        pipeline.reprocess(session, summary.run_id, raw_store, scrapers())
