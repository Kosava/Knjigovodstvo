"""Modul tr("Blagajna i račun"): uplate i isplate gotovinom ili preko tekućeg računa, sa saldom."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from PyQt6.QtCore import QDate, QLocale, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QWidget,
)

from core import (
    CASH_IN,
    CASH_OUT,
    CASH_TYPES,
    PAY_BANK,
    PAY_CASH,
    PAYMENT_METHODS,
    AppError,
    CashEntry,
    CashRepository,
    DatabaseManager,
    PartnerRepository,
    tax,
)
from settings import SettingsManager, ThemeManager
import i18n
from i18n import tr, tr_noop

from .common import (
    fill_combo,
    select_data,
    BaseDialog,
    BasePage,
    CardGrid,
    PageHeader,
    StatCard,
    configure_table,
    confirm,
    danger,
    primary,
    search_edit,
    show_app_error,
    show_info,
)
from .table_models import FilterProxyModel, ObjectTableModel, cash_columns



def _qdate(d: date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _pydate(q: QDate) -> date:
    return date(q.year(), q.month(), q.day())


class CashEntryDialog(BaseDialog):
    def __init__(self, repo: CashRepository, partners: PartnerRepository,
                 entry: CashEntry | None = None, default_type: str = CASH_IN,
                 parent: QWidget | None = None) -> None:
        super().__init__(tr("Izmena unosa") if entry else tr("Novi unos u blagajni"), parent, min_width=480)
        self.repo = repo
        self.entry = entry or CashEntry(entry_type=default_type)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        self.type = QComboBox()
        fill_combo(self.type, CASH_TYPES)
        select_data(self.type, self.entry.entry_type)
        self.date = QDateEdit(_qdate(self.entry.entry_date))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat(i18n.current().date_qt)
        self.method = QComboBox()
        fill_combo(self.method, PAYMENT_METHODS)
        select_data(self.method, self.entry.method)
        self.method.setToolTip(tr("Gotovina: blagajna (ne može u minus). Račun: tekući račun."))
        self.description = QLineEdit(self.entry.description)
        self.description.setPlaceholderText(tr("npr. Gorivo, Naplata gotovinom…"))
        self.amount = QDoubleSpinBox()
        self.amount.setLocale(QLocale())   # izabrani jezik programa
        self.amount.setDecimals(2)
        self.amount.setRange(0, 999_999_999.99)
        self.amount.setGroupSeparatorShown(True)
        self.amount.setSuffix(" RSD")
        self.amount.setValue(float(self.entry.amount))
        self.partner = QComboBox()
        self.partner.addItem(tr("— bez partnera —"), None)
        for p in partners.list():
            self.partner.addItem(p.name, p.id)
        self.partner.setCurrentIndex(max(0, self.partner.findData(self.entry.partner_id)))

        form.addRow(tr("Tip"), self.type)
        form.addRow(tr("Način"), self.method)
        form.addRow(tr("Datum"), self.date)
        form.addRow(tr("Opis *"), self.description)
        form.addRow(tr("Iznos *"), self.amount)
        form.addRow(tr("Partner"), self.partner)
        self.root.addLayout(form)
        self.add_buttons()
        self.description.setFocus()

    def save(self) -> bool:
        e = self.entry
        e.entry_type = self.type.currentData()
        e.method = self.method.currentData()
        e.entry_date = _pydate(self.date.date())
        e.description = self.description.text()
        e.amount = Decimal(f"{self.amount.value():.2f}")
        e.partner_id = self.partner.currentData()
        if e.id is None:
            self.repo.add(e)
        else:
            self.repo.update(e)
        return True


class CashPage(BasePage):
    TYPE_FILTERS = {tr_noop("Sve"): None, tr_noop("Uplate"): CASH_IN, tr_noop("Isplate"): CASH_OUT}
    METHOD_FILTERS = {tr_noop("Gotovina i račun"): None, PAY_CASH: PAY_CASH, PAY_BANK: PAY_BANK}

    def __init__(self, db: DatabaseManager, theme: ThemeManager, settings: SettingsManager,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = CashRepository(db)
        self.partners = PartnerRepository(db)
        self.theme = theme
        self.settings = settings
        self._build_ui()
        theme.theme_changed.connect(lambda *_: self.table.viewport().update())

    def _build_ui(self) -> None:
        root = self.root_layout()

        header = PageHeader(tr("Blagajna i račun"), tr("Uplate i isplate gotovinom i preko tekućeg računa"))
        self.in_btn = primary(QPushButton(tr("Nova uplata")))
        self.out_btn = QPushButton(tr("Nova isplata"))
        header.actions.addWidget(self.out_btn)
        header.actions.addWidget(self.in_btn)
        root.addWidget(header)

        self.card_in, self.card_out, self.card_balance = (
            StatCard(tr("Uplate (prikazano)")), StatCard(tr("Isplate (prikazano)")), StatCard(tr("Saldo (prikazano)")))
        self.card_cash = StatCard(tr("Gotovina u blagajni"))
        self.card_bank = StatCard(tr("Promet po računu"))
        root.addWidget(CardGrid((self.card_cash, self.card_bank, self.card_in, self.card_out,
                                 self.card_balance), min_card_width=210))

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = search_edit(tr("Pretraga po opisu, partneru, iznosu…"))
        self.type_filter = QComboBox()
        fill_combo(self.type_filter, self.TYPE_FILTERS.keys())
        self.method_filter = QComboBox()
        fill_combo(self.method_filter, self.METHOD_FILTERS.keys())
        self.period = QCheckBox(tr("Period:"))
        today = date.today()
        self.date_from = QDateEdit(_qdate(today.replace(day=1)))
        self.date_to = QDateEdit(_qdate(today))
        for d in (self.date_from, self.date_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat(i18n.current().date_qt)
            d.setEnabled(False)
        self.edit_btn = QPushButton(tr("Izmeni"))
        self.delete_btn = danger(QPushButton(tr("Obriši")))
        bar.addWidget(self.search)
        bar.addWidget(self.type_filter)
        bar.addWidget(self.method_filter)
        bar.addStretch(1)
        bar.addWidget(self.edit_btn)
        bar.addWidget(self.delete_btn)
        root.addLayout(bar)
        period_row = QHBoxLayout()
        period_row.setSpacing(10)
        period_row.addWidget(self.period)
        period_row.addWidget(self.date_from)
        period_row.addWidget(QLabel("–"))
        period_row.addWidget(self.date_to)
        period_row.addStretch(1)
        root.addLayout(period_row)

        self.model = ObjectTableModel(cash_columns(), self.theme)
        self.proxy = FilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        configure_table(self.table, stretch_column=2)
        self.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        root.addWidget(self.table, 1)

        self.footer = QLabel()
        self.footer.setProperty("muted", True)
        root.addWidget(self.footer)

        self.in_btn.clicked.connect(lambda: self.new_entry(CASH_IN))
        self.out_btn.clicked.connect(lambda: self.new_entry(CASH_OUT))
        self.edit_btn.clicked.connect(self.edit_entry)
        self.delete_btn.clicked.connect(self.delete_entry)
        self.table.doubleClicked.connect(lambda *_: self.edit_entry())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())
        self.search.textChanged.connect(self._apply_filters)
        self.type_filter.currentTextChanged.connect(self._apply_filters)
        self.method_filter.currentTextChanged.connect(self._apply_filters)
        self.period.toggled.connect(self._on_period_toggled)
        self.date_from.dateChanged.connect(self._apply_filters)
        self.date_to.dateChanged.connect(self._apply_filters)
        self._update_buttons()

    # -- podaci ----------------------------------------------------------------#
    def refresh(self) -> None:
        try:
            self.model.set_rows(self.repo.list())
            cash = self.repo.balance(PAY_CASH)
            self.card_cash.set_value(self._money(cash), role="success" if cash >= 0 else "danger")
            self.card_bank.set_value(self._money(self.repo.balance(PAY_BANK)), tr("uplate − isplate"))
        except AppError as exc:
            show_app_error(self, exc)
            return
        self._apply_filters()

    def _money(self, value: Decimal) -> str:
        return f"{tax.format_money(value)} {self.settings.get('company.currency', 'RSD')}"

    def _on_period_toggled(self, on: bool) -> None:
        self.date_from.setEnabled(on)
        self.date_to.setEnabled(on)
        self._apply_filters()

    def _apply_filters(self) -> None:
        kind = self.TYPE_FILTERS[self.type_filter.currentData()]
        self.proxy.set_predicate("type", None if kind is None else (lambda c: c.entry_type == kind))
        method = self.METHOD_FILTERS[self.method_filter.currentData()]
        self.proxy.set_predicate("method", None if method is None else (lambda c: c.method == method))
        if self.period.isChecked():
            lo, hi = _pydate(self.date_from.date()), _pydate(self.date_to.date())
            self.proxy.set_predicate("period", lambda c: lo <= c.entry_date <= hi)
        else:
            self.proxy.set_predicate("period", None)
        self.proxy.set_search(self.search.text())

        visible = self.proxy.visible_objects()
        total_in = sum((c.amount for c in visible if c.entry_type == CASH_IN), tax.ZERO)
        total_out = sum((c.amount for c in visible if c.entry_type == CASH_OUT), tax.ZERO)
        balance = total_in - total_out
        self.card_in.set_value(self._money(total_in), role="success")
        self.card_out.set_value(self._money(total_out), role="danger")
        self.card_balance.set_value(self._money(balance), role="success" if balance >= 0 else "danger")
        self.footer.setText(tr("Prikazano {shown} od {total} unosa", shown=len(visible),
                               total=self.model.rowCount()))
        self._update_buttons()

    def _selected(self) -> CashEntry | None:
        index = self.table.currentIndex()
        if not index.isValid() or not self.table.selectionModel().isRowSelected(index.row()):
            return None
        return self.proxy.source_object(index.row())

    def _select_id(self, entry_id: int | None) -> None:
        for row in range(self.proxy.rowCount()):
            if self.proxy.source_object(row).id == entry_id:
                self.table.selectRow(row)
                self.table.scrollTo(self.proxy.index(row, 0))
                return

    def _update_buttons(self) -> None:
        has = self._selected() is not None
        self.edit_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)

    # -- akcije ----------------------------------------------------------------#
    def new_entry(self, kind: str = CASH_IN) -> None:
        dialog = CashEntryDialog(self.repo, self.partners, default_type=kind, parent=self)
        if dialog.exec():
            self.refresh()
            self._select_id(dialog.entry.id)
            self.status_message.emit(tr("Unos u blagajni je sačuvan."))

    def _guard_linked(self, entry: CashEntry) -> bool:
        if entry.invoice_id:
            show_info(self, tr("Ovaj unos je automatski nastao iz plaćene fakture.\n"
                               "Promenu uradite u modulu „Fakture“ (vraćanje na neplaćeno pa ponovo "
                               "označavanje kao plaćene)."), tr("Unos vezan za fakturu"))
            return False
        return True

    def edit_entry(self) -> None:
        entry = self._selected()
        if entry is None or not self._guard_linked(entry):
            return
        fresh = self.repo.get(entry.id) or entry
        dialog = CashEntryDialog(self.repo, self.partners, fresh, parent=self)
        if dialog.exec():
            self.refresh()
            self._select_id(dialog.entry.id)
            self.status_message.emit(tr("Unos u blagajni je ažuriran."))

    def delete_entry(self) -> None:
        entry = self._selected()
        if entry is None or not self._guard_linked(entry):
            return
        if not confirm(self, tr("Brisanje unosa"), tr("Obrisati unos „{text}“?", text=entry.description),
                       confirm_text=tr("Obriši"), destructive=True):
            return
        try:
            self.repo.delete(entry.id)
        except AppError as exc:
            show_app_error(self, exc)
            return
        self.refresh()
        self.status_message.emit(tr("Unos je obrisan."))
