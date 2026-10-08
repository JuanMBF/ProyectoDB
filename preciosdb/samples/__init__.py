"""Offline sample data: one catalog per supermarket and day, with the exact shape of each API response.

The structure (fields, nesting, types) mirrors the real responses of Constructor.io (Coto) and VTEX
(Día, Carrefour). Prices and most SKUs are illustrative. Used by `python -m preciosdb demo` and by the
tests, so the whole pipeline runs without network access.
"""

from __future__ import annotations

import json
import unicodedata
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from preciosdb.http import JsonResponse

SAMPLES_DIR = Path(__file__).resolve().parent
DAYS = ("day1", "day2")
DEMO_TERMS = ("leche", "manteca", "yerba", "aceite", "coca", "queso")


@cache
def load(day: str, supermarket: str) -> Any:
    return json.loads((SAMPLES_DIR / day / f"{supermarket}.json").read_text(encoding="utf-8"))


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _matches(name: str, term: str) -> bool:
    words = _normalize(name).replace("-", " ").split()
    return all(any(w.startswith(t) for w in words) for t in _normalize(term).split())


class SampleClient:
    """HttpClient that answers search requests from the sample catalogs, filtering by the search text."""

    def __init__(self, day: str = "day1") -> None:
        if day not in DAYS:
            raise ValueError(f"day must be one of {DAYS}")
        self.day = day
        self.requests: list[str] = []

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> JsonResponse:
        self.requests.append(url)
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if "cnstrc" in parts.netloc:
            data = self._coto(unquote(parts.path.rsplit("/", 1)[-1]), int(query.get("page", 1)))
        elif "carrefour" in parts.netloc:
            data = self._vtex("carrefour", query)
        elif "supermercadosdia" in parts.netloc:
            data = self._vtex("dia", query)
        else:
            raise ValueError(f"No sample data for {url}")
        return JsonResponse(url=url, status=200, data=data)

    def _coto(self, term: str, page: int) -> dict[str, Any]:
        results = load(self.day, "coto")["response"]["results"]
        if term.isdigit():
            found = [r for r in results if str(r["data"].get("product_main_ean")) == term]
        else:
            found = [r for r in results if _matches(r["data"]["sku_display_name"], term)]
        return {"response": {"results": found if page == 1 else [], "total_num_results": len(found)}}

    def _vtex(self, supermarket: str, query: dict[str, str]) -> list[dict[str, Any]]:
        products = load(self.day, supermarket)
        if "fq" in query:
            ean = query["fq"].removeprefix("alternateIds_Ean:")
            return [p for p in products if any(i.get("ean") == ean for i in p["items"])]
        if int(query.get("_from", 0)) > 0:
            return []
        return [p for p in products if _matches(p["productName"], query.get("ft", ""))]
