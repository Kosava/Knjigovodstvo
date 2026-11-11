from datetime import date, datetime
from decimal import Decimal as D

import pytest

from core import tax


def test_calc_line_rounds_half_up():
    # 3 x 0,335 = 1,005 -> 1,01 (sa float bi bilo 1,00)
    a = tax.calc_line(3, "0.335", 20)
    assert a == tax.Amounts(D("1.01"), D("0.20"), D("1.21"))


def test_calc_line_vat_rates():
    assert tax.calc_line(2, 1500, 20) == (D("3000.00"), D("600.00"), D("3600.00"))
    assert tax.calc_line(1, 999.99, 10) == (D("999.99"), D("100.00"), D("1099.99"))
    assert tax.calc_line(5, 100, 0) == (D("500.00"), D("0.00"), D("500.00"))


def test_sum_and_breakdown():
    lines = [(D("20"), tax.calc_line(1, 100, 20)), (D("10"), tax.calc_line(1, 50, 10)),
             (D("20"), tax.calc_line(2, 10, 20))]
    total = tax.sum_amounts(a for _, a in lines)
    assert total == (D("170.00"), D("29.00"), D("199.00"))
    breakdown = tax.vat_breakdown(lines)
    assert list(breakdown) == [D("20"), D("10")]          # veća stopa prva
    assert breakdown[D("20")] == (D("120.00"), D("24.00"), D("144.00"))


@pytest.mark.parametrize("value", ["0", "0.01", "1234.56", "-17.50", "999999999.99"])
def test_minor_roundtrip(value):
    assert tax.from_minor(tax.to_minor(value)) == D(value).quantize(tax.CENT)


def test_to_decimal_is_safe():
    assert tax.to_decimal(None) == tax.ZERO
    assert tax.to_decimal("abc", D("7")) == D("7")
    assert tax.to_decimal(0.1) == D("0.1")               # preko str(), bez binarne greške


def test_format_money():
    assert tax.format_money(D("1234567.8")) == "1.234.567,80"
    assert tax.format_money(D("-1234.5")) == "-1.234,50"
    assert tax.format_money(0) == "0,00"


def test_format_quantity():
    assert tax.format_quantity(D("2.000")) == "2"
    assert tax.format_quantity(D("2.50")) == "2,5"
    assert tax.format_quantity(D("0")) == "0"


@pytest.mark.parametrize("text, expected", [
    ("1.234,56", D("1234.56")), ("1234.56", D("1234.56")), (" 12 000,5 ", D("12000.5")),
])
def test_parse_money(text, expected):
    assert tax.parse_money(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "1,2,3"])
def test_parse_money_rejects(text):
    with pytest.raises(ValueError):
        tax.parse_money(text)


def test_format_date():
    assert tax.format_date(date(2026, 3, 5)) == "05.03.2026."
    assert tax.format_date("2026-03-05T10:00:00") == "05.03.2026."
    assert tax.format_date(datetime(2026, 12, 31, 23, 59)) == "31.12.2026."
    assert tax.format_date(None) == ""
