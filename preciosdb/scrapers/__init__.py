"""Scrapers per supermarket. `build_scrapers` wires them to an HTTP client and the settings."""

from preciosdb.config import Settings
from preciosdb.http import HttpClient
from preciosdb.scrapers.base import Page, ScrapedProduct, Scraper
from preciosdb.scrapers.coto import CotoScraper
from preciosdb.scrapers.vtex import VtexScraper

SUPERMARKETS = ("coto", "dia", "carrefour")


def build_scrapers(client: HttpClient, settings: Settings) -> dict[str, Scraper]:
    return {
        "coto": CotoScraper(
            client,
            api_url=settings.coto_constructor_url,
            api_key=settings.coto_constructor_key,
            product_base_url=settings.coto_product_base_url,
        ),
        "dia": VtexScraper(client, code="dia", name="Día", base_url=settings.dia_base_url),
        "carrefour": VtexScraper(
            client, code="carrefour", name="Carrefour", base_url=settings.carrefour_base_url
        ),
    }


__all__ = [
    "SUPERMARKETS",
    "CotoScraper",
    "Page",
    "ScrapedProduct",
    "Scraper",
    "VtexScraper",
    "build_scrapers",
]
