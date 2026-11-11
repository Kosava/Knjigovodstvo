"""Forma za unos i izmenu fakture sa automatskim proračunom PDV-a."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from PyQt6.QtCore import QDate, QLocale, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QCompleter,
)

from core import (
    DOC_ADVANCE,
    DOC_INVOICE,
    DOC_KINDS,
    DOC_PROFORMA,
    STATUS_OPEN,
    VAT_CATEGORIES,
    VAT_GENERAL,
    VatRateRepository,
    AdvanceDeduction,
    Article,
    ArticleRepository,
    INVOICE_IN,
    INVOICE_OUT,
    INVOICE_TYPES,
    PAY_BANK,
    PAYMENT_METHODS,
    STATUS_PAID,
    STATUS_UNPAID,
    Invoice,
    InvoiceItem,
    InvoiceRepository,
    Partner,
    PartnerRepository,
    ValidationError,
    tax,
)
import i18n
from i18n import tr, tr_noop

from .articles_page import UNITS, ArticlePickerDialog, combo_rate, fill_vat_combo
from .common import BaseDialog, field_hint, fill_combo, primary, select_data

_COL_DESC, _COL_QTY, _COL_UNIT, _COL_PRICE, _COL_VAT, _COL_NET, _COL_VATAMT, _COL_TOTAL, _COL_DEL = range(9)

_TITLES = {  # (nov, izmena) po vrsti dokumenta
    DOC_INVOICE: (tr_noop("Nova faktura"), tr_noop("Izmena fakture")),
    DOC_PROFORMA: (tr_noop("Novi predračun"), tr_noop("Izmena predračuna")),
    DOC_ADVANCE: (tr_noop("Novi avansni račun"), tr_noop("Izmena avansnog računa")),
}


def _to_qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _to_date(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


def _spin(decimals: int, maximum: float, minimum: float = 0.0) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setDecimals(decimals)
    box.setRange(minimum, maximum)
    box.setGroupSeparatorShown(True)
    box.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
    box.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    box.setLocale(QLocale())          # podrazumevani locale = izabrani jezik programa
    return box


class InvoiceDialog(BaseDialog):
    """Zaglavlje fakture + tabela stavki + ukupni iznosi koji se računaju uživo."""

    def __init__(self, invoices: InvoiceRepository, partners: PartnerRepository,
                 invoice: Invoice | None = None, default_vat: str = VAT_GENERAL,
                 default_terms: int = 15, parent: QWidget | None = None,
                 articles: ArticleRepository | None = None, theme=None) -> None:
        editing = invoice is not None and invoice.id is not None
        super().__init__(tr("Izmena fakture") if editing else tr("Nova faktura"), parent, min_width=980)
        self.invoices, self.partners, self.articles, self.theme = invoices, partners, articles, theme
        self.invoice = invoice or Invoice()
        self.editing = editing
        # Dokument napravljen po predračunu: kupac i vrsta su određeni predračunom.
        self.from_proforma = self.invoice.source_id is not None and not editing
        self._articles: list[Article] = articles.list(active_only=True) if articles else []
        self._linked_advances = list(self.invoice.advances)
        # Partner postojeće fakture ostaje u listi i ako mu je u međuvremenu promenjen tip.
        self._initial_partner_id = self.invoice.partner_id if editing else None
        # Stope PDV-a zavise od datuma prometa; podrazumevana vrsta stope dolazi iz podešavanja.
        self.vat_rates = VatRateRepository(invoices.db)
        self._default_category = default_vat if default_vat in VAT_CATEGORIES else VAT_GENERAL
        self._rates = self.vat_rates.rates_on(self.invoice.effective_supply_date)
        self._all_partners: list[Partner] = partners.list()
        self._auto_number = ""
        self._last_issue: QDate | None = None    # da datum prometa prati datum fakture

        self._build_header(default_terms)
        self._build_items()
        self._build_footer()
        self.add_buttons()

        self._load(default_terms)
        self.resize(1040, 720)

    # ------------------------------------------------------------------ #
    # Gradnja
    # ------------------------------------------------------------------ #
    def _build_header(self, default_terms: int) -> None:
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)

        self.type = QComboBox()
        fill_combo(self.type, INVOICE_TYPES)
        self.kind = QComboBox()
        fill_combo(self.kind, DOC_KINDS)
        self.kind.setToolTip(tr("Predračun i avansni račun postoje samo za izlazne dokumente."))
        self.number = QLineEdit()
        self.number.setPlaceholderText(tr("npr. 2026/0001"))
        self.issue_date = QDateEdit()
        self.issue_date.setCalendarPopup(True)
        self.issue_date.setDisplayFormat(i18n.current().date_qt)
        self.terms = QSpinBox()
        self.terms.setRange(0, 365)
        self.terms.setSuffix(" " + tr("dana"))
        self.due_label = QLabel()
        self.due_label.setProperty("muted", True)

        self.partner = QComboBox()
        self.partner.setEditable(True)
        self.partner.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.partner.lineEdit().setPlaceholderText(tr("Počnite da kucate naziv partnera…"))
        completer = self.partner.completer()
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)

        self.status = QComboBox()
        fill_combo(self.status, [STATUS_UNPAID, STATUS_PAID])
        self.paid_on = QDateEdit()
        self.paid_on.setCalendarPopup(True)
        self.paid_on.setDisplayFormat(i18n.current().date_qt)
        self.paid_on.setEnabled(False)
        self.method = QComboBox()
        fill_combo(self.method, PAYMENT_METHODS)
        select_data(self.method, PAY_BANK)
        self.method.setEnabled(False)
        self.supply_date = QDateEdit()
        self.supply_date.setCalendarPopup(True)
        self.supply_date.setDisplayFormat(i18n.current().date_qt)
        self.supply_date.setToolTip(tr("Datum prometa dobara / usluga (obično isti kao datum fakture)"))

        def labeled(text: str, widget: QWidget) -> QVBoxLayout:
            box = QVBoxLayout()
            box.setSpacing(4)
            label = QLabel(text)
            label.setProperty("muted", True)
            box.addWidget(label)
            box.addWidget(widget)
            return box

        self.source_label = QLabel()
        self.source_label.setProperty("muted", True)
        self._status_labels: list[QLabel] = []

        def status_part(text: str, widget: QWidget) -> QVBoxLayout:
            box = labeled(text, widget)
            self._status_labels.append(box.itemAt(0).widget())
            return box

        grid.addLayout(labeled(tr("Tip fakture"), self.type), 0, 0)
        grid.addLayout(labeled(tr("Vrsta dokumenta"), self.kind), 0, 1)
        grid.addLayout(labeled(tr("Broj dokumenta *"), self.number), 0, 2)
        grid.addLayout(labeled(tr("Datum"), self.issue_date), 0, 3)
        grid.addLayout(labeled(tr("Partner *"), self.partner), 1, 0, 1, 2)
        grid.addLayout(labeled(tr("Datum prometa"), self.supply_date), 1, 2)
        self._supply_label = grid.itemAtPosition(1, 2).layout().itemAt(0).widget()
        grid.addLayout(labeled(tr("Rok plaćanja"), self.terms), 1, 3)
        grid.addLayout(status_part(tr("Status"), self.status), 2, 0)
        grid.addLayout(status_part(tr("Datum plaćanja"), self.paid_on), 2, 1)
        grid.addLayout(status_part(tr("Način plaćanja"), self.method), 2, 2)
        grid.addWidget(self.due_label, 2, 3, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        grid.addWidget(self.source_label, 3, 0, 1, 4)
        for col in range(4):
            grid.setColumnStretch(col, 1)
        self.root.addLayout(grid)

        self.type.currentTextChanged.connect(self._on_type_changed)
        self.kind.currentIndexChanged.connect(self._on_kind_changed)
        self.partner.currentIndexChanged.connect(lambda *_: self._fill_advances())
        self.issue_date.dateChanged.connect(self._on_issue_date_changed)
        self.supply_date.dateChanged.connect(self._on_supply_date_changed)
        self.terms.valueChanged.connect(self._update_due_label)
        self.status.currentTextChanged.connect(self._update_paid_state)

    def _build_items(self) -> None:
        bar = QHBoxLayout()
        title = QLabel(tr("Stavke"))
        title.setObjectName("SectionTitle")
        self.add_row_btn = QPushButton(tr("+ Dodaj stavku"))
        self.pick_btn = QPushButton(tr("Iz šifarnika…"))
        self.pick_btn.setToolTip(tr("Dodaje stavke iz šifarnika artikala i usluga"))
        self.pick_btn.setEnabled(bool(self._articles))
        bar.addWidget(title)
        bar.addStretch(1)
        bar.addWidget(self.pick_btn)
        bar.addWidget(self.add_row_btn)
        self.root.addLayout(bar)

        headers = [tr("Opis"), tr("Količina"), tr("JM"), tr("Cena"), tr("PDV %"), tr("Bez PDV-a"),
                   tr("PDV"), tr("Ukupno"), ""]
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(220)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(_COL_DESC, QHeaderView.ResizeMode.Stretch)
        for col, width in ((_COL_QTY, 100), (_COL_UNIT, 110), (_COL_PRICE, 120), (_COL_VAT, 100),
                           (_COL_NET, 115), (_COL_VATAMT, 105), (_COL_TOTAL, 120), (_COL_DEL, 44)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, width)
        self.root.addWidget(self.table, 1)
        self.add_row_btn.clicked.connect(lambda: self._add_row())
        self.pick_btn.clicked.connect(self._pick_articles)

        # Odbijeni avansi (samo izlazna faktura, kad kupac ima plaćene neiskorišćene avanse)
        self.advances_box = QWidget()
        box = QVBoxLayout(self.advances_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        label = QLabel(tr("Odbijeni avansi (označite avanse koji se odbijaju od ove fakture)"))
        label.setProperty("muted", True)
        self.advances_list = QListWidget()
        self.advances_list.setFixedHeight(78)
        box.addWidget(label)
        box.addWidget(self.advances_list)
        self.root.addWidget(self.advances_box)
        self.advances_list.itemChanged.connect(lambda *_: self._recalculate())

    def _build_footer(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(24)

        note_box = QVBoxLayout()
        label = QLabel(tr("Napomena"))
        label.setProperty("muted", True)
        self.note = QPlainTextEdit()
        self.note.setFixedHeight(76)
        note_box.addWidget(label)
        note_box.addWidget(self.note)
        row.addLayout(note_box, 1)

        totals = QFormLayout()
        totals.setHorizontalSpacing(24)
        totals.setVerticalSpacing(6)
        self.net_label, self.vat_label, self.total_label = QLabel(), QLabel(), QLabel()
        self.gross_label, self.advance_label = QLabel(), QLabel()
        for lab in (self.net_label, self.vat_label, self.total_label, self.gross_label, self.advance_label):
            lab.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.total_label.setObjectName("CardValue")
        totals.addRow(tr("Ukupno bez PDV-a:"), self.net_label)
        totals.addRow(tr("PDV:"), self.vat_label)
        totals.addRow(tr("Ukupno:"), self.gross_label)
        totals.addRow(tr("Umanjenje za avans:"), self.advance_label)
        totals.addRow(tr("Ukupno za uplatu:"), self.total_label)
        self.totals_form = totals
        row.addLayout(totals)
        self.root.addLayout(row)

    # ------------------------------------------------------------------ #
    # Učitavanje postojeće fakture / podrazumevane vrednosti
    # ------------------------------------------------------------------ #
    def _load(self, default_terms: int) -> None:
        inv = self.invoice
        select_data(self.type, inv.invoice_type)
        select_data(self.kind, inv.doc_kind)
        self.issue_date.setDate(_to_qdate(inv.issue_date))
        if inv.due_date is not None:
            self.terms.setValue(max(0, (inv.due_date - inv.issue_date).days))
        else:
            self.terms.setValue(default_terms)
        self._fill_partners(inv.partner_id)
        self.supply_date.setDate(_to_qdate(inv.effective_supply_date))
        self.paid_on.setMinimumDate(_to_qdate(inv.issue_date))
        self.paid_on.setDate(_to_qdate(inv.paid_date or max(date.today(), inv.issue_date)))
        select_data(self.status, inv.status)
        self.note.setPlainText(inv.note)

        if self.editing:
            self.number.setText(inv.number)
            # Status se menja posebno (dugme tr("Označi plaćeno")), jer povlači unos u blagajnu.
            self.status.setEnabled(False)
        else:
            self._suggest_number()
        # Vrsta dokumenta se ne menja posle čuvanja, a ni kod dokumenta po predračunu.
        self.kind.setEnabled(not self.editing and not self.from_proforma)
        if self.from_proforma:
            self.type.setEnabled(False)
            self.partner.setEnabled(False)
        if inv.source_number:
            self.source_label.setText(tr("Po predračunu {number}", number=inv.source_number))

        for item in inv.items or [None]:
            self._add_row(item)
        self._update_due_label()
        self._on_kind_changed()
        self._recalculate()

    def _suggest_number(self) -> None:
        """Za izlazne fakture predlaže sledeći broj; za ulazne ostavlja prazno."""
        current = self.number.text().strip()
        if current and current != self._auto_number:
            return   # korisnik je sam upisao broj
        if self.type.currentData() == INVOICE_OUT:
            try:
                self._auto_number = self.invoices.next_number(INVOICE_OUT, self.issue_date.date().year(),
                                                              self.kind.currentData())
            except Exception:
                self._auto_number = ""
        else:
            self._auto_number = ""
        self.number.setText(self._auto_number)

    def _fill_partners(self, select_id: int | None = None) -> None:
        """Puni listu partnera koji odgovaraju tipu fakture (kupci za izlazne...)."""
        kind = self.type.currentData()
        if select_id is None and self.partner.currentIndex() >= 0:
            select_id = self.partner.currentData()
        self.partner.blockSignals(True)
        self.partner.clear()
        for p in self._all_partners:
            if p.matches_invoice_type(kind) or p.id == self._initial_partner_id:
                ident = f"{p.tax_id_label} {p.pib}" if p.pib else tr(p.entity_type).lower()
                self.partner.addItem(f"{p.name}  ·  {ident}", p.id)
        index = self.partner.findData(select_id) if select_id is not None else -1
        self.partner.setCurrentIndex(index)
        if index < 0:
            self.partner.setEditText("")
        self.partner.blockSignals(False)

    # ------------------------------------------------------------------ #
    # Stavke
    # ------------------------------------------------------------------ #
    def _add_row(self, item: InvoiceItem | None = None) -> None:
        new = item is None
        item = item or InvoiceItem(vat_rate=self._rates[self._default_category])
        row = self.table.rowCount()
        self.table.insertRow(row)

        desc = QLineEdit(item.description)
        desc.setPlaceholderText(tr("Naziv robe ili usluge (ili izbor iz šifarnika)") if self._articles
                                else tr("Naziv robe ili usluge"))
        if self._articles:
            completer = QCompleter([a.name for a in self._articles], desc)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.activated[str].connect(lambda name, d=desc: self._apply_article(d, name))
            desc.setCompleter(completer)
        qty = _spin(3, 9_999_999.0, 0.0)
        qty.setValue(float(item.quantity))
        unit = QComboBox()
        unit.setEditable(True)
        unit.addItems(UNITS)
        unit.setCurrentText(item.unit or "kom")
        price = _spin(2, 999_999_999.99)
        price.setValue(float(item.unit_price))
        vat = QComboBox()
        if new:
            fill_vat_combo(vat, self._rates, category=self._default_category)
        else:
            fill_vat_combo(vat, self._rates, rate=item.vat_rate)
        remove = QPushButton("×")
        remove.setToolTip(tr("Ukloni stavku"))
        remove.setFixedSize(32, 32)
        remove.setStyleSheet("font-size: 16px; padding: 0;")

        self.table.setCellWidget(row, _COL_DESC, desc)
        self.table.setCellWidget(row, _COL_QTY, qty)
        self.table.setCellWidget(row, _COL_UNIT, unit)
        self.table.setCellWidget(row, _COL_PRICE, price)
        self.table.setCellWidget(row, _COL_VAT, vat)
        self.table.setCellWidget(row, _COL_DEL, remove)
        for col in (_COL_NET, _COL_VATAMT, _COL_TOTAL):
            cell = QTableWidgetItem("0,00")
            cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
            cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, col, cell)

        qty.valueChanged.connect(self._recalculate)
        price.valueChanged.connect(self._recalculate)
        vat.currentIndexChanged.connect(self._recalculate)
        remove.clicked.connect(lambda _=False, b=remove: self._remove_row(b))
        self._recalculate()
        if not item.description:
            desc.setFocus()

    def _apply_article(self, desc: QLineEdit, name: str) -> None:
        """Izabran artikal iz padajuće liste: popunjava jedinicu, cenu i PDV u tom redu."""
        article = next((a for a in self._articles if a.name == name), None)
        if article is None:
            return
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, _COL_DESC) is desc:
                self._fill_row(row, article)
                break

    def _fill_row(self, row: int, article: Article) -> None:
        self.table.cellWidget(row, _COL_DESC).setText(article.name)
        self.table.cellWidget(row, _COL_UNIT).setCurrentText(article.unit)
        self.table.cellWidget(row, _COL_PRICE).setValue(float(article.price))
        fill_vat_combo(self.table.cellWidget(row, _COL_VAT), self._rates, category=article.vat_category)
        self._recalculate()

    def _pick_articles(self) -> None:
        dialog = ArticlePickerDialog(self._articles, self.theme, self)
        if not dialog.exec():
            return
        for article in dialog.selected():
            last = self.table.rowCount() - 1
            empty = last >= 0 and not self.table.cellWidget(last, _COL_DESC).text().strip() \
                and self.table.cellWidget(last, _COL_PRICE).value() == 0
            if not empty:
                self._add_row()
                last = self.table.rowCount() - 1
            self._fill_row(last, article)

    def _remove_row(self, button: QPushButton) -> None:
        for row in range(self.table.rowCount()):
            if self.table.cellWidget(row, _COL_DEL) is button:
                if self.table.rowCount() == 1:      # uvek ostaje bar jedna (prazna) stavka
                    self.table.cellWidget(row, _COL_DESC).clear()
                    self.table.cellWidget(row, _COL_PRICE).setValue(0)
                    self.table.cellWidget(row, _COL_QTY).setValue(1)
                else:
                    self.table.removeRow(row)
                break
        self._recalculate()

    def _row_item(self, row: int) -> InvoiceItem:
        qty = self.table.cellWidget(row, _COL_QTY).value()
        price = self.table.cellWidget(row, _COL_PRICE).value()
        vat_box: QComboBox = self.table.cellWidget(row, _COL_VAT)
        return InvoiceItem(
            description=self.table.cellWidget(row, _COL_DESC).text().strip(),
            quantity=Decimal(f"{qty:.3f}"),
            unit=self.table.cellWidget(row, _COL_UNIT).currentText().strip() or "kom",
            unit_price=Decimal(f"{price:.2f}"),
            vat_rate=combo_rate(vat_box, self._rates),
        )

    def _items(self) -> list[InvoiceItem]:
        return [self._row_item(r) for r in range(self.table.rowCount())]

    def _recalculate(self, *_args) -> None:
        """Preračunava iznose po stavkama i ukupno (poziva se pri svakoj izmeni)."""
        if not hasattr(self, "net_label"):      # signal stigao pre izgradnje podnožja
            return
        amounts = []
        for row in range(self.table.rowCount()):
            a = self._row_item(row).amounts
            amounts.append(a)
            self.table.item(row, _COL_NET).setText(tax.format_money(a.net))
            self.table.item(row, _COL_VATAMT).setText(tax.format_money(a.vat))
            self.table.item(row, _COL_TOTAL).setText(tax.format_money(a.total))
        total = tax.sum_amounts(amounts)
        advance = tax.sum_amounts(a.amounts for a in self._checked_advances()) \
            if hasattr(self, "advances_list") else tax.EMPTY_AMOUNTS
        self.net_label.setText(tax.format_money(total.net))
        self.vat_label.setText(tax.format_money(total.vat))
        self.gross_label.setText(tax.format_money(total.total))
        self.advance_label.setText(tax.format_money(-advance.total))
        self.total_label.setText(f"{tax.format_money(total.total - advance.total)} RSD")
        with_advance = advance.total != 0
        self.totals_form.setRowVisible(self.gross_label, with_advance)
        self.totals_form.setRowVisible(self.advance_label, with_advance)

    # ------------------------------------------------------------------ #
    # Zaglavlje
    # ------------------------------------------------------------------ #
    def _on_type_changed(self) -> None:
        # Ulazni dokument je uvek faktura.
        out = self.type.currentData() == INVOICE_OUT
        if not out:
            select_data(self.kind, DOC_INVOICE)
        self.kind.setEnabled(out and not self.editing and not self.from_proforma)
        self._fill_partners()
        self._suggest_number()
        self._fill_advances()

    def _on_kind_changed(self) -> None:
        kind = self.kind.currentData()
        new_title, edit_title = _TITLES.get(kind, _TITLES[DOC_INVOICE])
        self.setWindowTitle(tr(edit_title if self.editing else new_title))
        proforma = kind == DOC_PROFORMA
        # Predračun nema status plaćanja ni datum prometa; avansni račun se izdaje na dan uplate.
        for widget in (self.status, self.paid_on, self.method, *self._status_labels):
            widget.setVisible(not proforma)
        self.supply_date.setVisible(not proforma)
        self._supply_label.setVisible(not proforma)
        if kind == DOC_ADVANCE and not self.editing:
            select_data(self.status, STATUS_PAID)
        self._update_paid_state()
        if not self.editing:
            self._suggest_number()
        self._fill_advances()

    # ------------------------------------------------------------------ #
    # Avansi
    # ------------------------------------------------------------------ #
    def _fill_advances(self) -> None:
        if not hasattr(self, "advances_list"):
            return
        applies = self.type.currentData() == INVOICE_OUT and self.kind.currentData() == DOC_INVOICE
        checked = {a.advance_id for a in self._checked_advances()} | {a.advance_id for a in self._linked_advances}
        self._linked_advances = []
        options: list[AdvanceDeduction] = []
        if applies:
            try:
                options = self.invoices.available_advances(self._selected_partner_id(), self.invoice.id)
            except Exception:
                options = []
        self.advances_list.blockSignals(True)
        self.advances_list.clear()
        for adv in options:
            text = tr("{number} od {date} · {amount} RSD (PDV {vat})", number=adv.number,
                      date=tax.format_date(adv.issue_date), amount=tax.format_money(adv.amounts.total),
                      vat=tax.format_money(adv.amounts.vat))
            entry = QListWidgetItem(text)
            entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            entry.setCheckState(Qt.CheckState.Checked if adv.advance_id in checked else Qt.CheckState.Unchecked)
            entry.setData(Qt.ItemDataRole.UserRole, adv)
            self.advances_list.addItem(entry)
        self.advances_list.blockSignals(False)
        self.advances_box.setVisible(bool(options))
        self._recalculate()

    def _checked_advances(self) -> list[AdvanceDeduction]:
        result = []
        for row in range(self.advances_list.count()):
            entry = self.advances_list.item(row)
            if entry.checkState() == Qt.CheckState.Checked:
                result.append(entry.data(Qt.ItemDataRole.UserRole))
        return result

    def _on_issue_date_changed(self) -> None:
        new_issue = self.issue_date.date()
        # Datum prometa prati datum fakture dok ga korisnik ne promeni ručno.
        if self._last_issue is None or self.supply_date.date() == self._last_issue:
            self.supply_date.setDate(new_issue)
        self._last_issue = new_issue
        self._update_due_label()
        # Datum plaćanja ne može biti pre datuma fakture (QDateEdit sam pomera vrednost).
        self.paid_on.setMinimumDate(self.issue_date.date())
        if not self.editing:
            self._suggest_number()   # nova godina -> nova numeracija (ručno upisan broj ostaje)

    def _on_supply_date_changed(self) -> None:
        """Nova stopa PDV-a važi po datumu prometa: stavke zadržavaju vrstu stope, a iznos se menja."""
        if not hasattr(self, "table"):
            return
        rates = self.vat_rates.rates_on(_to_date(self.supply_date.date()))
        if rates == self._rates:
            return
        old = self._rates
        self._rates = rates
        for row in range(self.table.rowCount()):
            box: QComboBox = self.table.cellWidget(row, _COL_VAT)
            key = box.currentData() or VAT_GENERAL
            if key in VAT_CATEGORIES:
                fill_vat_combo(box, rates, category=key)
            else:
                fill_vat_combo(box, rates, rate=combo_rate(box, old))
        self._recalculate()

    def _update_paid_state(self) -> None:
        """Datum i način plaćanja se biraju samo za novu fakturu sa statusom "Plaćeno"."""
        enabled = (not self.editing and self.status.currentData() == STATUS_PAID
                   and self.kind.currentData() != DOC_PROFORMA)
        self.paid_on.setEnabled(enabled)
        self.method.setEnabled(enabled)

    def _due_date(self) -> date:
        return _to_date(self.issue_date.date()) + timedelta(days=self.terms.value())

    def _update_due_label(self) -> None:
        self.due_label.setText(tr("Dospeva: {date}", date=tax.format_date(self._due_date())))

    def _selected_partner_id(self) -> int | None:
        text = self.partner.currentText().strip().casefold()
        index = self.partner.currentIndex()
        if index >= 0 and self.partner.itemText(index).strip().casefold() == text:
            return self.partner.itemData(index)
        index = self.partner.findText(self.partner.currentText().strip(),
                                      Qt.MatchFlag.MatchFixedString)
        return self.partner.itemData(index) if index >= 0 else None

    # ------------------------------------------------------------------ #
    # Čuvanje
    # ------------------------------------------------------------------ #
    def save(self) -> bool:
        partner_id = self._selected_partner_id()
        if partner_id is None:
            raise ValidationError(tr("Izaberite partnera iz liste (možete kucati deo naziva)."))
        inv = self.invoice
        inv.invoice_type = self.type.currentData()
        inv.doc_kind = self.kind.currentData()
        inv.advances = self._checked_advances()
        inv.number = self.number.text()
        inv.issue_date = _to_date(self.issue_date.date())
        inv.supply_date = _to_date(self.supply_date.date())
        inv.due_date = self._due_date()
        inv.partner_id = partner_id
        inv.note = self.note.toPlainText()
        inv.items = self._items()
        if not self.editing:
            inv.status = STATUS_OPEN if inv.is_proforma else self.status.currentData()
            inv.paid_date = _to_date(self.paid_on.date()) if inv.status == STATUS_PAID else None
            inv.payment_method = self.method.currentData()

        if self.editing:
            self.invoices.update(inv)
        else:
            self.invoices.add(inv)
        return True


class PaymentDateDialog(QDialog):
    """Mali dijalog: datum i način plaćanja fakture."""

    def __init__(self, title: str, default: date, parent: QWidget | None = None,
                 minimum: date | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Datum plaćanja"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel(title))
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat(i18n.current().date_qt)
        if minimum is not None:      # npr. datum fakture: plaćanje ne može biti pre njega
            self.date.setMinimumDate(_to_qdate(minimum))
        self.date.setDate(_to_qdate(default))
        layout.addWidget(self.date)
        layout.addWidget(QLabel(tr("Način plaćanja:")))
        self.method_box = QComboBox()
        fill_combo(self.method_box, PAYMENT_METHODS)
        select_data(self.method_box, PAY_BANK)
        self.method_box.setToolTip(tr("Gotovina ide u blagajnu, a plaćanje preko računa na tekući račun."))
        layout.addWidget(self.method_box)
        box = QDialogButtonBox()
        ok = box.addButton(tr("Označi plaćeno"), QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton(tr("Otkaži"), QDialogButtonBox.ButtonRole.RejectRole)
        primary(ok)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def value(self) -> date:
        return _to_date(self.date.date())

    def method(self) -> str:
        return self.method_box.currentData()


__all__ = ["InvoiceDialog", "PaymentDateDialog", "field_hint"]
