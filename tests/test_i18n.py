"""Prevodi: nijedan tekst ne fali, zamene {..} se poklapaju, srpski je na ekavici."""

import importlib
import re
import string
from decimal import Decimal
from datetime import date

import pytest

import i18n
from core import Partner, ValidationError
from core import tax
from i18n.extract import dynamic_tr_calls, extract

KEYS = extract()
OTHER_LANGUAGES = [code for code in i18n.LANGUAGES if code != i18n.DEFAULT_LANGUAGE]


def _fields(text: str) -> list[str]:
    return sorted(f for _, f, _, _ in string.Formatter().parse(text) if f)


@pytest.mark.parametrize("code", OTHER_LANGUAGES)
def test_every_text_is_translated(code):
    table = importlib.import_module(f"i18n.{code}").TRANSLATIONS
    missing = sorted(k for k in KEYS if k not in table)
    unused = sorted(k for k in table if k not in KEYS)
    assert not missing, f"{code}: nedostaju prevodi za {missing[:5]}"
    assert not unused, f"{code}: višak prevoda {unused[:5]}"


@pytest.mark.parametrize("code", OTHER_LANGUAGES)
def test_placeholders_and_mnemonics_match(code):
    table = importlib.import_module(f"i18n.{code}").TRANSLATIONS
    for key, value in table.items():
        assert _fields(key) == _fields(value), f"{code}: {{...}} se ne poklapa u {key!r}"
        assert ("&" in key) == ("&" in value), f"{code}: znak & (prečica u meniju) u {value!r}"
        assert value.strip(), f"{code}: prazan prevod za {key!r}"


def test_tr_is_only_called_with_marked_constants():
    # tr(promenljiva) je dozvoljen samo za vrednosti koje su negde označene sa tr_noop/Column;
    # spisak mesta služi kao podsetnik pri pregledu koda.
    assert dynamic_tr_calls()                    # postoje (konstante iz baze, naslovi kolona...)


# Ijekavski oblici koji ne smeju da se pojave u srpskom tekstu programa
IJEKAVICA = re.compile(r"\b(prije|poslije|umjesto|vrijed|vrijeme|uvijek|podrazumijev|mijenj|izmijen|"
                       r"promijen|zamijen|izmjen|promjen|primjen|provjer|posljed|sljede|mjesec|mjest|"
                       r"cijel|cijen|dospjel|dospijev|smije|uspjel|riječ|gdje|ovdje|obje|dvije|svijetl|"
                       r"bijel|lijep|djel|dio\b|tijek|htio|vidio)", re.IGNORECASE)


def test_serbian_texts_are_ekavian():
    bad = {k: re.findall(IJEKAVICA, k) for k in KEYS if IJEKAVICA.search(k)}
    assert not bad, f"ijekavica u tekstu programa: {bad}"


def test_unknown_language_falls_back_to_serbian():
    assert i18n.set_language("xx") == "sr"
    assert i18n.tr("Sačuvaj") == "Sačuvaj"


def test_translation_and_formats_follow_language():
    i18n.set_language("en")
    assert i18n.tr("Sačuvaj") == "Save"
    assert i18n.tr("Dokument {number} je sačuvan.", number="2026/0001") == "Document 2026/0001 saved."
    assert tax.format_money(Decimal("1234567.5")) == "1,234,567.50"
    assert tax.format_date(date(2026, 3, 5)) == "05/03/2026"
    assert tax.parse_money("1,234.56") == Decimal("1234.56")
    i18n.set_language("hu")
    assert tax.format_money(Decimal("1234.5")) == "1 234,50"
    assert tax.format_date(date(2026, 3, 5)) == "2026.03.05."
    i18n.set_language("de")
    assert tax.format_money(Decimal("1234.5")) == "1.234,50"
    assert tax.format_quantity(Decimal("2.50")) == "2,5"


def test_core_messages_are_translated():
    i18n.set_language("de")
    with pytest.raises(ValidationError) as err:
        Partner(name="").validate()
    assert "Der Partnername ist erforderlich." in err.value.messages
    i18n.set_language("sr")
    with pytest.raises(ValidationError) as err:
        Partner(name="").validate()
    assert "Naziv partnera je obavezan." in err.value.messages


def test_bad_translation_does_not_crash(monkeypatch):
    i18n.set_language("en")
    monkeypatch.setitem(i18n._table, "Dokument {number} je sačuvan.", "Document {broj} saved.")
    assert i18n.tr("Dokument {number} je sačuvan.", number="1") == "Dokument 1 je sačuvan."
