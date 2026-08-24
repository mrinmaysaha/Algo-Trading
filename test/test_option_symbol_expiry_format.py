import pytest
from services.option_symbol_service import (
    normalize_expiry_date,
    construct_option_symbol,
    parse_underlying_symbol,
)


def test_normalize_expiry_date_formats():
    # 4-digit year DDMMMYYYY
    no_h, fmt = normalize_expiry_date("27AUG2026")
    assert no_h == "27AUG26"
    assert fmt == "27-AUG-26"

    # 2-digit year DDMMMYY
    no_h, fmt = normalize_expiry_date("27AUG26")
    assert no_h == "27AUG26"
    assert fmt == "27-AUG-26"

    # Hyphenated 4-digit year DD-MMM-YYYY
    no_h, fmt = normalize_expiry_date("27-AUG-2026")
    assert no_h == "27AUG26"
    assert fmt == "27-AUG-26"

    # Hyphenated 2-digit year DD-MMM-YY
    no_h, fmt = normalize_expiry_date("27-AUG-26")
    assert no_h == "27AUG26"
    assert fmt == "27-AUG-26"

    # Standard ISO date YYYY-MM-DD
    no_h, fmt = normalize_expiry_date("2026-08-27")
    assert no_h == "27AUG26"
    assert fmt == "27-AUG-26"


def test_construct_option_symbol_with_4digit_year():
    symbol = construct_option_symbol("MIDCPNIFTY", "27AUG2026", 15000, "CE")
    assert symbol == "MIDCPNIFTY27AUG2615000CE"


def test_parse_underlying_symbol_with_4digit_year():
    base, expiry = parse_underlying_symbol("MIDCPNIFTY27AUG2026FUT")
    assert base == "MIDCPNIFTY"
    assert expiry == "27AUG26"
