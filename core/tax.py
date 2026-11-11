"""Novac, PDV i formatiranje iznosa.

Svi iznosi se računaju sa ``Decimal`` (nikad sa ``float``) i zaokružuju na dve
decimale pravilom "half up", kako je uobičajeno u knjigovodstvu. U bazi se
iznosi čuvaju kao celi brojevi (pare), pa su ovde i pomoćne konverzije.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, NamedTuple

import i18n

ZERO = Decimal("0")
CENT = Decimal("0.01")

#: PDV stope u Srbiji: opšta 20 %, posebna 10 %, bez PDV-a 0 %.
VAT_RATES: tuple[Decimal, ...] = (Decimal("20"), Decimal("10"), Decimal("0"))
DEFAULT_VAT_RATE = Decimal("20")


class Amounts(NamedTuple):
    """Osnovica, PDV i ukupan iznos jedne stavke ili cele fakture."""

    net: Decimal
    vat: Decimal
    total: Decimal


EMPTY_AMOUNTS = Amounts(ZERO, ZERO, ZERO)


def to_decimal(value: object, default: Decimal = ZERO) -> Decimal:
    """Bezbedno pretvara bilo šta u ``Decimal`` (greška daje ``default``)."""
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        # str(float) izbegava binarne nepreciznosti tipa 0.1 + 0.2
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return default


def round_money(value: object) -> Decimal:
    """Zaokružuje na dve decimale (half up)."""
    return to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def calc_line(quantity: object, unit_price: object, vat_rate: object) -> Amounts:
    """Računa osnovicu, PDV i ukupno za jednu stavku fakture."""
    net = round_money(to_decimal(quantity) * to_decimal(unit_price))
    vat = round_money(net * to_decimal(vat_rate) / Decimal("100"))
    return Amounts(net, vat, net + vat)


def sum_amounts(items: Iterable[Amounts]) -> Amounts:
    """Sabira više iznosa (osnovica, PDV i ukupno zasebno)."""
    net = vat = total = ZERO
    for a in items:
        net += a.net
        vat += a.vat
        total += a.total
    return Amounts(net, vat, total)


def vat_breakdown(lines: Iterable[tuple[Decimal, Amounts]]) -> "OrderedDict[Decimal, Amounts]":
    """Rekapitulacija PDV-a po stopama; ``lines`` su parovi (stopa, iznosi)."""
    buckets: dict[Decimal, list[Amounts]] = {}
    for rate, amounts in lines:
        buckets.setdefault(rate, []).append(amounts)
    return OrderedDict(
        (rate, sum_amounts(buckets[rate])) for rate in sorted(buckets, reverse=True)
    )


# --------------------------------------------------------------------------- #
# Konverzija za bazu (pare kao celi broj)
# --------------------------------------------------------------------------- #
def to_minor(value: object) -> int:
    """Iznos u dinarima -> celi broj para."""
    return int(round_money(value) * 100)


def from_minor(value: int | None) -> Decimal:
    """Celi broj para -> iznos u dinarima."""
    return (Decimal(int(value or 0)) / 100).quantize(CENT)


# --------------------------------------------------------------------------- #
# Formatiranje i parsiranje (zavisi od jezika: sr 1.234,56 · en 1,234.56 · fr 1 234,56)
# --------------------------------------------------------------------------- #
def format_money(value: object, decimals: int = 2) -> str:
    """Formatira iznos po trenutnom jeziku (srpski: ``1.234,56``)."""
    lang = i18n.current()
    number = to_decimal(value)
    text = f"{number:,.{decimals}f}"
    return (text.replace(",", "\x00").replace(".", lang.decimal)
            .replace("\x00", lang.thousands))


def format_quantity(value: object) -> str:
    """Količina bez suvišnih nula (``2`` ili ``2,5``)."""
    number = to_decimal(value)
    text = f"{number:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", i18n.current().decimal) or "0"


def parse_money(text: str) -> Decimal:
    """Parsira iznos u zapisu trenutnog jezika (ili ``1234.56``). Baca ``ValueError``."""
    cleaned = (text or "").strip()
    for space in (" ", "\u00a0", "\u202f"):
        cleaned = cleaned.replace(space, "")
    if not cleaned:
        raise ValueError("prazan iznos")
    if i18n.current().decimal == ",":
        if "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"neispravan iznos: {text!r}") from exc


def format_date(value: date | datetime | str | None) -> str:
    """Datum u zapisu trenutnog jezika (srpski: ``dd.mm.gggg.``)."""
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime(i18n.current().date_py)
