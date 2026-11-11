"""Generički model za tabele + proxy za sortiranje i filtriranje u realnom vremenu."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from PyQt6.QtCore import (
    QAbstractTableModel,
    QCollator,
    QLocale,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PyQt6.QtGui import QBrush

from core import (CASH_IN, STATUS_CANCELLED, STATUS_OPEN, STATUS_PAID, STATUS_REALIZED, Article, CashEntry,
                  Invoice, Partner, tax)
from i18n import tr
from settings import ThemeManager

ALIGN_LEFT = (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter).value
ALIGN_RIGHT = (Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter).value
ALIGN_CENTER = (Qt.AlignmentFlag.AlignCenter).value


@dataclass(frozen=True)
class Column:
    """Opis jedne kolone: naslov, sirova vrednost (za sortiranje) i prikaz."""

    title: str                          # ključ prevoda (srpski); prikazuje se kao tr(title)
    value: Callable[[Any], Any]
    fmt: Callable[[Any], str] | None = None
    align: int = ALIGN_LEFT
    color: Callable[[Any], str | None] | None = None   # naziv boje iz teme, npr. "danger"


class ObjectTableModel(QAbstractTableModel):
    """Tabelarni model nad listom proizvoljnih objekata (partneri, fakture...)."""

    def __init__(self, columns: Sequence[Column], theme: ThemeManager | None = None,
                 search_text: Callable[[Any], str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._columns = list(columns)
        self._rows: list[Any] = []
        self._theme = theme
        self._search_text = search_text

    # -- podaci ---------------------------------------------------------------#
    def set_rows(self, rows: Sequence[Any]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> Any:
        return self._rows[row]

    def rows(self) -> list[Any]:
        return list(self._rows)

    def display_text(self, obj: Any, col: int) -> str:
        column = self._columns[col]
        value = column.value(obj)
        if column.fmt is not None:
            return column.fmt(value)
        return "" if value is None else str(value)

    def search_text(self, obj: Any) -> str:
        if self._search_text is not None:
            return self._search_text(obj)
        return " ".join(self.display_text(obj, c) for c in range(len(self._columns)))

    # -- QAbstractTableModel -------------------------------------------------- #
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        obj, column = self._rows[index.row()], self._columns[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self.display_text(obj, index.column())
        if role == Qt.ItemDataRole.UserRole:           # sirova vrednost za sortiranje
            return column.value(obj)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return column.align
        if role == Qt.ItemDataRole.ForegroundRole and column.color and self._theme:
            name = column.color(obj)
            if name:
                return QBrush(self._theme.color(name))
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return tr(self._columns[section].title)
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return self._columns[section].align
        return None


class FilterProxyModel(QSortFilterProxyModel):
    """Sortira po sirovim vrednostima i filtrira po tekstu + dodatnim uslovima."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self.setDynamicSortFilter(True)
        self._terms: list[str] = []
        self._predicates: dict[str, Callable[[Any], bool]] = {}
        # Pravilan redosled slova (č, ć, š, ž, đ) i "brojevi kao brojevi" (2026/0002 < 2026/0010)
        self._collator = QCollator(QLocale(QLocale.Language.Serbian, QLocale.Script.LatinScript,
                                           QLocale.Country.Serbia))
        self._collator.setNumericMode(True)
        self._collator.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_search(self, text: str) -> None:
        """Svaka reč mora da se nalazi negde u redu (redosled nije bitan)."""
        self._terms = [t for t in text.casefold().split() if t]
        self.invalidateFilter()

    def set_predicate(self, name: str, predicate: Callable[[Any], bool] | None) -> None:
        if predicate is None:
            self._predicates.pop(name, None)
        else:
            self._predicates[name] = predicate
        self.invalidateFilter()

    def source_object(self, proxy_row: int) -> Any:
        source = self.mapToSource(self.index(proxy_row, 0))
        return self.sourceModel().row_at(source.row())

    def visible_objects(self) -> list[Any]:
        return [self.source_object(r) for r in range(self.rowCount())]

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model: ObjectTableModel = self.sourceModel()  # type: ignore[assignment]
        obj = model.row_at(source_row)
        if any(not pred(obj) for pred in self._predicates.values()):
            return False
        if self._terms:
            haystack = model.search_text(obj).casefold()
            return all(term in haystack for term in self._terms)
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        a, b = left.data(Qt.ItemDataRole.UserRole), right.data(Qt.ItemDataRole.UserRole)
        if a is None or b is None:
            return a is None and b is not None
        if isinstance(a, str) and isinstance(b, str):
            return self._collator.compare(a, b) < 0
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


# --------------------------------------------------------------------------- #
# Definicije kolona
# --------------------------------------------------------------------------- #
def partner_columns() -> list[Column]:
    return [
        Column("Naziv", lambda p: p.name),
        Column("PIB / poreski broj", lambda p: p.pib),
        Column("Vrsta", lambda p: tr(p.entity_type)),
        Column("Tip", lambda p: tr(p.partner_type)),
        Column("Adresa", lambda p: p.address),
        Column("Grad", lambda p: p.city),
        Column("Tekući račun", lambda p: p.account),
        Column("E-pošta", lambda p: p.email),
        Column("Telefon", lambda p: p.phone),
    ]


def _status_color(inv: Invoice) -> str | None:
    if inv.status in (STATUS_CANCELLED, STATUS_REALIZED):
        return "text_muted"
    if inv.status == STATUS_OPEN:
        return None
    return "success" if inv.status == STATUS_PAID else "danger"


def _status_text(inv: Invoice) -> str:
    if inv.is_storno:
        return (tr("Storno ({number})", number=inv.storno_of_number) if inv.storno_of_number
                else tr("Storno"))
    return tr(inv.status)


def invoice_columns(compact: bool = False, only: Sequence[str] | None = None) -> list[Column]:
    """Kolone fakture; ``compact`` izostavlja tip i PDV, ``only`` bira kolone po naslovu."""
    cols = [
        Column("Broj", lambda i: i.number),
        Column("Tip", lambda i: tr(i.kind_label)),
        Column("Datum", lambda i: i.issue_date, tax.format_date),
        Column("Rok plaćanja", lambda i: i.due_date, tax.format_date,
               color=lambda i: "danger" if i.is_overdue() else None),
        Column("Partner", lambda i: i.partner_name),
        Column("Bez PDV-a", lambda i: i.net_amount, tax.format_money, ALIGN_RIGHT),
        Column("PDV", lambda i: i.vat_amount, tax.format_money, ALIGN_RIGHT),
        Column("Ukupno", lambda i: i.total_amount, tax.format_money, ALIGN_RIGHT),
        Column("Status", _status_text, color=_status_color),
    ]
    if compact:
        keep = {"Broj", "Datum", "Rok plaćanja", "Partner", "Ukupno", "Status"}
        cols = [c for c in cols if c.title in keep]
    if only is not None:
        cols = [c for c in cols if c.title in only]
    return cols


def article_columns() -> list[Column]:
    return [
        Column("Šifra", lambda a: a.code),
        Column("Naziv", lambda a: a.name),
        Column("Vrsta", lambda a: tr(a.kind)),
        Column("JM", lambda a: a.unit),
        Column("Cena bez PDV-a", lambda a: a.price, tax.format_money, ALIGN_RIGHT),
        Column("PDV %", lambda a: a.vat_rate, tax.format_quantity, ALIGN_RIGHT),
        Column("Cena sa PDV-om", lambda a: a.price_with_vat, tax.format_money, ALIGN_RIGHT),
        Column("Aktivan", lambda a: tr("da") if a.active else tr("ne"),
               color=lambda a: None if a.active else "text_muted"),
    ]


def _signed(value: tax.Decimal) -> str:
    sign = "+" if value > 0 else "−"
    return f"{sign}{tax.format_money(abs(value))}"


def cash_columns() -> list[Column]:
    return [
        Column("Datum", lambda c: c.entry_date, tax.format_date),
        Column("Tip", lambda c: tr(c.entry_type),
               color=lambda c: "success" if c.entry_type == CASH_IN else "danger"),
        Column("Opis", lambda c: c.description),
        Column("Partner", lambda c: c.partner_name),
        Column("Način", lambda c: tr(c.method)),
        Column("Veza", lambda c: tr("Faktura") if c.invoice_id else ""),
        Column("Iznos", lambda c: c.signed_amount, _signed, ALIGN_RIGHT,
               color=lambda c: "success" if c.entry_type == CASH_IN else "danger"),
    ]


__all__ = [
    "Column", "ObjectTableModel", "FilterProxyModel",
    "partner_columns", "invoice_columns", "cash_columns", "article_columns",
    "Partner", "Invoice", "CashEntry", "Article",
]
