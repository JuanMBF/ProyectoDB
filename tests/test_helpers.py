from decimal import Decimal

import pytest

from preciosdb.scrapers.base import (
    build_url,
    clean_text,
    comparable_ean,
    gtin_check_digit_ok,
    parse_money,
    plausible_regular_price,
    to_decimal,
    to_money,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$2186.25", "2186.25"),
        ("Precio Contado: $2915", "2915.00"),
        ("$ 1.898,97", "1898.97"),
        ("$1898,5", "1898.50"),
        ("$13.197", "13197.00"),
        ("$8999.00", "8999.00"),
        (4649, "4649.00"),
        ("sin precio", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_money(text, expected):
    assert parse_money(text) == (Decimal(expected) if expected else None)


def test_to_money_rounds_to_cents_and_rejects_garbage():
    assert to_money(1939.0) == Decimal("1939.00")
    assert to_money("3486.755") == Decimal("3486.76")
    assert to_money(None) is None
    assert to_money(True) is None
    assert to_money("abc") is None
    assert to_money(float("nan")) is None


def test_to_decimal():
    assert to_decimal(1.5) == Decimal("1.5")
    assert to_decimal("x") is None
    assert to_decimal(None) is None


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("7790742363008", "7790742363008"),  # Leche La Serenísima 1 L
        ("7790387120349", "7790387120349"),  # Yerba Taragüi 1 kg
        (7790895000997, "7790895000997"),  # Coto sends EANs as numbers
        ("012345678905", "0012345678905"),  # UPC-A -> EAN-13
        ("96385074", "96385074"),  # EAN-8
        ("7790742363009", None),  # wrong check digit
        ("2000199000001", None),  # store-only code (weighed cheese at Coto)
        ("0212345678909", None),  # restricted prefix 02 (valid check digit)
        ("12345", None),
        ("9638507²", None),  # str.isdigit() accepts superscripts, but int() does not
        ("７７９０７４２３６３００８", None),  # fullwidth digits: not a barcode a scanner reads
        ("", None),
        (None, None),
    ],
)
def test_comparable_ean(code, expected):
    assert comparable_ean(code) == expected


def test_gtin_check_digit():
    assert gtin_check_digit_ok("7793940054006")
    assert not gtin_check_digit_ok("7793940054007")


def test_plausible_regular_price():
    price = Decimal("1939.00")
    assert plausible_regular_price(price, [Decimal("2915.00"), price]) == Decimal("2915.00")
    # More than 3x the price is a unit error in the source, not a discount.
    assert plausible_regular_price(price, [Decimal("9999.00")]) == price
    # Below the price makes no sense either.
    assert plausible_regular_price(price, [Decimal("100.00"), None]) == price
    assert plausible_regular_price(None, [Decimal("1.00")]) is None


def test_build_url_encodes_spaces_as_percent_20():
    assert (
        build_url("https://x/api", {"ft": "leche entera", "_from": 0})
        == "https://x/api?ft=leche%20entera&_from=0"
    )


def test_clean_text():
    assert clean_text("  Leche   Entera ") == "Leche Entera"
    assert clean_text("   ") is None
    assert clean_text(None) is None
