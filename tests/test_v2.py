"""Gotovina/račun, gotovina bez minusa, datum prometa, zaključavanje i storno, migracija."""

import sqlite3
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from conftest import make_invoice
from core import (
    CASH_IN,
    CASH_OUT,
    INVOICE_IN,
    PAY_BANK,
    PAY_CASH,
    STATUS_CANCELLED,
    STATUS_PAID,
    CashEntry,
    DatabaseManager,
    InvoiceRepository,
    ValidationError,
)
from core.database import SCHEMA_VERSION
from core.stats import load_dashboard_stats

ISSUE = date(2026, 3, 10)


def cash_in(cash, amount, day=ISSUE, method=PAY_CASH):
    return cash.add(CashEntry(entry_date=day, entry_type=CASH_IN, description="Polog",
                              amount=D(amount), method=method))


def cash_out(cash, amount, day=ISSUE, method=PAY_CASH):
    return cash.add(CashEntry(entry_date=day, entry_type=CASH_OUT, description="Isplata",
                              amount=D(amount), method=method))


# --------------------------------------------------------------------------- #
# Gotovina i račun
# --------------------------------------------------------------------------- #
def test_payment_method_is_recorded(invoices, cash, customer):
    a = invoices.add(make_invoice(customer.id, number="2026/0001"))
    b = invoices.add(make_invoice(customer.id, number="2026/0002"))
    invoices.set_paid(a, True, ISSUE)                       # podrazumevano: račun
    invoices.set_paid(b, True, ISSUE, method=PAY_CASH)
    methods = {e.invoice_id: e.method for e in cash.list()}
    assert methods == {a: PAY_BANK, b: PAY_CASH}
    assert cash.balance(PAY_CASH) == D("1200.00")
    assert cash.balance(PAY_BANK) == D("1200.00")
    assert cash.balance() == D("2400.00")


def test_new_paid_invoice_uses_chosen_method(invoices, cash, customer):
    inv = make_invoice(customer.id, status=STATUS_PAID, paid_date=ISSUE)
    inv.payment_method = PAY_CASH
    invoices.add(inv)
    assert cash.list()[0].method == PAY_CASH


def test_dashboard_separates_cash_and_bank(db, invoices, cash, customer):
    cash_in(cash, "500")
    inv = invoices.add(make_invoice(customer.id))
    invoices.set_paid(inv, True, ISSUE)
    s = load_dashboard_stats(db, today=ISSUE)
    assert (s.cash_balance, s.bank_balance) == (D("500.00"), D("1200.00"))


# --------------------------------------------------------------------------- #
# Gotovina ne sme u minus
# --------------------------------------------------------------------------- #
def test_cash_payout_without_cash_is_rejected(cash):
    cash_in(cash, "100")
    with pytest.raises(ValidationError, match="Gotovine u blagajni nema dovoljno"):
        cash_out(cash, "150")
    assert cash.balance(PAY_CASH) == D("100.00")             # ništa nije upisano


def test_bank_payout_is_not_limited(cash):
    cash_out(cash, "150", method=PAY_BANK)                   # račun može u minus (dozvoljeni minus)
    assert cash.balance(PAY_BANK) == D("-150.00")


def test_cash_is_checked_chronologically(cash):
    cash_in(cash, "100", day=ISSUE + timedelta(days=5))
    with pytest.raises(ValidationError):
        cash_out(cash, "50", day=ISSUE)                      # pre nego što je novac uplaćen
    cash_out(cash, "50", day=ISSUE + timedelta(days=5))     # istog dana posle uplate: OK


def test_deleting_deposit_that_covers_payout_is_rejected(cash):
    deposit = cash_in(cash, "100")
    cash_out(cash, "80", day=ISSUE + timedelta(days=1))
    with pytest.raises(ValidationError):
        cash.delete(deposit)


def test_paying_supplier_in_cash_needs_cash(invoices, cash, supplier):
    inv = invoices.add(make_invoice(supplier.id, invoice_type=INVOICE_IN, number="R-1"))
    with pytest.raises(ValidationError, match="nema dovoljno"):
        invoices.set_paid(inv, True, ISSUE, method=PAY_CASH)
    assert invoices.get(inv).status != STATUS_PAID
    invoices.set_paid(inv, True, ISSUE, method=PAY_BANK)     # preko računa prolazi


def test_old_negative_cash_does_not_block_unrelated_changes(db, cash):
    # Stari podaci (pre ove provere) mogu već imati minus u gotovini.
    db.conn.execute("INSERT INTO cash_entries (entry_date, entry_type, description, amount, method)"
                    " VALUES (?, ?, 'stara isplata', 10000, ?)", (ISSUE.isoformat(), CASH_OUT, PAY_CASH))
    db.conn.commit()
    cash_in(cash, "50", day=ISSUE + timedelta(days=3))       # popravlja stanje: dozvoljeno
    with pytest.raises(ValidationError):
        cash_out(cash, "500", day=ISSUE + timedelta(days=4))  # dodatno pogoršava: odbijeno


# --------------------------------------------------------------------------- #
# Datum prometa
# --------------------------------------------------------------------------- #
def test_supply_date_defaults_to_issue_date(invoices, customer):
    inv_id = invoices.add(make_invoice(customer.id))
    assert invoices.get(inv_id).supply_date == ISSUE


def test_supply_date_roundtrip_and_update(invoices, customer):
    inv = make_invoice(customer.id)
    inv.supply_date = ISSUE - timedelta(days=4)
    inv_id = invoices.add(inv)
    loaded = invoices.get(inv_id)
    assert loaded.supply_date == ISSUE - timedelta(days=4)
    loaded.supply_date = ISSUE - timedelta(days=1)
    invoices.update(loaded)
    assert invoices.get(inv_id).supply_date == ISSUE - timedelta(days=1)


# --------------------------------------------------------------------------- #
# Brisanje bez rupa u numeraciji
# --------------------------------------------------------------------------- #
def test_only_last_outgoing_invoice_can_be_deleted(invoices, customer):
    first = invoices.add(make_invoice(customer.id, number="2026/0001"))
    last = invoices.add(make_invoice(customer.id, number="2026/0002"))
    with pytest.raises(ValidationError, match="rupa"):
        invoices.delete(first)
    invoices.delete(last)
    invoices.delete(first)                                   # sada je ona poslednja
    assert invoices.list() == []


def test_incoming_invoice_can_be_deleted(invoices, supplier):
    inv = invoices.add(make_invoice(supplier.id, invoice_type=INVOICE_IN, number="R-1"))
    invoices.delete(inv)
    assert invoices.get(inv) is None


# --------------------------------------------------------------------------- #
# Storno
# --------------------------------------------------------------------------- #
def test_storno_creates_negative_document(db, invoices, customer):
    orig = invoices.add(make_invoice(customer.id, number="2026/0001", price="1000"))
    invoices.add(make_invoice(customer.id, number="2026/0002", price="500"))
    doc_id = invoices.storno(orig, ISSUE + timedelta(days=2))

    original, doc = invoices.get(orig), invoices.get(doc_id)
    assert original.status == STATUS_CANCELLED and doc.status == STATUS_CANCELLED
    assert doc.number == "2026/0003" and doc.storno_of == orig
    assert doc.storno_of_number == "2026/0001"
    assert (doc.net_amount, doc.vat_amount, doc.total_amount) == (D("-1000.00"), D("-200.00"), D("-1200.00"))
    assert doc.items[0].quantity == D("-1")
    assert invoices.storno_document(orig).id == doc_id

    s = load_dashboard_stats(db, today=ISSUE + timedelta(days=30))
    assert s.receivables == D("600.00")                      # samo druga faktura
    assert s.overdue_count == 1
    assert s.months[-2].revenue == D("500.00")               # mart: 1000 - 1000 (storno) + 500


def test_storno_documents_are_locked(invoices, customer):
    orig = invoices.add(make_invoice(customer.id, number="2026/0001"))
    doc_id = invoices.storno(orig, ISSUE)
    for inv_id in (orig, doc_id):
        inv = invoices.get(inv_id)
        with pytest.raises(ValidationError):
            invoices.update(inv)
        with pytest.raises(ValidationError):
            invoices.delete(inv_id)
        with pytest.raises(ValidationError):
            invoices.set_paid(inv_id, True, ISSUE)
        with pytest.raises(ValidationError):
            invoices.storno(inv_id)


def test_paid_invoice_must_be_unpaid_before_storno(invoices, customer):
    inv = invoices.add(make_invoice(customer.id, status=STATUS_PAID, paid_date=ISSUE))
    with pytest.raises(ValidationError, match="vratite na neplaćeno"):
        invoices.storno(inv)


def test_incoming_invoices_are_not_stornoed(invoices, supplier):
    inv = invoices.add(make_invoice(supplier.id, invoice_type=INVOICE_IN, number="R-1"))
    with pytest.raises(ValidationError, match="izlazne"):
        invoices.storno(inv)


def test_cancelled_status_cannot_be_set_by_hand(invoices, customer):
    with pytest.raises(ValidationError, match="Storniraj"):
        invoices.add(make_invoice(customer.id, status=STATUS_CANCELLED))


# --------------------------------------------------------------------------- #
# Migracija baze sa verzije 1
# --------------------------------------------------------------------------- #
V1_SCHEMA = """
CREATE TABLE partners (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, pib TEXT NOT NULL UNIQUE,
  address TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '', account TEXT NOT NULL DEFAULT '',
  partner_type TEXT NOT NULL DEFAULT 'Kupac', email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE invoices (id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_type TEXT NOT NULL CHECK (invoice_type IN ('Izlazna', 'Ulazna')), number TEXT NOT NULL,
  issue_date TEXT NOT NULL, due_date TEXT, partner_id INTEGER NOT NULL REFERENCES partners(id) ON DELETE RESTRICT,
  net_amount INTEGER NOT NULL DEFAULT 0, vat_amount INTEGER NOT NULL DEFAULT 0, total_amount INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'Neplaćeno' CHECK (status IN ('Plaćeno', 'Neplaćeno')), paid_date TEXT,
  note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE invoice_items (id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE, position INTEGER NOT NULL DEFAULT 0,
  description TEXT NOT NULL, quantity TEXT NOT NULL DEFAULT '1', unit TEXT NOT NULL DEFAULT 'kom',
  unit_price TEXT NOT NULL DEFAULT '0', vat_rate TEXT NOT NULL DEFAULT '20', net_amount INTEGER NOT NULL DEFAULT 0,
  vat_amount INTEGER NOT NULL DEFAULT 0, total_amount INTEGER NOT NULL DEFAULT 0);
CREATE TABLE cash_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, entry_date TEXT NOT NULL,
  entry_type TEXT NOT NULL CHECK (entry_type IN ('Uplata', 'Isplata')), description TEXT NOT NULL,
  amount INTEGER NOT NULL CHECK (amount > 0), partner_id INTEGER REFERENCES partners(id) ON DELETE SET NULL,
  invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')));
INSERT INTO partners (name, pib) VALUES ('Stari kupac', '100000016');
INSERT INTO invoices (invoice_type, number, issue_date, partner_id, net_amount, vat_amount, total_amount, status, paid_date)
  VALUES ('Izlazna', '2025/0001', '2025-06-01', 1, 100000, 20000, 120000, 'Plaćeno', '2025-06-05');
INSERT INTO invoice_items (invoice_id, description, quantity, unit_price, net_amount, vat_amount, total_amount)
  VALUES (1, 'Usluga', '1', '1000', 100000, 20000, 120000);
INSERT INTO cash_entries (entry_date, entry_type, description, amount, invoice_id)
  VALUES ('2025-06-05', 'Uplata', 'Naplata fakture 2025/0001', 120000, 1);
INSERT INTO cash_entries (entry_date, entry_type, description, amount) VALUES ('2025-06-06', 'Uplata', 'Polog', 5000);
PRAGMA user_version = 1;
"""


def test_migration_from_v1(tmp_path):
    path = tmp_path / "stara.db"
    old = sqlite3.connect(path)
    old.executescript(V1_SCHEMA)
    old.close()

    db = DatabaseManager(path)
    db.open()
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert (tmp_path / "stara.pre-v2.db").exists()            # rezervna kopija

    repo = InvoiceRepository(db)
    inv = repo.get(1)
    assert inv.supply_date == date(2025, 6, 1) and inv.items[0].description == "Usluga"
    from core import ENTITY_LEGAL, PartnerRepository
    assert PartnerRepository(db).get(1).entity_type == ENTITY_LEGAL     # postojeći partneri
    methods = dict(db.conn.execute("SELECT description, method FROM cash_entries").fetchall())
    assert methods == {"Naplata fakture 2025/0001": PAY_BANK, "Polog": PAY_CASH}
    # Nova pravila rade nad migriranom bazom
    repo.set_paid(1, False)
    doc = repo.storno(1, date(2025, 7, 1))
    assert repo.get(doc).number == "2025/0002"
    db.close()
    db.open()                                                    # ponovno otvaranje bez migracije
    db.close()


# --------------------------------------------------------------------------- #
# Štampa
# --------------------------------------------------------------------------- #
def test_printed_invoice_shows_supply_date_and_storno(invoices, partners, customer):
    from core.printing import render_invoice_html
    inv = make_invoice(customer.id, number="2026/0001")
    inv.supply_date = date(2026, 3, 7)
    orig = invoices.add(inv)
    html = render_invoice_html(invoices.get(orig), partners.get(customer.id), {"name": "Moja firma"})
    assert "Datum prometa: <b>07.03.2026.</b>" in html and "<h1" in html and ">FAKTURA<" in html
    doc = invoices.storno(orig, ISSUE)
    html = render_invoice_html(invoices.get(doc), partners.get(customer.id), {"name": "Moja firma"})
    assert "STORNO FAKTURA" in html and "Stornira fakturu br. <b>2026/0001</b>" in html
    assert "-1.200,00" in html
