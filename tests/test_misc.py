"""Vezani unosi u blagajni, vraćanje rezervne kopije, štampa bez internog statusa."""

import sqlite3
from datetime import date

import pytest

from conftest import make_invoice
from core import (
    DatabaseError,
    DatabaseManager,
    Partner,
    PartnerRepository,
    STATUS_PAID,
    ValidationError,
)
from core.printing import render_invoice_html
from core.validators import make_pib

ISSUE = date(2026, 3, 10)


def test_linked_cash_entry_cannot_be_changed_directly(invoices, cash, customer):
    inv = invoices.add(make_invoice(customer.id, status=STATUS_PAID, paid_date=ISSUE))
    entry = cash.list()[0]
    entry.amount = entry.amount + 1
    with pytest.raises(ValidationError, match="plaćene fakture 2026/0001"):
        cash.update(entry)
    with pytest.raises(ValidationError, match="plaćene fakture"):
        cash.delete(entry.id)
    invoices.set_paid(inv, False)                 # ispravan put: preko fakture
    assert cash.list() == []


def test_outgoing_print_has_no_status_incoming_has(invoices, partners, customer, supplier):
    out = invoices.get(invoices.add(make_invoice(customer.id)))
    assert "Status:" not in render_invoice_html(out, partners.get(customer.id), {})
    inc = invoices.get(invoices.add(make_invoice(supplier.id, invoice_type="Ulazna", number="R-1")))
    assert "Status:" in render_invoice_html(inc, partners.get(supplier.id), {})


def test_restore_replaces_data_and_keeps_safety_copy(tmp_path, db, partners, customer):
    snapshot = db.backup(tmp_path / "kopija.db")                     # stanje sa jednim partnerom
    partners.add(Partner(name="Novi posle kopije", pib=make_pib("10000077")))
    assert partners.count() == 2

    safety = db.restore(snapshot)
    assert partners.count() == 1 and partners.list()[0].name == customer.name
    assert safety.exists()
    old = DatabaseManager(safety)
    old.open()
    assert PartnerRepository(old).count() == 2                       # prethodno stanje sačuvano
    old.close()


def test_restore_rejects_wrong_files(tmp_path, db):
    not_db = tmp_path / "tekst.db"
    not_db.write_text("ovo nije baza", encoding="utf-8")
    with pytest.raises(DatabaseError, match="nije SQLite baza"):
        db.restore(not_db)
    other = tmp_path / "druga.db"
    sqlite3.connect(other).execute("CREATE TABLE nesto (x)").connection.close()
    with pytest.raises(DatabaseError, match="nije baza ovog programa"):
        db.restore(other)
    with pytest.raises(DatabaseError, match="ne postoji"):
        db.restore(tmp_path / "nema.db")
    with pytest.raises(DatabaseError, match="trenutne baze"):
        db.restore(db.path)


def test_restore_of_older_backup_is_upgraded(tmp_path, db, partners):
    from test_v2 import V1_SCHEMA
    old = tmp_path / "v1.db"
    sqlite3.connect(old).executescript(V1_SCHEMA)
    db.restore(old)
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] >= 3
    assert partners.list()[0].name == "Stari kupac"
