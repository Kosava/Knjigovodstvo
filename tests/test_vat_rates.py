"""Stope PDV-a po datumu: istorija, nova stopa iz podešavanja, artikli po vrsti stope."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from core import (
    VAT_GENERAL,
    VAT_NONE,
    VAT_REDUCED,
    Article,
    ArticleRepository,
    DatabaseManager,
    ValidationError,
    VatRate,
    VatRateRepository,
)
from core.database import SCHEMA_VERSION

D = Decimal


@pytest.fixture
def rates(db):
    return VatRateRepository(db)


def test_initial_serbian_rates(rates):
    assert rates.rates_on(date(2026, 3, 1)) == {VAT_GENERAL: D("20"), VAT_REDUCED: D("10"), VAT_NONE: D("0")}
    assert rates.rate_on(VAT_GENERAL, date(2012, 9, 30)) == D("18")
    assert rates.rate_on(VAT_GENERAL, date(2012, 10, 1)) == D("20")
    assert rates.rate_on(VAT_REDUCED, date(2013, 12, 31)) == D("8")
    assert rates.rate_on(VAT_REDUCED, date(2014, 1, 1)) == D("10")
    assert rates.rate_on(VAT_GENERAL, date(1999, 1, 1)) == D("18")      # pre prve promene: najstarija


def test_new_rate_applies_from_its_date(rates):
    rates.add(VatRate(category=VAT_GENERAL, rate=D("22"), valid_from=date(2027, 1, 1)))
    assert rates.rate_on(VAT_GENERAL, date(2026, 12, 31)) == D("20")
    assert rates.rate_on(VAT_GENERAL, date(2027, 1, 1)) == D("22")
    assert rates.category_of(D("22"), date(2027, 2, 1)) == VAT_GENERAL
    assert rates.category_of(D("20"), date(2027, 2, 1)) is None
    assert rates.list()[0].rate == D("22")                               # najnovija prva
    with pytest.raises(ValidationError, match="već postoji"):
        rates.add(VatRate(category=VAT_GENERAL, rate=D("23"), valid_from=date(2027, 1, 1)))


def test_decimal_rate_is_stored_exactly(rates):
    rates.add(VatRate(category=VAT_REDUCED, rate=D("9.50"), valid_from=date(2028, 1, 1)))
    assert rates.rate_on(VAT_REDUCED, date(2028, 6, 1)) == D("9.5")


def test_rate_validation(rates):
    for bad in (VatRate(category=VAT_NONE, rate=D("5")), VatRate(category=VAT_GENERAL, rate=D("0")),
                VatRate(category=VAT_GENERAL, rate=D("100")), VatRate(category=VAT_GENERAL, rate=D("20.123")),
                VatRate(category="Nepostojeća", rate=D("20"))):
        with pytest.raises(ValidationError):
            rates.add(bad)


def test_last_rate_of_a_kind_cannot_be_deleted(rates):
    zero = [r for r in rates.list() if r.category == VAT_NONE]
    assert len(zero) == 1
    with pytest.raises(ValidationError, match="bar jednu"):
        rates.delete(zero[0].id)
    general = [r for r in rates.list() if r.category == VAT_GENERAL]
    rates.delete(general[0].id)                                          # 20 % od 2012. obrisana
    assert rates.rate_on(VAT_GENERAL, date(2026, 1, 1)) == D("18")


def test_article_follows_current_rate(db, rates):
    articles = ArticleRepository(db)
    a = Article(name="Usluga", price=D("1000"), vat_category=VAT_GENERAL)
    articles.add(a)
    assert articles.get(a.id).vat_rate == D("20")
    assert a.to_item(vat_rate=D("22")).amounts.total == D("1220.00")
    rates.add(VatRate(category=VAT_GENERAL, rate=D("22"), valid_from=date(2000, 1, 1).replace(year=date.today().year)))
    got = articles.get(a.id)
    assert got.vat_category == VAT_GENERAL and got.vat_rate == D("22") and got.price_with_vat == D("1220.00")


def test_existing_invoices_keep_their_rate(db, rates, invoices, customer):
    from conftest import make_invoice
    inv_id = invoices.add(make_invoice(customer.id, number="2026/0001", vat="20"))
    rates.add(VatRate(category=VAT_GENERAL, rate=D("22"), valid_from=date(2026, 1, 1)))
    assert invoices.get(inv_id).items[0].vat_rate == D("20")
    assert invoices.get(inv_id).total_amount == D("1200.00")


def test_migration_v4_to_v5_maps_article_rates(tmp_path):
    path = tmp_path / "v4.db"
    db = DatabaseManager(path)
    db.open()
    db.close()
    con = sqlite3.connect(path)
    con.executescript("""
        PRAGMA foreign_keys = OFF;
        DROP TABLE articles; DROP TABLE vat_rates;
        CREATE TABLE articles (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL DEFAULT '',
          name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'Usluga', unit TEXT NOT NULL DEFAULT 'kom',
          price TEXT NOT NULL DEFAULT '0', vat_rate TEXT NOT NULL DEFAULT '20',
          active INTEGER NOT NULL DEFAULT 1, note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO articles (name, price, vat_rate) VALUES ('Opšti', '100', '20'), ('Knjiga', '50', '10'),
          ('Izvoz', '70', '0'), ('Čudna', '10', '18.00');
        PRAGMA user_version = 4;
    """)
    con.close()
    db.open()
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 5
    assert (tmp_path / "v4.pre-v5.db").exists()
    got = {a.name: (a.vat_category, a.vat_rate) for a in ArticleRepository(db).list()}
    assert got == {"Opšti": (VAT_GENERAL, D("20")), "Knjiga": (VAT_REDUCED, D("10")),
                   "Izvoz": (VAT_NONE, D("0")), "Čudna": (VAT_GENERAL, D("20"))}
    assert len(VatRateRepository(db).list()) == 5                         # početne stope
    db.close()
