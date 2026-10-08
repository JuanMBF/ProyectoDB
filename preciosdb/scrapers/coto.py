"""Coto Digital, through the Constructor.io search API that powers the search box of Coto's website.

Response: `{"response": {"results": [{"value": ..., "data": {...}}], "total_num_results": N}}`.
`data` carries the PLU (`sku_plu`), the EAN (`product_main_ean`), the online list price
(`product_list_price`), the price in each branch (`price[]`) and the active promotions (`discounts[]`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from preciosdb.http import HttpClient
from preciosdb.scrapers.base import (
    Page,
    ScrapedProduct,
    Scraper,
    build_url,
    clean_text,
    parse_money,
    plausible_regular_price,
    to_decimal,
    to_money,
)

# "25%Dto" lowers the price of a single unit. Everything else ("2x1", "70% 2da", "3x2", "+10%")
# depends on how many units you buy or how you pay, so it is kept as text and not applied.
DIRECT_DISCOUNT = re.compile(r"^\s*\d+(?:[.,]\d+)?\s*%\s*dto\.?\s*$", re.IGNORECASE)


class CotoScraper(Scraper):
    code = "coto"
    name = "Coto"
    website = "https://www.cotodigital.com.ar"
    PAGE_SIZE = 50

    def __init__(self, client: HttpClient, api_url: str, api_key: str, product_base_url: str) -> None:
        super().__init__(client)
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.product_base_url = product_base_url.rstrip("/")

    def _search_page(self, term: str, page: int, per_page: int) -> tuple[str, Any]:
        url = build_url(
            f"{self.api_url}/search/{quote(term, safe='')}",
            {"key": self.api_key, "section": "Products", "page": page, "num_results_per_page": per_page},
        )
        response = self.client.get_json(url)
        return response.url, response.data

    def search(self, term: str, max_results: int = 100) -> Iterator[Page]:
        per_page = min(self.PAGE_SIZE, max_results)
        page, fetched = 1, 0
        while fetched < max_results:
            url, data = self._search_page(term, page, per_page)
            body = data.get("response", {}) if isinstance(data, dict) else {}
            results = body.get("results") or []
            products = self.parse(data)[: max_results - fetched]
            yield Page(self.code, term, page, url, data, products, body.get("total_num_results"))
            fetched += len(results)
            if len(results) < per_page:
                return
            page += 1

    def find_by_ean(self, ean: str) -> Page:
        url, data = self._search_page(ean, 1, 10)
        return Page(self.code, f"ean:{ean}", 1, url, data, self.parse(data))

    def parse(self, payload: Any) -> list[ScrapedProduct]:
        if not isinstance(payload, dict):
            return []
        products = []
        for result in payload.get("response", {}).get("results") or []:
            data = result.get("data") or {}
            sku = data.get("sku_plu")
            if sku is None:
                sku = data.get("id")
            if sku is None:
                continue
            price, regular, promo = coto_prices(
                to_money(data.get("product_list_price")), data.get("discounts")
            )
            relative_url = clean_text(data.get("url"))
            products.append(
                ScrapedProduct(
                    sku=str(sku),
                    name=clean_text(data.get("sku_display_name") or result.get("value")) or str(sku),
                    brand=clean_text(data.get("product_brand")),
                    ean=clean_text(data.get("product_main_ean")),
                    price=price,
                    regular_price=regular,
                    promo_text=promo,
                    is_available=price is not None and bool(data.get("store_availability", True)),
                    url=f"{self.product_base_url}/{relative_url.lstrip('/')}" if relative_url else None,
                    image_url=data.get("product_large_image_url") or data.get("image_url"),
                    category=_category(data.get("groups")),
                    unit=clean_text(data.get("product_format") or data.get("product_unit_of_measure")),
                    unit_quantity=to_decimal(data.get("product_format_quantity")),
                )
            )
        return products


def coto_prices(
    list_price: Decimal | None, discounts: Any
) -> tuple[Decimal | None, Decimal | None, str | None]:
    """Returns (price paid for one unit, regular price, conditional promotions as text)."""
    if list_price is None or list_price <= 0:
        return None, None, None
    price = list_price
    regular_candidates: list[Decimal | None] = [list_price]
    promos = []
    for discount in discounts if isinstance(discounts, list) else []:
        text = clean_text(discount.get("discountText"))
        discount_price = parse_money(discount.get("discountPrice"))
        # "Precio Contado: $13299" when the online price is already the promotional one.
        regular_candidates.append(parse_money(discount.get("regularPriceText")))
        if text and DIRECT_DISCOUNT.match(text):
            if discount_price is not None and 0 < discount_price < price:
                price = discount_price
        elif text:
            taking = clean_text(discount.get("takingText"))
            promos.append(f"{text} ({taking.lower()})" if taking else text)
    regular = plausible_regular_price(price, regular_candidates)
    return price, regular, " · ".join(promos)[:120] or None


def _category(groups: Any) -> str | None:
    """Constructor.io groups: {"display_name": ..., "path_list": [{"display_name": ...}, ...ancestors]}."""
    if not isinstance(groups, list) or not groups:
        return None
    group = groups[0]
    names = [clean_text(p.get("display_name")) for p in group.get("path_list") or []]
    names.append(clean_text(group.get("display_name")))
    parts = [n for n in names if n]
    return " > ".join(dict.fromkeys(parts)) or None
