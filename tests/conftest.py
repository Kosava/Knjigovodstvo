"""Zajednički fixture-i: svaka proba dobija svoju praznu bazu u privremenom folderu."""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

# Koren projekta na putanju, da `import core` radi i kad se pokrene samo `pytest`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import (
    INVOICE_IN,
    INVOICE_OUT,
    PARTNER_BOTH,
    PARTNER_CUSTOMER,
    PARTNER_SUPPLIER,
    STATUS_UNPAID,
    CashRepository,
    DatabaseManager,
    Invoice,
    InvoiceItem,
    InvoiceRepository,
    Partner,
    PartnerRepository,
)
from core.validators import make_pib


@pytest.fixture(autouse=True)
def serbian_language():
    """Svaki test počinje na srpskom (podrazumevani jezik), bez obzira na prethodni test."""
    import i18n
    i18n.set_language("sr")
    yield
    i18n.set_language("sr")


@pytest.fixture
def db(tmp_path):
    database = DatabaseManager(tmp_path / "test.db")
    database.open()
    yield database
    database.close()


@pytest.fixture
def partners(db):
    return PartnerRepository(db)


@pytest.fixture
def invoices(db):
    return InvoiceRepository(db)


@pytest.fixture
def cash(db):
    return CashRepository(db)


@pytest.fixture
def customer(partners):
    p = Partner(name="Kupac d.o.o.", pib=make_pib("10000001"), partner_type=PARTNER_CUSTOMER)
    partners.add(p)
    return p


@pytest.fixture
def supplier(partners):
    p = Partner(name="Dobavljač d.o.o.", pib=make_pib("10000002"), partner_type=PARTNER_SUPPLIER)
    partners.add(p)
    return p


@pytest.fixture
def both(partners):
    p = Partner(name="Oba d.o.o.", pib=make_pib("10000003"), partner_type=PARTNER_BOTH)
    partners.add(p)
    return p


def make_invoice(partner_id: int, *, invoice_type: str = INVOICE_OUT, number: str = "2026/0001",
                 price: str = "1000", qty: str = "1", vat: str = "20",
                 issue: date = date(2026, 3, 10), status: str = STATUS_UNPAID,
                 paid_date: date | None = None) -> Invoice:
    return Invoice(
        invoice_type=invoice_type, number=number, issue_date=issue, due_date=issue,
        partner_id=partner_id, status=status, paid_date=paid_date,
        items=[InvoiceItem("Usluga", Decimal(qty), "kom", Decimal(price), Decimal(vat))],
    )


__all__ = ["make_invoice", "INVOICE_IN", "INVOICE_OUT"]
