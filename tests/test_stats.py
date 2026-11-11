from datetime import date
from decimal import Decimal as D

from conftest import make_invoice
from core import INVOICE_IN, STATUS_PAID
from core.demo_data import seed_demo_data
from core.stats import load_dashboard_stats

TODAY = date(2026, 3, 20)


def test_dashboard_totals(db, invoices, customer, supplier):
    invoices.add(make_invoice(customer.id, number="2026/0001", price="1000"))                  # 1200
    invoices.add(make_invoice(customer.id, number="2026/0002", price="500",
                              status=STATUS_PAID, paid_date=date(2026, 3, 11)))              # 600, plaćena
    invoices.add(make_invoice(supplier.id, invoice_type=INVOICE_IN, number="R-1", price="300",
                              vat="10"))                                                     # 330

    s = load_dashboard_stats(db, today=TODAY)
    assert s.receivables == D("1200.00")
    assert s.payables == D("330.00")
    assert s.cash_balance == D("0.00")          # naplata je išla preko računa
    assert s.bank_balance == D("600.00")
    assert s.vat_liability == D("200.00") + D("100.00") - D("30.00")
    assert s.unpaid_count == 2
    assert s.overdue_count == 2                # rok = datum fakture (10.3.) < 20.3.
    assert [m.label for m in s.months] == ["10/25", "11/25", "12/25", "01/26", "02/26", "03/26"]
    assert s.months[-1].revenue == D("1500.00") and s.months[-1].expense == D("300.00")


def test_demo_data_loads_once(db):
    assert seed_demo_data(db) is True
    assert seed_demo_data(db) is False
    s = load_dashboard_stats(db)
    assert s.receivables > 0 and len(s.recent_invoices) == 8
