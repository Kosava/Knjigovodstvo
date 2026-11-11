"""Šifarnik artikala, predračuni i avansni računi."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from conftest import make_invoice
from core import (
    ARTICLE_GOODS,
    CASH_IN,
    DOC_ADVANCE,
    DOC_INVOICE,
    DOC_PROFORMA,
    INVOICE_IN,
    INVOICE_OUT,
    PAY_BANK,
    STATUS_CANCELLED,
    STATUS_OPEN,
    STATUS_PAID,
    STATUS_REALIZED,
    STATUS_UNPAID,
    Article,
    ArticleRepository,
    DatabaseManager,
    Invoice,
    InvoiceItem,
    InvoiceRepository,
    ValidationError,
)
from core.database import SCHEMA_VERSION
from core.printing import render_invoice_html
from core.stats import load_dashboard_stats

D = Decimal
DAY = date(2026, 3, 10)


# --------------------------------------------------------------------------- #
# Šifarnik
# --------------------------------------------------------------------------- #
@pytest.fixture
def articles(db):
    return ArticleRepository(db)


def test_article_roundtrip_and_item(articles):
    a = Article(code="WEB-1", name="Održavanje sajta", unit="mj", price=D("85000"))
    articles.add(a)
    got = articles.get(a.id)
    assert (got.code, got.name, got.unit, got.price, got.vat_rate) == ("WEB-1", "Održavanje sajta", "mj",
                                                                       D("85000.00"), D("20"))
    assert got.price_with_vat == D("102000.00")
    item = got.to_item(D("2"))
    assert (item.description, item.quantity, item.amounts.total) == ("Održavanje sajta", D("2"), D("204000.00"))
    assert articles.find_by_name("održavanje SAJTA").id == a.id


def test_article_code_and_name_are_unique(articles):
    articles.add(Article(code="A1", name="Papir"))
    with pytest.raises(ValidationError, match="Šifru A1"):
        articles.add(Article(code="A1", name="Toner"))
    with pytest.raises(ValidationError, match="već postoji"):
        articles.add(Article(name="papir"))
    articles.add(Article(name="Toner"))          # bez šifre: dozvoljeno više artikala
    articles.add(Article(name="Olovka"))


def test_article_validation_and_inactive(articles):
    with pytest.raises(ValidationError) as err:
        Article(name="", price=D("-1"), vat_category="nepoznata", unit="").validate()
    assert len(err.value.messages) == 4
    a = Article(name="Stari artikal", kind=ARTICLE_GOODS, active=False)
    articles.add(a)
    assert [x.name for x in articles.list(active_only=True)] == []
    a.active = True
    articles.update(a)
    assert [x.name for x in articles.list(active_only=True)] == ["Stari artikal"]
    articles.delete(a.id)
    assert articles.count() == 0


# --------------------------------------------------------------------------- #
# Predračun
# --------------------------------------------------------------------------- #
def proforma(customer_id: int, price: str = "1000", number: str = "PR-2026/0001", **kw) -> Invoice:
    inv = make_invoice(customer_id, number=number, price=price, **kw)
    inv.doc_kind = DOC_PROFORMA
    return inv


def test_numbering_series_are_separate(invoices, customer):
    assert invoices.next_number(INVOICE_OUT, 2026, DOC_PROFORMA) == "PR-2026/0001"
    invoices.add(make_invoice(customer.id, number="2026/0001"))
    invoices.add(proforma(customer.id))
    assert invoices.next_number(INVOICE_OUT, 2026) == "2026/0002"
    assert invoices.next_number(INVOICE_OUT, 2026, DOC_PROFORMA) == "PR-2026/0002"
    assert invoices.next_number(INVOICE_OUT, 2026, DOC_ADVANCE) == "AV-2026/0001"


def test_proforma_is_open_and_not_payable(invoices, customer, cash):
    pid = invoices.add(proforma(customer.id))
    p = invoices.get(pid)
    assert p.status == STATUS_OPEN and p.is_proforma and not p.is_overdue(date(2030, 1, 1))
    with pytest.raises(ValidationError, match="Predračun se ne plaća"):
        invoices.set_paid(pid, True)
    with pytest.raises(ValidationError, match="ne stornira"):
        invoices.storno(pid)
    assert cash.list() == []
    # Ne utiče na potraživanja, PDV ni prihod
    stats = load_dashboard_stats(invoices.db, today=DAY)
    assert stats.receivables == 0 and stats.vat_liability == 0 and stats.unpaid_count == 0
    assert sum(m.revenue for m in stats.months) == 0


def test_only_outgoing_proforma(invoices, supplier):
    inv = proforma(supplier.id, invoice_type=INVOICE_IN)
    with pytest.raises(ValidationError, match="samo izlazni"):
        invoices.add(inv)


def test_invoice_from_proforma_realizes_it(invoices, customer):
    pid = invoices.add(proforma(customer.id, price="500", qty="3"))
    draft = invoices.draft_from_proforma(pid, DOC_INVOICE, on=DAY)
    assert draft.source_id == pid and draft.total_amount == D("1800.00") and draft.items[0].quantity == 3
    draft.number = invoices.next_number(INVOICE_OUT, 2026)
    fid = invoices.add(draft)
    p = invoices.get(pid)
    assert p.status == STATUS_REALIZED and p.is_locked
    assert invoices.get(fid).source_number == "PR-2026/0001"
    assert invoices.can_delete(p)                         # realizovan predračun se ne briše
    with pytest.raises(ValidationError):
        invoices.update(invoices.get(pid))
    with pytest.raises(ValidationError, match="već izdata"):
        invoices.draft_from_proforma(pid)
    # Brisanje fakture vraća predračun na "Otvoren"
    invoices.delete(fid)
    assert invoices.get(pid).status == STATUS_OPEN
    invoices.delete(pid)                                  # otvoren predračun se briše bez storna
    assert invoices.get(pid) is None


def test_storno_of_invoice_reopens_proforma(invoices, customer):
    pid = invoices.add(proforma(customer.id))
    draft = invoices.draft_from_proforma(pid, on=DAY)
    draft.number = "2026/0001"
    fid = invoices.add(draft)
    invoices.storno(fid, DAY)
    assert invoices.get(pid).status == STATUS_OPEN


# --------------------------------------------------------------------------- #
# Avansni račun
# --------------------------------------------------------------------------- #
def advance(invoices, customer_id, gross: str, pid: int | None = None, number: str = "AV-2026/0001",
            on: date = DAY) -> int:
    if pid is not None:
        draft = invoices.draft_from_proforma(pid, DOC_ADVANCE, gross=D(gross), on=on)
    else:
        net = (D(gross) / D("1.2")).quantize(D("0.01"))
        draft = Invoice(doc_kind=DOC_ADVANCE, issue_date=on, partner_id=customer_id,
                        status=STATUS_PAID, paid_date=on,
                        items=[InvoiceItem("Avans", D("1"), "usluga", net, D("20"))])
    draft.number = number
    return invoices.add(draft)


def test_advance_from_proforma_splits_by_vat_rate(invoices, customer, cash):
    p = proforma(customer.id)
    p.items = [InvoiceItem("Roba", D("1"), "kom", D("1000"), D("20")),      # 1.200
               InvoiceItem("Knjiga", D("1"), "kom", D("1000"), D("10"))]    # 1.100
    pid = invoices.add(p)
    aid = advance(invoices, customer.id, "1150", pid)                       # pola predračuna
    adv = invoices.get(aid)
    assert adv.is_advance and adv.is_paid and adv.source_id == pid and adv.total_amount == D("1150.00")
    rates = {i.vat_rate: i.amounts for i in adv.items}
    assert rates[D("20")].total == D("600.00") and rates[D("10")].total == D("550.00")
    assert invoices.get(pid).status == STATUS_OPEN          # avans ne realizuje predračun
    entry = cash.list()[0]
    assert entry.amount == D("1150.00") and entry.entry_type == CASH_IN and entry.invoice_id == aid
    assert "AV-2026/0001" in entry.description
    with pytest.raises(ValidationError, match="veći od iznosa predračuna"):
        invoices.draft_from_proforma(pid, DOC_ADVANCE, gross=D("5000"))


def test_final_invoice_deducts_advance(db, invoices, customer, cash):
    pid = invoices.add(proforma(customer.id, price="10000"))               # 12.000 sa PDV-om
    aid = advance(invoices, customer.id, "6000", pid)                       # avans 5.000 + 1.000
    draft = invoices.draft_from_proforma(pid, DOC_INVOICE, on=date(2026, 3, 20))
    assert [a.advance_id for a in draft.advances] == [aid]                  # avans po predračunu
    draft.number = "2026/0001"
    fid = invoices.add(draft)
    inv = invoices.get(fid)
    assert (inv.advance_net, inv.advance_vat, inv.advance_total) == (D("5000.00"), D("1000.00"), D("6000.00"))
    assert inv.amount_due == D("6000.00")
    assert invoices.advance_used_in(aid) == "2026/0001"
    assert invoices.available_advances(customer.id) == []
    # Naplata konačne fakture upisuje samo ostatak
    invoices.set_paid(fid, True, date(2026, 3, 25), PAY_BANK)
    assert sorted(c.amount for c in cash.list()) == [D("6000.00"), D("6000.00")]
    # Potraživanje i PDV: avans nosi 1.000 PDV-a, faktura još 1.000 (2.000 - 1.000)
    stats = load_dashboard_stats(db, today=date(2026, 3, 31))
    assert stats.vat_liability == D("2000.00") and stats.receivables == 0
    assert stats.months[-1].revenue == D("10000.00")                        # avans nije prihod
    # Iskorišćen avans se ne vraća na neplaćeno i ne briše
    with pytest.raises(ValidationError, match="odbijen od fakture"):
        invoices.set_paid(aid, False)
    assert invoices.can_delete(invoices.get(aid))


def test_unpaid_final_invoice_receivable_is_net_of_advance(db, invoices, customer):
    aid = advance(invoices, customer.id, "1200")
    inv = make_invoice(customer.id, number="2026/0001", price="5000")
    inv.advances = invoices.available_advances(customer.id)
    invoices.add(inv)
    stats = load_dashboard_stats(db, today=DAY)
    assert stats.receivables == D("4800.00")                               # 6.000 - 1.200
    assert invoices.get(aid).is_paid


def test_advance_rules(invoices, customer, both, partners):
    aid = advance(invoices, customer.id, "1200")
    # Avans ne sme biti veći od fakture po stopi PDV-a
    small = make_invoice(customer.id, number="2026/0001", price="500")
    small.advances = invoices.available_advances(customer.id)
    with pytest.raises(ValidationError, match="veći od osnovice"):
        invoices.add(small)
    # Druga stopa PDV-a: nema osnovice po stopi avansa
    other = make_invoice(customer.id, number="2026/0001", price="5000", vat="10")
    other.advances = invoices.available_advances(customer.id)
    with pytest.raises(ValidationError, match="veći od osnovice"):
        invoices.add(other)
    # Tuđi avans
    foreign = make_invoice(both.id, number="2026/0001", price="5000")
    foreign.advances = invoices.available_advances(customer.id)
    with pytest.raises(ValidationError, match="nije moguće odbiti"):
        invoices.add(foreign)
    # Faktura pre avansa
    early = make_invoice(customer.id, number="2026/0001", price="5000", issue=date(2026, 3, 1))
    early.advances = invoices.available_advances(customer.id)
    with pytest.raises(ValidationError, match="posle datuma fakture"):
        invoices.add(early)
    # Neplaćen avans se ne nudi
    invoices.set_paid(aid, False)
    assert invoices.available_advances(customer.id) == []


def test_advance_covering_everything_needs_no_payment_entry(invoices, customer, cash):
    advance(invoices, customer.id, "1200")
    inv = make_invoice(customer.id, number="2026/0001", price="1000")
    inv.advances = invoices.available_advances(customer.id)
    fid = invoices.add(inv)
    assert invoices.get(fid).amount_due == 0
    invoices.set_paid(fid, True, DAY)
    assert len(cash.list()) == 1                                           # samo avans


def test_editing_invoice_can_remove_advance(invoices, customer):
    aid = advance(invoices, customer.id, "1200")
    inv = make_invoice(customer.id, number="2026/0001", price="5000")
    inv.advances = invoices.available_advances(customer.id)
    fid = invoices.add(inv)
    edited = invoices.get(fid)
    assert [a.advance_id for a in invoices.available_advances(customer.id, fid)] == [aid]
    edited.advances = []
    invoices.update(edited)
    assert invoices.get(fid).advance_total == 0 and invoices.advance_used_in(aid) is None


def test_storno_of_final_invoice_releases_advance(db, invoices, customer):
    aid = advance(invoices, customer.id, "1200")
    inv = make_invoice(customer.id, number="2026/0001", price="5000")
    inv.advances = invoices.available_advances(customer.id)
    fid = invoices.add(inv)
    doc = invoices.get(invoices.storno(fid, DAY))
    assert doc.advance_total == D("-1200.00") and doc.number == "2026/0002"
    assert invoices.get(fid).status == STATUS_CANCELLED
    assert [a.advance_id for a in invoices.available_advances(customer.id)] == [aid]
    stats = load_dashboard_stats(db, today=DAY)
    assert stats.vat_liability == D("200.00")                              # ostaje samo PDV avansa


def test_storno_of_advance_uses_advance_series(invoices, customer):
    aid = advance(invoices, customer.id, "1200")
    invoices.set_paid(aid, False)
    doc = invoices.get(invoices.storno(aid, DAY))
    assert doc.number == "AV-2026/0002" and doc.doc_kind == DOC_ADVANCE and doc.total_amount == D("-1200.00")


def test_only_last_advance_can_be_deleted(invoices, customer):
    a1 = advance(invoices, customer.id, "1200", number="AV-2026/0001")
    advance(invoices, customer.id, "1200", number="AV-2026/0002")
    invoices.set_paid(a1, False)
    assert "nije poslednja" in invoices.can_delete(invoices.get(a1))


# --------------------------------------------------------------------------- #
# Štampa
# --------------------------------------------------------------------------- #
def test_printing_of_documents(invoices, partners, customer):
    company = {"name": "Moja firma"}
    pid = invoices.add(proforma(customer.id, price="10000"))
    html = render_invoice_html(invoices.get(pid), partners.get(customer.id), company)
    assert ">PREDRAČUN<" in html and "nije poreska isprava" in html and "Status" not in html
    aid = advance(invoices, customer.id, "6000", pid)
    html = render_invoice_html(invoices.get(aid), partners.get(customer.id), company)
    assert ">AVANSNI RAČUN<" in html and "PR-2026/0001" in html and "Datum uplate" in html
    draft = invoices.draft_from_proforma(pid, on=date(2026, 3, 20))
    draft.number = "2026/0001"
    fid = invoices.add(draft)
    html = render_invoice_html(invoices.get(fid), partners.get(customer.id), company)
    assert "Umanjenje za avans AV-2026/0001" in html and "-6.000,00" in html
    assert "ZA UPLATU" in html and "6.000,00 RSD" in html


# --------------------------------------------------------------------------- #
# Migracija 3 -> 4
# --------------------------------------------------------------------------- #
def test_migration_v3_to_v4(tmp_path, partners):
    path = tmp_path / "v3.db"
    db = DatabaseManager(path)
    db.open()
    from core import Partner, PartnerRepository
    from core.validators import make_pib
    pid = PartnerRepository(db).add(Partner(name="Kupac", pib=make_pib("10000001")))
    InvoiceRepository(db).add(make_invoice(pid, number="2025/0007"))
    db.close()
    # Tabela faktura u obliku iz verzije 3 (bez vrste dokumenta i avansa)
    con = sqlite3.connect(path)
    con.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE i3 (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_type TEXT NOT NULL,
          number TEXT NOT NULL, issue_date TEXT NOT NULL, supply_date TEXT, due_date TEXT,
          partner_id INTEGER NOT NULL REFERENCES partners(id), net_amount INTEGER NOT NULL DEFAULT 0,
          vat_amount INTEGER NOT NULL DEFAULT 0, total_amount INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'Neplaćeno', paid_date TEXT,
          storno_of INTEGER REFERENCES invoices(id), note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO i3 SELECT id, invoice_type, number, issue_date, supply_date, due_date, partner_id,
          net_amount, vat_amount, total_amount, status, paid_date, storno_of, note, created_at FROM invoices;
        DROP TABLE invoices; ALTER TABLE i3 RENAME TO invoices;
        DROP TABLE invoice_advances; DROP TABLE articles;
        PRAGMA user_version = 3;
    """)
    con.close()

    db.open()
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert (tmp_path / "v3.pre-v4.db").exists()
    repo = InvoiceRepository(db)
    inv = repo.get(1)
    assert inv.doc_kind == DOC_INVOICE and inv.number == "2025/0007" and inv.items[0].description == "Usluga"
    assert inv.status == STATUS_UNPAID and inv.advance_total == 0
    assert ArticleRepository(db).count() == 0
    repo.add(proforma(pid))
    db.close()
