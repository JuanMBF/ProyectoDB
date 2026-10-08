"""Supermarkets on the VTEX platform (Día and Carrefour), through VTEX's public catalog search API.

Response: a JSON array of products. Each product has one or more `items` (SKUs) with their EAN and
`sellers[].commertialOffer` with `Price` (what you pay), `ListPrice` and `PriceWithoutDiscount`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from preciosdb.http import HttpClient
from preciosdb.scrapers.base import (
    Page,
    ScrapedProduct,
    Scraper,
    build_url,
    clean_text,
    plausible_regular_price,
    to_decimal,
    to_money,
)

SEARCH_PATH = "/api/catalog_system/pub/products/search"


class VtexScraper(Scraper):
    PAGE_SIZE = 50  # the legacy search API returns at most 50 products per request

    def __init__(self, client: HttpClient, code: str, name: str, base_url: str) -> None:
        super().__init__(client)
        self.code = code
        self.name = name
        self.website = base_url.rstrip("/")

    @property
    def search_url(self) -> str:
        return self.website + SEARCH_PATH

    def search(self, term: str, max_results: int = 100) -> Iterator[Page]:
        start, page = 0, 1
        while start < max_results:
            end = min(start + self.PAGE_SIZE, max_results) - 1
            url = build_url(self.search_url, {"ft": term, "_from": start, "_to": end})
            response = self.client.get_json(url)
            data = response.data if isinstance(response.data, list) else []
            yield Page(self.code, term, page, response.url, response.data, self.parse(data))
            if len(data) < end - start + 1:
                return
            start, page = end + 1, page + 1

    def find_by_ean(self, ean: str) -> Page:
        url = build_url(self.search_url, {"fq": f"alternateIds_Ean:{ean}"})
        response = self.client.get_json(url)
        return Page(self.code, f"ean:{ean}", 1, response.url, response.data, self.parse(response.data))

    def parse(self, payload: Any) -> list[ScrapedProduct]:
        if not isinstance(payload, list):
            return []
        products = []
        for product in payload:
            category = _category(product.get("categories"))
            link = product.get("link") or (
                f"{self.website}/{product['linkText']}/p" if product.get("linkText") else None
            )
            for item in product.get("items") or []:
                if item.get("itemId") is None:
                    continue
                offer = _default_offer(item)
                price = to_money(offer.get("Price"))
                if price is not None and price <= 0:  # VTEX reports 0 when there is no stock
                    price = None
                regular = plausible_regular_price(
                    price, [to_money(offer.get("ListPrice")), to_money(offer.get("PriceWithoutDiscount"))]
                )
                images = item.get("images") or []
                products.append(
                    ScrapedProduct(
                        sku=str(item["itemId"]),
                        name=clean_text(item.get("nameComplete") or product.get("productName"))
                        or str(item["itemId"]),
                        brand=clean_text(product.get("brand")),
                        ean=clean_text(item.get("ean")),
                        price=price,
                        regular_price=regular,
                        is_available=price is not None and bool(offer.get("IsAvailable", True)),
                        url=link,
                        image_url=images[0].get("imageUrl") if images else None,
                        category=category,
                        unit=clean_text(item.get("measurementUnit")),
                        unit_quantity=to_decimal(item.get("unitMultiplier")),
                    )
                )
        return products


def _default_offer(item: dict[str, Any]) -> dict[str, Any]:
    sellers = item.get("sellers") or []
    seller = next((s for s in sellers if s.get("sellerDefault")), sellers[0] if sellers else {})
    return seller.get("commertialOffer") or {}


def _category(paths: Any) -> str | None:
    """VTEX lists the deepest category first.

    "/Almacén/Infusiones/Yerba/" -> "Almacén > Infusiones > Yerba"
    """
    if not paths:
        return None
    parts = [part.strip() for part in str(paths[0]).split("/") if part.strip()]
    return " > ".join(parts) or None
