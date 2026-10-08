"""Relational model.

    supermarkets 1─* listings *─1 products        (a product = one barcode, shared by every chain)
    supermarkets 1─* scrape_runs 1─* price_snapshots *─1 listings

A *listing* is a product as one supermarket sells it (its own SKU, name and URL). Prices are never
updated in place: every run appends a snapshot, so the history stays queryable with plain SQL.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from preciosdb.db.base import Base
from preciosdb.db.types import FixedDecimal, UTCDateTime, enum_type, money, utcnow

# BIGINT in MySQL; SQLite only auto-increments a column declared exactly as INTEGER PRIMARY KEY.
BigId = BigInteger().with_variant(Integer(), "sqlite")


class RunStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"


class Supermarket(Base):
    __tablename__ = "supermarkets"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    website: Mapped[str] = mapped_column(String(200))


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    ean: Mapped[str] = mapped_column(String(14), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("supermarket_id", "sku"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    supermarket_id: Mapped[int] = mapped_column(ForeignKey("supermarkets.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    ean: Mapped[str | None] = mapped_column(String(14))  # as published, even when it is a store-only code
    name: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(255))
    unit: Mapped[str | None] = mapped_column(String(40))
    unit_quantity: Mapped[Decimal | None] = mapped_column(FixedDecimal(10, 3))
    url: Mapped[str | None] = mapped_column(String(500))
    image_url: Mapped[str | None] = mapped_column(String(500))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)

    supermarket: Mapped[Supermarket] = relationship()
    product: Mapped[Product | None] = relationship()


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    supermarket_id: Mapped[int] = mapped_column(ForeignKey("supermarkets.id"), index=True)
    status: Mapped[RunStatus] = mapped_column(enum_type(RunStatus), default=RunStatus.RUNNING)
    terms: Mapped[str] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pages: Mapped[int] = mapped_column(default=0)
    products_seen: Mapped[int] = mapped_column(default=0)
    prices_saved: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)

    supermarket: Mapped[Supermarket] = relationship()


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    __table_args__ = (
        UniqueConstraint("listing_id", "scrape_run_id"),
        Index("ix_price_snapshots_listing_captured", "listing_id", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    scrape_run_id: Mapped[int] = mapped_column(ForeignKey("scrape_runs.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    # NULL when the product was listed without price or stock: the last snapshot then says "not
    # available" instead of leaving an old price looking current.
    price: Mapped[Decimal | None] = mapped_column(money())
    regular_price: Mapped[Decimal | None] = mapped_column(money())
    promo_text: Mapped[str | None] = mapped_column(String(120))
    is_available: Mapped[bool] = mapped_column(default=True)
    # _id of the MongoDB document holding the API response this price was parsed from.
    raw_ref: Mapped[str | None] = mapped_column(String(24))

    listing: Mapped[Listing] = relationship()
