"""Common pieces of every scraper: the normalized product, raw pages and price/barcode helpers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlencode

from preciosdb.http import HttpClient

CENTS = Decimal("0.01")

# Largest amount that fits the DECIMAL(12, 2) price columns. A bigger number is garbage in the source,
# and rounding it to cents can even raise InvalidOperation (more digits than the decimal context holds).
MAX_MONEY = Decimal("9999999999.99")

# Same for the DECIMAL(10, 3) unit quantity column.
MAX_QUANTITY = Decimal("9999999.999")

# A "regular price" more than 3x the price paid is a unit/format error in the source, not a discount:
# supermarkets do not sell at -66 %. In that case we assume there is no discount instead of inventing one.
MAX_REGULAR_TO_PRICE_RATIO = 3


@dataclass(frozen=True)
class ScrapedProduct:
    """One sellable item (SKU) of one supermarket, already normalized."""

    sku: str
    name: str
    brand: str | None = None
    ean: str | None = None
    price: Decimal | None = None  # what you pay for one unit today (direct discounts applied)
    regular_price: Decimal | None = None  # price without the direct discount (== price when there is none)
    promo_text: str | None = None  # conditional promos (2x1, 70% 2da...) that do not change the unit price
    is_available: bool = True
    url: str | None = None
    image_url: str | None = None
    category: str | None = None
    unit: str | None = None
    unit_quantity: Decimal | None = None


@dataclass(frozen=True)
class Page:
    """One API response: the raw payload (archived as-is in MongoDB) plus the products parsed from it."""

    supermarket: str
    term: str
    page: int
    url: str
    payload: Any
    products: list[ScrapedProduct] = field(default_factory=list)
    total: int | None = None


class Scraper(ABC):
    code: str
    name: str
    website: str

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    @abstractmethod
    def search(self, term: str, max_results: int = 100) -> Iterator[Page]:
        """Full-text search, paginated. Yields one Page per request."""

    @abstractmethod
    def find_by_ean(self, ean: str) -> Page:
        """Looks up a product by barcode (used to follow a fixed basket across supermarkets)."""

    @abstractmethod
    def parse(self, payload: Any) -> list[ScrapedProduct]:
        """Turns a raw payload into products. Pure: also used to reprocess payloads stored in MongoDB."""


# --------------------------------------------------------------------------- helpers


def build_url(base: str, params: dict[str, Any]) -> str:
    # %20 instead of "+" for spaces: both APIs treat the search text literally.
    return f"{base}?{urlencode(params, quote_via=quote)}"


def to_money(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if not amount.is_finite() or abs(amount) > MAX_MONEY:
        return None
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite() or abs(number) > MAX_QUANTITY:
        return None
    return number


_MONEY = re.compile(r"\$?\s*(\d[\d.,]*)")
_THOUSANDS_DOTS = re.compile(r"\d{1,3}(\.\d{3})+")


def parse_money(text: Any) -> Decimal | None:
    """Parses amounts written as text: "$2186.25", "Precio Contado: $2915", "$ 1.898,97", "$13.197"."""
    if not isinstance(text, str):
        return to_money(text)
    match = _MONEY.search(text)
    if not match:
        return None
    number = match.group(1).rstrip(".,")
    if "," in number and "." in number:  # 1.898,97
        number = number.replace(".", "").replace(",", ".")
    elif "," in number:  # 1898,97
        number = number.replace(",", ".")
    elif _THOUSANDS_DOTS.fullmatch(number):  # 13.197
        number = number.replace(".", "")
    return to_money(number)


def plausible_regular_price(price: Decimal | None, candidates: list[Decimal | None]) -> Decimal | None:
    """Highest candidate in [price, 3 x price]; the price itself when no candidate is believable."""
    if price is None:
        return None
    plausible = [c for c in candidates if c is not None and price <= c <= price * MAX_REGULAR_TO_PRICE_RATIO]
    return max(plausible, default=price)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


# --------------------------------------------------------------------------- barcodes (GTIN / EAN)


def gtin_check_digit_ok(code: str) -> bool:
    """GS1 mod-10 check: weights 3,1,3,1... from the rightmost data digit."""
    *body, check = (int(d) for d in code)
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def comparable_ean(value: Any) -> str | None:
    """Barcode usable to match the same product across supermarkets, or None.

    Rejects malformed codes and GS1 "restricted circulation" prefixes (020-029, 040-049, 200-299):
    those are assigned by each store (weighed cheese, meat...) and mean different things in each chain.
    """
    code = clean_text(value)
    # isascii(): str.isdigit() also accepts "²" or fullwidth digits, which are not barcodes.
    if code is None or not (code.isascii() and code.isdigit()) or len(code) not in (8, 12, 13, 14):
        return None
    if not gtin_check_digit_ok(code):
        return None
    if len(code) == 12:  # UPC-A is the same number as EAN-13 with a leading zero
        code = "0" + code
    if len(code) == 13 and (code[0] == "2" or code[:2] in ("02", "04")):
        return None
    return code
