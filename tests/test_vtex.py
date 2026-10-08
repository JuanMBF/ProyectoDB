from decimal import Decimal

from preciosdb.samples import load
from preciosdb.scrapers.vtex import VtexScraper
from tests.conftest import FakeClient

DIA = "https://diaonline.supermercadosdia.com.ar"


def by_ean(products, ean):
    return next(p for p in products if p.ean == ean)


def test_parses_a_regular_product():
    products = VtexScraper(FakeClient(), "dia", "Día", DIA).parse(load("day1", "dia"))
    leche = by_ean(products, "7790742363008")

    assert leche.sku == "295999"
    assert leche.name == "Leche Larga Vida Clásica 3% La Serenisima 1 Lt."
    assert leche.brand == "LA SERENISIMA"
    assert leche.price == leche.regular_price == Decimal("2915.00")
    assert leche.is_available
    assert leche.url.endswith("-295999/p")
    assert leche.image_url.startswith("https://ardiaprod.vteximg.com.br/")
    assert leche.category == "Frescos > Lácteos > Leches"
    assert leche.unit == "un"
    assert leche.unit_quantity == Decimal("1.0")


def test_list_price_above_price_is_a_discount():
    carrefour = VtexScraper(FakeClient(), "carrefour", "Carrefour", "https://www.carrefour.com.ar")
    leche = by_ean(carrefour.parse(load("day1", "carrefour")), "7790742363008")

    assert leche.price == Decimal("1939.00")
    assert leche.regular_price == Decimal("2915.00")


def test_price_zero_means_out_of_stock():
    carrefour = VtexScraper(FakeClient(), "carrefour", "Carrefour", "https://www.carrefour.com.ar")
    aceite = by_ean(carrefour.parse(load("day2", "carrefour")), "7790272001029")

    assert aceite.price is None
    assert aceite.regular_price is None
    assert not aceite.is_available


def test_missing_ean_and_images():
    products = VtexScraper(FakeClient(), "dia", "Día", DIA).parse(load("day1", "dia"))
    own_brand = next(p for p in products if p.brand == "DIA")

    assert own_brand.ean is None
    assert own_brand.image_url is None


def test_default_seller_and_link_fallback():
    payload = [
        {
            "productName": "Yerba",
            "linkText": "yerba-1kg",
            "items": [
                {
                    "itemId": "7",
                    "ean": "7790387120349",
                    "sellers": [
                        {"sellerDefault": False, "commertialOffer": {"Price": 1.0}},
                        {"sellerDefault": True, "commertialOffer": {"Price": 5150, "IsAvailable": True}},
                    ],
                },
                {"name": "item without id is skipped"},
            ],
        },
        {"productName": "No items"},
    ]
    [yerba] = VtexScraper(FakeClient(), "dia", "Día", DIA).parse(payload)

    assert yerba.price == Decimal("5150.00")
    assert yerba.url == f"{DIA}/yerba-1kg/p"
    assert yerba.name == "Yerba"
    assert yerba.category is None


def test_parse_ignores_unexpected_payloads():
    scraper = VtexScraper(FakeClient(), "dia", "Día", DIA)
    assert scraper.parse({"error": "blocked"}) == []
    assert scraper.parse(None) == []


def test_search_paginates_with_from_and_to():
    page1 = [{"productName": f"p{i}", "items": []} for i in range(50)]
    page2 = [{"productName": "last", "items": []}]
    client = FakeClient(page1, page2)

    pages = list(VtexScraper(client, "dia", "Día", DIA).search("leche entera", max_results=120))

    assert [p.page for p in pages] == [1, 2]
    assert client.urls == [
        f"{DIA}/api/catalog_system/pub/products/search?ft=leche%20entera&_from=0&_to=49",
        f"{DIA}/api/catalog_system/pub/products/search?ft=leche%20entera&_from=50&_to=99",
    ]


def test_search_respects_max_results():
    client = FakeClient([{"productName": f"p{i}", "items": []} for i in range(30)])

    list(VtexScraper(client, "dia", "Día", DIA).search("leche", max_results=30))

    assert client.urls == [f"{DIA}/api/catalog_system/pub/products/search?ft=leche&_from=0&_to=29"]


def test_find_by_ean_uses_the_ean_filter():
    client = FakeClient(load("day1", "dia")[:1])

    page = VtexScraper(client, "dia", "Día", DIA).find_by_ean("7790742363008")

    assert client.urls == [
        f"{DIA}/api/catalog_system/pub/products/search?fq=alternateIds_Ean%3A7790742363008"
    ]
    assert page.term == "ean:7790742363008"
    assert page.products[0].ean == "7790742363008"
