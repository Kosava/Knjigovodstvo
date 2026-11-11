from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from conftest import make_invoice
from core import (
    CASH_IN,
    CASH_OUT,
    INVOICE_IN,
    INVOICE_OUT,
    PARTNER_SUPPLIER,
    STATUS_PAID,
    CashEntry,
    Partner,
    ValidationError,
)
from core.validators import make_pib

ISSUE = date(2026, 3, 10)


# --------------------------------------------------------------------------- #
# Partneri
# --------------------------------------------------------------------------- #
def test_duplicate_pib_rejected(partners, customer):
    with pytest.raises(ValidationError, match="već koristi"):
        partners.add(Partner(name="Drugi", pib=customer.pib))


def test_partner_validation_collects_all_errors(partners):
    with pytest.raises(ValidationError) as err:
        partners.add(Partner(name="", pib="123", email="nije-adresa"))
    assert len(err.value.messages) == 3


def test_partner_with_invoices_cannot_be_deleted(partners, invoices, customer):
    invoices.add(make_invoice(customer.id))
    with pytest.raises(ValidationError, match="ne može obrisati"):
        partners.delete(customer.id)


# --------------------------------------------------------------------------- #
# Fakture: osnovno
# --------------------------------------------------------------------------- #
def test_invoice_amounts_are_stored_in_minor_units(db, invoices, customer):
    inv_id = invoices.add(make_invoice(customer.id, price="1234.56", qty="2"))
    row = db.query_one("SELECT net_amount, vat_amount, total_amount FROM invoices WHERE id=?", (inv_id,))
    assert tuple(row) == (246912, 49382, 296294)
    loaded = invoices.get(inv_id)
    assert loaded.total_amount == D("2962.94")
    assert loaded.items[0].unit_price == D("1234.56")


def test_next_number(invoices, customer):
    assert invoices.next_number(INVOICE_OUT, 2026) == "2026/0001"
    invoices.add(make_invoice(customer.id, number="2026/0001"))
    invoices.add(make_invoice(customer.id, number="2026/0007"))
    invoices.add(make_invoice(customer.id, number="2026/RUČNO"))
    assert invoices.next_number(INVOICE_OUT, 2026) == "2026/0008"
    assert invoices.next_number(INVOICE_OUT, 2027) == "2027/0001"


def test_outgoing_number_unique(invoices, customer):
    invoices.add(make_invoice(customer.id, number="2026/0001"))
    with pytest.raises(ValidationError, match="već postoji"):
        invoices.add(make_invoice(customer.id, number="2026/0001"))


def test_incoming_number_unique_per_supplier(partners, invoices, supplier):
    other = Partner(name="Drugi dobavljač", pib=make_pib("10000009"), partner_type=PARTNER_SUPPLIER)
    partners.add(other)
    invoices.add(make_invoice(supplier.id, invoice_type=INVOICE_IN, number="R-1"))
    invoices.add(make_invoice(other.id, invoice_type=INVOICE_IN, number="R-1"))   # drugi dobavljač: OK
    with pytest.raises(ValidationError):
        invoices.add(make_invoice(supplier.id, invoice_type=INVOICE_IN, number="R-1"))


# --------------------------------------------------------------------------- #
# Bag 1: faktura sa iznosom 0
# --------------------------------------------------------------------------- #
def test_zero_total_invoice_is_rejected(invoices, customer):
    with pytest.raises(ValidationError, match="veći od nule"):
        invoices.add(make_invoice(customer.id, price="0"))


def test_zero_total_paid_invoice_gives_validation_error_not_sql_error(invoices, customer):
    with pytest.raises(ValidationError):
        invoices.add(make_invoice(customer.id, price="0", status=STATUS_PAID, paid_date=ISSUE))


def test_set_paid_on_old_zero_invoice(db, invoices, customer):
    inv_id = invoices.add(make_invoice(customer.id))
    db.conn.execute("UPDATE invoices SET total_amount = 0 WHERE id = ?", (inv_id,))  # stari podaci
    with pytest.raises(ValidationError, match="iznosom 0"):
        invoices.set_paid(inv_id, True, ISSUE)


# --------------------------------------------------------------------------- #
# Bag 2: tip partnera mora odgovarati tipu fakture
# --------------------------------------------------------------------------- #
def test_supplier_cannot_be_on_outgoing_invoice(invoices, supplier):
    with pytest.raises(ValidationError, match="mora biti kupac"):
        invoices.add(make_invoice(supplier.id, invoice_type=INVOICE_OUT))


def test_customer_cannot_be_on_incoming_invoice(invoices, customer):
    with pytest.raises(ValidationError, match="mora biti dobavljač"):
        invoices.add(make_invoice(customer.id, invoice_type=INVOICE_IN, number="X-1"))


def test_partner_of_both_types_works_everywhere(invoices, both):
    invoices.add(make_invoice(both.id, invoice_type=INVOICE_OUT))
    invoices.add(make_invoice(both.id, invoice_type=INVOICE_IN, number="X-1"))


def test_changing_type_on_update_is_checked(invoices, customer):
    inv_id = invoices.add(make_invoice(customer.id))
    inv = invoices.get(inv_id)
    inv.invoice_type = INVOICE_IN
    with pytest.raises(ValidationError, match="mora biti dobavljač"):
        invoices.update(inv)


def test_old_invoice_editable_after_partner_type_change(partners, invoices, customer):
    inv_id = invoices.add(make_invoice(customer.id))
    customer.partner_type = PARTNER_SUPPLIER
    partners.update(customer)
    inv = invoices.get(inv_id)
    inv.note = "ispravka"
    invoices.update(inv)                     # partner i tip nisu menjani -> dozvoljeno
    assert invoices.get(inv_id).note == "ispravka"


# --------------------------------------------------------------------------- #
# Bag 4: datum plaćanja
# --------------------------------------------------------------------------- #
def test_paid_date_before_issue_date_rejected_on_add(invoices, customer):
    with pytest.raises(ValidationError, match="Datum plaćanja"):
        invoices.add(make_invoice(customer.id, status=STATUS_PAID, paid_date=ISSUE - timedelta(days=1)))


def test_paid_date_before_issue_date_rejected_on_set_paid(invoices, customer):
    inv_id = invoices.add(make_invoice(customer.id))
    with pytest.raises(ValidationError, match="Datum plaćanja"):
        invoices.set_paid(inv_id, True, ISSUE - timedelta(days=1))


def test_paid_without_date_defaults_to_today_or_issue(invoices, customer):
    future = date.today() + timedelta(days=30)
    inv_id = invoices.add(make_invoice(customer.id, issue=future, status=STATUS_PAID))
    assert invoices.get(inv_id).paid_date == future


# --------------------------------------------------------------------------- #
# Plaćanje <-> blagajna (uključujući bag 5)
# --------------------------------------------------------------------------- #
def test_set_paid_creates_and_removes_cash_entry(invoices, cash, customer):
    inv_id = invoices.add(make_invoice(customer.id, price="1000"))
    invoices.set_paid(inv_id, True, ISSUE + timedelta(days=5))
    [entry] = cash.list()
    assert (entry.entry_type, entry.amount, entry.invoice_id) == (CASH_IN, D("1200.00"), inv_id)
    assert entry.entry_date == ISSUE + timedelta(days=5)
    invoices.set_paid(inv_id, False)
    assert cash.list() == []


def test_paying_incoming_invoice_is_cash_out(invoices, cash, supplier):
    inv_id = invoices.add(make_invoice(supplier.id, invoice_type=INVOICE_IN, number="R-9"))
    invoices.set_paid(inv_id, True, ISSUE)
    assert cash.list()[0].entry_type == CASH_OUT
    assert cash.balance() == D("-1200.00")


def test_marking_paid_twice_keeps_one_entry(invoices, cash, customer):
    inv_id = invoices.add(make_invoice(customer.id))
    invoices.set_paid(inv_id, True, ISSUE)
    invoices.set_paid(inv_id, True, ISSUE + timedelta(days=1))
    assert len(cash.list()) == 1


def test_paid_invoice_is_locked(invoices, cash, customer):
    inv_id = invoices.add(make_invoice(customer.id, number="2026/0001"))
    invoices.set_paid(inv_id, True, ISSUE)
    inv = invoices.get(inv_id)
    inv.items[0].unit_price = D("2000")
    with pytest.raises(ValidationError, match="plaćena"):
        invoices.update(inv)
    with pytest.raises(ValidationError, match="plaćena"):
        invoices.delete(inv_id)
    assert cash.list()[0].amount == D("1200.00")


def test_editing_unpaid_then_paying_uses_new_number(invoices, cash, customer):
    inv_id = invoices.add(make_invoice(customer.id, number="2026/0001"))
    inv = invoices.get(inv_id)
    inv.number = "2026/0099"
    invoices.update(inv)
    invoices.set_paid(inv_id, True, ISSUE)
    assert cash.list()[0].description == "Naplata fakture 2026/0099"


def test_unpay_then_delete_removes_cash_entry(invoices, cash, customer):
    inv_id = invoices.add(make_invoice(customer.id, status=STATUS_PAID, paid_date=ISSUE))
    assert len(cash.list()) == 1
    invoices.set_paid(inv_id, False)
    invoices.delete(inv_id)
    assert cash.list() == [] and invoices.get(inv_id) is None


def test_cash_balance(cash):
    cash.add(CashEntry(entry_date=ISSUE, entry_type=CASH_IN, description="Polog", amount=D("500")))
    cash.add(CashEntry(entry_date=ISSUE, entry_type=CASH_OUT, description="Gorivo", amount=D("120.50")))
    assert cash.balance() == D("379.50")


def test_cash_entry_requires_positive_amount(cash):
    with pytest.raises(ValidationError):
        cash.add(CashEntry(entry_date=ISSUE, description="Nula", amount=D("0")))
