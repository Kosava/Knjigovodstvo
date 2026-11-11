"""Zbirni podaci za Dashboard (potraživanja, obaveze, PDV, blagajna, grafikon)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import tax
from .database import DatabaseManager
from .models import (CASH_IN, DOC_INVOICE, DOC_PROFORMA, INVOICE_IN, INVOICE_OUT, PAY_BANK, PAY_CASH,
                     STATUS_UNPAID, Invoice)
from .repositories import InvoiceRepository


@dataclass
class MonthPoint:
    label: str            # npr. "10/26"
    revenue: Decimal      # osnovica izlaznih faktura
    expense: Decimal      # osnovica ulaznih faktura


@dataclass
class DashboardStats:
    receivables: Decimal = tax.ZERO     # neplaćene izlazne fakture (umanjene za avanse)
    payables: Decimal = tax.ZERO        # neplaćene ulazne fakture
    cash_balance: Decimal = tax.ZERO    # gotovina u blagajni
    bank_balance: Decimal = tax.ZERO    # promet preko tekućeg računa (uplate - isplate)
    vat_liability: Decimal = tax.ZERO   # izlazni PDV - ulazni PDV u tekućem mesecu
    unpaid_count: int = 0
    overdue_count: int = 0
    months: list[MonthPoint] = field(default_factory=list)
    recent_invoices: list[Invoice] = field(default_factory=list)
    overdue_invoices: list[Invoice] = field(default_factory=list)


def _month_start(year: int, month: int, offset: int) -> tuple[int, int]:
    """Pomera (godina, mesec) za ``offset`` meseci."""
    index = year * 12 + (month - 1) + offset
    return index // 12, index % 12 + 1


def load_dashboard_stats(db: DatabaseManager, today: date | None = None, months: int = 6) -> DashboardStats:
    today = today or date.today()
    stats = DashboardStats()

    def unpaid_sum(invoice_type: str) -> Decimal:
        return tax.from_minor(db.scalar(
            "SELECT SUM(total_amount - advance_total) FROM invoices WHERE invoice_type=? AND status=?",
            (invoice_type, STATUS_UNPAID), default=0))

    stats.receivables = unpaid_sum(INVOICE_OUT)
    stats.payables = unpaid_sum(INVOICE_IN)
    stats.unpaid_count = int(db.scalar(
        "SELECT COUNT(*) FROM invoices WHERE status=?", (STATUS_UNPAID,), default=0))
    stats.overdue_count = int(db.scalar(
        "SELECT COUNT(*) FROM invoices WHERE status=? AND due_date IS NOT NULL AND due_date < ?",
        (STATUS_UNPAID, today.isoformat()), default=0))

    def balance(method: str) -> Decimal:
        return tax.from_minor(db.scalar(
            "SELECT SUM(CASE entry_type WHEN ? THEN amount ELSE -amount END) FROM cash_entries"
            " WHERE method = ?", (CASH_IN, method), default=0))

    stats.cash_balance = balance(PAY_CASH)
    stats.bank_balance = balance(PAY_BANK)

    # PDV tekućeg meseca (po datumu fakture). Avansni račun nosi PDV na avans, a konačna
    # faktura samo razliku (PDV umanjen za avans); predračun nije poreska isprava.
    month_key = today.strftime("%Y-%m")
    vat_rows = db.query(
        "SELECT invoice_type, SUM(vat_amount - advance_vat) AS vat FROM invoices"
        " WHERE strftime('%Y-%m', issue_date) = ? AND doc_kind <> ? GROUP BY invoice_type",
        (month_key, DOC_PROFORMA))
    vat = {r["invoice_type"]: tax.from_minor(r["vat"]) for r in vat_rows}
    stats.vat_liability = vat.get(INVOICE_OUT, tax.ZERO) - vat.get(INVOICE_IN, tax.ZERO)

    # Prihodi/rashodi po mesecima (poslednjih ``months`` meseci). Prihod je osnovica faktura:
    # avans nije prihod (prihod je cela konačna faktura), a predračun se ne knjiži.
    first_year, first_month = _month_start(today.year, today.month, -(months - 1))
    rows = db.query(
        "SELECT strftime('%Y-%m', issue_date) AS ym, invoice_type, SUM(net_amount) AS net"
        " FROM invoices WHERE issue_date >= ? AND doc_kind = ? GROUP BY ym, invoice_type",
        (date(first_year, first_month, 1).isoformat(), DOC_INVOICE))
    sums = {(r["ym"], r["invoice_type"]): tax.from_minor(r["net"]) for r in rows}
    for i in range(months):
        y, m = _month_start(first_year, first_month, i)
        key = f"{y:04d}-{m:02d}"
        stats.months.append(MonthPoint(
            label=f"{m:02d}/{y % 100:02d}",
            revenue=sums.get((key, INVOICE_OUT), tax.ZERO),
            expense=sums.get((key, INVOICE_IN), tax.ZERO)))

    repo = InvoiceRepository(db)
    stats.recent_invoices = [i for i in repo.list(limit=16) if not i.is_proforma][:8]
    stats.overdue_invoices = [
        inv for inv in repo.list(status=STATUS_UNPAID) if inv.is_overdue(today)
    ][:8]
    return stats
