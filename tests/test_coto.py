from decimal import Decimal

import pytest

from preciosdb.samples import load
from preciosdb.scrapers.coto import CotoScraper, coto_prices
from tests.conftest import FakeClient

API = "https://ac.cnstrc.com"
PRODUCT_BASE = "https://www.cotodigital.com.ar/sitios/cdigi/productos"


def scraper(client=None) -> CotoScraper:
    return CotoScraper(client or FakeClient(), API, "key_test", PRODUCT_BASE)


def by_sku(products, sku):
    return next(p for p in products if p.sku == sku)


def test_parses_a_product():
    leche = by_sku(scraper().parse(load("day1", "coto")), "251875")

    assert leche.name == "Leche Larga Vida Entera Ua Clasica 3% LA SERENISIMA 1l"
    assert leche.brand == "LA SERENISIMA"
    assert leche.ean == "7790742363008"
    assert leche.url == f"{PRODUCT_BASE}/_/R-00251875-00251875-200"
    assert leche.image_url.endswith("/00251875.jpg")
    assert leche.category == "Frescos > Lácteos > Leches"
    assert leche.unit == "Litro"
    assert leche.unit_quantity == Decimal("1")
    assert leche.is_available


def test_conditional_promo_is_kept_as_text_and_not_applied():
    leche = by_sku(scraper().parse(load("day1", "coto")), "251875")

    assert leche.price == leche.regular_price == Decimal("2915.00")
    assert leche.promo_text == "50% 2da (llevando 2)"


def test_direct_percentage_discount_lowers_the_price():
    manteca = by_sku(scraper().parse(load("day1", "coto")), "310221")

    assert manteca.price == Decimal("3486.75")
    assert manteca.regular_price == Decimal("4649.00")
    assert manteca.promo_text is None


def test_regular_price_from_precio_contado_text():
    queso = by_sku(scraper().parse(load("day1", "coto")), "578534")

    assert queso.price == Decimal("10990.00")
    assert queso.regular_price == Decimal("13990.00")
    assert queso.ean == "2000199000001"  # kept as published; not comparable across chains


@pytest.mark.parametrize(
    ("discounts", "price", "regular", "promo"),
    [
        ([], "1000", "1000", None),
        (None, "1000", "1000", None),
        ([{"discountText": "25%Dto", "discountPrice": "$750.00"}], "750", "1000", None),
        ([{"discountText": "30 % dto.", "discountPrice": "$700"}], "700", "1000", None),
        # A "discount" price above the list price is ignored.
        ([{"discountText": "25%Dto", "discountPrice": "$1200"}], "1000", "1000", None),
        ([{"discountText": "2x1", "takingText": "Llevando 2"}], "1000", "1000", "2x1 (llevando 2)"),
        ([{"discountText": "3x2"}, {"discountText": "+10%"}], "1000", "1000", "3x2 · +10%"),
        # A regular price more than 3x the price is a format error, not a discount.
        ([{"discountText": None, "regularPriceText": "Precio Contado: $99999"}], "1000", "1000", None),
    ],
)
def test_coto_prices(discounts, price, regular, promo):
    assert coto_prices(Decimal("1000"), discounts) == (Decimal(price), Decimal(regular), promo)


def test_without_list_price_there_is_no_price():
    assert coto_prices(None, []) == (None, None, None)
    assert coto_prices(Decimal("0"), []) == (None, None, None)


def test_without_stock_in_any_branch_is_unavailable():
    payload = {
        "response": {
            "results": [
                {
                    "value": "Sin stock",
                    "data": {"sku_plu": 1, "product_list_price": 100, "store_availability": []},
                },
                {"value": "Sin PLU", "data": {"product_list_price": 100}},
            ]
        }
    }
    [product] = scraper().parse(payload)

    assert product.name == "Sin stock"
    assert not product.is_available
    assert product.url is None
    assert product.category is None


def test_parse_ignores_unexpected_payloads():
    assert scraper().parse([]) == []
    assert scraper().parse({"response": {}}) == []


def test_search_paginates_until_a_short_page():
    full = {"response": {"results": [{"data": {"sku_plu": i}} for i in range(50)], "total_num_results": 60}}
    short = {"response": {"results": [{"data": {"sku_plu": i}} for i in range(10)], "total_num_results": 60}}
    client = FakeClient(full, short)

    pages = list(scraper(client).search("dulce de leche", max_results=200))

    assert [len(p.products) for p in pages] == [50, 10]
    assert pages[0].total == 60
    assert client.urls[0] == (
        f"{API}/search/dulce%20de%20leche?key=key_test&section=Products&page=1&num_results_per_page=50"
    )
    assert "page=2" in client.urls[1]


def test_search_stops_at_max_results():
    full = {"response": {"results": [{"data": {"sku_plu": i}} for i in range(20)]}}
    client = FakeClient(full)

    [page] = scraper(client).search("leche", max_results=20)

    assert len(page.products) == 20
    assert len(client.urls) == 1


def test_find_by_ean_searches_the_code():
    client = FakeClient(load("day1", "coto"))

    page = scraper(client).find_by_ean("7790742363008")

    assert client.urls[0].startswith(f"{API}/search/7790742363008?")
    assert page.term == "ean:7790742363008"
