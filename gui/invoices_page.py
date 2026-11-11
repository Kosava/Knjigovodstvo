"""Modul "Fakture/Računi": izlazne i ulazne fakture, status plaćanja, štampa/PDF."""

from __future__ import annotations

from datetime import date

from PyQt6.QtCore import QLocale, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core import (
    DOC_ADVANCE,
    DOC_INVOICE,
    DOC_PROFORMA,
    STATUS_OPEN,
    STATUS_REALIZED,
    ArticleRepository,
    INVOICE_IN,
    INVOICE_OUT,
    PAY_CASH,
    STATUS_CANCELLED,
    STATUS_PAID,
    STATUS_UNPAID,
    AppError,
    DatabaseManager,
    Invoice,
    InvoiceRepository,
    PartnerRepository,
    tax,
)
from core.printing import render_invoice_html
from settings import SettingsManager, ThemeManager
from i18n import tr, tr_noop

from . import printing
from .common import (
    fill_combo,
    select_data,
    BasePage,
    PageHeader,
    configure_table,
    confirm,
    danger,
    primary,
    search_edit,
    show_app_error,
    show_error,
    show_info,
)
from .invoice_dialog import InvoiceDialog, PaymentDateDialog
from .table_models import FilterProxyModel, ObjectTableModel, invoice_columns


class AdvanceAmountDialog(QDialog):
    """Iznos primljenog avansa (sa PDV-om) za avansni račun po predračunu."""

    def __init__(self, proforma: Invoice, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Avansni račun"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel(tr("Primljeni avans po predračunu {number} ({partner}):",
                                   number=proforma.number, partner=proforma.partner_name)))
        form = QFormLayout()
        self.amount = QDoubleSpinBox()
        self.amount.setDecimals(2)
        self.amount.setRange(0.01, float(proforma.total_amount))
        self.amount.setGroupSeparatorShown(True)
        self.amount.setLocale(QLocale())
        self.amount.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.amount.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.amount.setValue(float(proforma.total_amount))
        self.amount.setSuffix(" RSD")
        form.addRow(tr("Iznos sa PDV-om:"), self.amount)
        layout.addLayout(form)
        hint = QLabel(tr("Iznos predračuna je {amount} RSD. Avans se deli po stopama PDV-a "
                         "srazmerno predračunu.", amount=tax.format_money(proforma.total_amount)))
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        layout.addWidget(hint)
        box = QDialogButtonBox()
        ok = primary(box.addButton(tr("Dalje"), QDialogButtonBox.ButtonRole.AcceptRole))
        box.addButton(tr("Otkaži"), QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        ok.setDefault(True)

    def value(self) -> tax.Decimal:
        return tax.round_money(f"{self.amount.value():.2f}")


class InvoicesPage(BasePage):
    TYPE_FILTERS = {tr_noop("Sve fakture"): None, tr_noop("Izlazne (kupci)"): INVOICE_OUT,
                    tr_noop("Ulazne (dobavljači)"): INVOICE_IN, tr_noop("Predračuni"): DOC_PROFORMA,
                    tr_noop("Avansni računi"): DOC_ADVANCE}
    STATUS_FILTERS = {tr_noop("Svi statusi"): None, STATUS_UNPAID: STATUS_UNPAID, STATUS_PAID: STATUS_PAID,
                      tr_noop("Dospelo"): "overdue", STATUS_CANCELLED: STATUS_CANCELLED,
                      STATUS_OPEN: STATUS_OPEN, STATUS_REALIZED: STATUS_REALIZED}

    def __init__(self, db: DatabaseManager, theme: ThemeManager, settings: SettingsManager,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.invoices = InvoiceRepository(db)
        self.partners = PartnerRepository(db)
        self.articles = ArticleRepository(db)
        self.theme = theme
        self.settings = settings
        self._build_ui()
        theme.theme_changed.connect(lambda *_: self.table.viewport().update())

    # -- UI ------------------------------------------------------------------- #
    def _build_ui(self) -> None:
        root = self.root_layout()

        header = PageHeader(tr("Fakture / Računi"),
                            tr("Izlazne i ulazne fakture, predračuni i avansni računi"))
        self.proforma_btn = QPushButton(tr("Novi predračun"))
        self.add_btn = primary(QPushButton(tr("Nova faktura")))
        header.actions.addWidget(self.proforma_btn)
        header.actions.addWidget(self.add_btn)
        root.addWidget(header)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = search_edit(tr("Pretraga po broju, partneru, iznosu, statusu…"))
        self.type_filter = QComboBox()
        fill_combo(self.type_filter, self.TYPE_FILTERS.keys())
        self.status_filter = QComboBox()
        fill_combo(self.status_filter, self.STATUS_FILTERS.keys())
        self.edit_btn = QPushButton(tr("Izmeni"))
        self.paid_btn = QPushButton(tr("Označi plaćeno"))
        self.print_btn = QPushButton(tr("Štampa / PDF"))
        self.storno_btn = QPushButton(tr("Storniraj"))
        self.storno_btn.setToolTip(tr("Poništava izlaznu fakturu storno dokumentom (bez brisanja i rupe u numeraciji)"))
        self.delete_btn = danger(QPushButton(tr("Obriši")))
        self.from_btn = QPushButton(tr("Iz predračuna"))
        self.from_btn.setToolTip(tr("Pravi fakturu ili avansni račun po izabranom predračunu"))
        from_menu = QMenu(self.from_btn)
        from_menu.addAction(tr("Napravi fakturu…"), self.invoice_from_proforma)
        from_menu.addAction(tr("Napravi avansni račun…"), self.advance_from_proforma)
        self.from_btn.setMenu(from_menu)
        menu = QMenu(self.print_btn)
        menu.addAction(tr("Izvezi u PDF…"), self.export_pdf)
        menu.addAction(tr("Štampaj…"), self.print_invoice)
        self.print_btn.setMenu(menu)
        bar.addWidget(self.search)
        bar.addWidget(self.type_filter)
        bar.addWidget(self.status_filter)
        bar.addStretch(1)
        root.addLayout(bar)
        # Radnje u posebnom redu: na nemačkom, francuskom ili mađarskom ne staju pored filtera.
        actions = QHBoxLayout()
        actions.setSpacing(10)
        for b in (self.edit_btn, self.paid_btn, self.from_btn, self.print_btn, self.storno_btn,
                  self.delete_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        root.addLayout(actions)

        self.model = ObjectTableModel(invoice_columns(), self.theme)
        self.proxy = FilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        configure_table(self.table, stretch_column=4)
        self.table.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        root.addWidget(self.table, 1)

        # Zbir za trenutno prikazane (filtrirane) redove
        foot = QHBoxLayout()
        self.footer = QLabel()
        self.footer.setProperty("muted", True)
        self.totals = QLabel()
        foot.addWidget(self.footer, 1)
        foot.addWidget(self.totals)
        root.addLayout(foot)

        self.add_btn.clicked.connect(lambda: self.new_invoice())
        self.proforma_btn.clicked.connect(self.new_proforma)
        self.edit_btn.clicked.connect(self.edit_invoice)
        self.paid_btn.clicked.connect(self.toggle_paid)
        self.storno_btn.clicked.connect(self.storno_invoice)
        self.delete_btn.clicked.connect(self.delete_invoice)
        self.table.doubleClicked.connect(lambda *_: self.edit_invoice())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())
        self.search.textChanged.connect(self._apply_filters)
        self.type_filter.currentTextChanged.connect(self._apply_filters)
        self.status_filter.currentTextChanged.connect(self._apply_filters)
        self._update_buttons()

    # -- podaci ----------------------------------------------------------------#
    def refresh(self) -> None:
        selected = self._selected()
        try:
            self.model.set_rows(self.invoices.list())
        except AppError as exc:
            show_app_error(self, exc)
            return
        self._apply_filters()
        if selected is not None:
            self._select_id(selected.id)

    def _apply_filters(self) -> None:
        kind = self.TYPE_FILTERS[self.type_filter.currentData()]
        status = self.STATUS_FILTERS[self.status_filter.currentData()]
        if kind is None:
            self.proxy.set_predicate("type", None)
        elif kind in (DOC_PROFORMA, DOC_ADVANCE):
            self.proxy.set_predicate("type", lambda i: i.doc_kind == kind)
        else:    # izlazne/ulazne fakture, bez predračuna i avansa
            self.proxy.set_predicate("type", lambda i: i.invoice_type == kind and i.doc_kind == DOC_INVOICE)
        if status is None:
            self.proxy.set_predicate("status", None)
        elif status == "overdue":
            self.proxy.set_predicate("status", lambda i: i.is_overdue())
        else:
            self.proxy.set_predicate("status", lambda i: i.status == status)
        self.proxy.set_search(self.search.text())
        self._update_summary()
        self._update_buttons()

    def _update_summary(self) -> None:
        visible = self.proxy.visible_objects()
        currency = self.settings.get("company.currency", "RSD")
        self.footer.setText(tr("Prikazano {shown} od {total} dokumenata", shown=len(visible),
                               total=self.model.rowCount()))
        # Predračuni nisu promet: sabiraju se samo kad su jedino što je prikazano.
        if any(not i.is_proforma for i in visible):
            visible = [i for i in visible if not i.is_proforma]
        kinds = {i.invoice_type for i in visible}
        if len(kinds) > 1:
            # Izlazne (prihod) i ulazne (rashod) se ne sabiraju zajedno.
            # Avans je već sadržan u konačnoj fakturi, pa se ovde ne sabira posebno.
            out_total = sum((i.total_amount for i in visible
                             if i.invoice_type == INVOICE_OUT and i.doc_kind == DOC_INVOICE), tax.ZERO)
            in_total = sum((i.total_amount for i in visible if i.invoice_type == INVOICE_IN), tax.ZERO)
            self.totals.setText(
                tr("Izlazne: {amount} {currency}", amount=tax.format_money(out_total), currency=currency)
                + "    "
                + tr("Ulazne: {amount} {currency}", amount=tax.format_money(in_total), currency=currency)
            )
            return
        net = sum((i.net_amount for i in visible), tax.ZERO)
        vat = sum((i.vat_amount for i in visible), tax.ZERO)
        total = sum((i.total_amount for i in visible), tax.ZERO)
        self.totals.setText(
            tr("Bez PDV-a: {amount}", amount=tax.format_money(net)) + "    "
            + tr("PDV: {amount}", amount=tax.format_money(vat)) + "    "
            + tr("Ukupno: {amount} {currency}", amount=tax.format_money(total), currency=currency)
        )

    def _selected(self) -> Invoice | None:
        index = self.table.currentIndex()
        if not index.isValid() or not self.table.selectionModel().isRowSelected(index.row()):
            return None
        return self.proxy.source_object(index.row())

    def _select_id(self, invoice_id: int | None) -> None:
        for row in range(self.proxy.rowCount()):
            if self.proxy.source_object(row).id == invoice_id:
                self.table.selectRow(row)
                self.table.scrollTo(self.proxy.index(row, 0))
                return

    def _update_buttons(self) -> None:
        inv = self._selected()
        has = inv is not None
        self.print_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)
        self.edit_btn.setEnabled(has and not inv.is_locked)
        self.paid_btn.setEnabled(has and not inv.is_cancelled and not inv.is_proforma)
        self.storno_btn.setEnabled(has and inv.invoice_type == INVOICE_OUT and not inv.is_locked
                                   and not inv.is_proforma)
        self.from_btn.setEnabled(has and inv.is_proforma and inv.status == STATUS_OPEN)
        self.paid_btn.setText(tr("Vrati na neplaćeno") if has and inv.is_paid else tr("Označi plaćeno"))

    # -- akcije ----------------------------------------------------------------#
    def _open_dialog(self, invoice: Invoice | None) -> InvoiceDialog | None:
        try:
            dialog = InvoiceDialog(
                self.invoices, self.partners, invoice,
                default_vat=str(self.settings.get("defaults.vat_category", "")),
                default_terms=int(self.settings.get("defaults.payment_term_days", 15)),
                parent=self, articles=self.articles, theme=self.theme,
            )
        except AppError as exc:
            show_app_error(self, exc)
            return None
        return dialog if dialog.exec() else None

    def new_invoice(self, invoice: Invoice | None = None) -> None:
        try:
            has_partners = self.partners.count() > 0
        except AppError as exc:
            show_app_error(self, exc)
            return
        if not has_partners:
            show_error(self, tr("Prvo unesite bar jednog partnera u modulu „Partneri“."), tr("Nema partnera"))
            return
        dialog = self._open_dialog(invoice)
        if dialog:
            self.refresh()
            self._select_id(dialog.invoice.id)
            self.status_message.emit(tr("Dokument {number} je sačuvan.", number=dialog.invoice.number))

    def new_proforma(self) -> None:
        self.new_invoice(Invoice(invoice_type=INVOICE_OUT, doc_kind=DOC_PROFORMA))

    def _proforma_draft(self, doc_kind: str, gross=None) -> Invoice | None:
        selected = self._selected()
        if selected is None or not selected.is_proforma:
            return None
        try:
            return self.invoices.draft_from_proforma(selected.id, doc_kind, gross)
        except AppError as exc:
            show_app_error(self, exc)
            return None

    def invoice_from_proforma(self) -> None:
        draft = self._proforma_draft(DOC_INVOICE)
        if draft is not None:
            self.new_invoice(draft)

    def advance_from_proforma(self) -> None:
        selected = self._selected()
        if selected is None or not selected.is_proforma:
            return
        dialog = AdvanceAmountDialog(selected, self)
        if not dialog.exec():
            return
        draft = self._proforma_draft(DOC_ADVANCE, dialog.value())
        if draft is not None:
            self.new_invoice(draft)

    def edit_invoice(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        if selected.is_locked:
            if selected.status == STATUS_REALIZED:
                text = tr_noop("Po predračunu {number} je izdata faktura, pa se on više ne menja.")
            elif selected.is_storno:
                text = tr_noop("Storno dokument {number} se ne može menjati. Za pregled koristite „Štampa / PDF“.")
            elif selected.is_cancelled:
                text = tr_noop("Faktura {number} je stornirana i ne može se menjati. "
                               "Za pregled koristite „Štampa / PDF“.")
            else:
                text = tr_noop("Faktura {number} je plaćena i zato zaključana.\n"
                               "Ako je potrebna izmena, prvo je vratite na neplaćeno.")
            show_info(self, tr(text, number=selected.number), tr("Faktura je zaključana"))
            return
        try:
            invoice = self.invoices.get(selected.id)
        except AppError as exc:
            show_app_error(self, exc)
            return
        if invoice is None:
            self.refresh()
            return
        dialog = self._open_dialog(invoice)
        if dialog:
            self.refresh()
            self._select_id(dialog.invoice.id)
            self.status_message.emit(tr("Dokument {number} je ažuriran.", number=dialog.invoice.number))

    def toggle_paid(self) -> None:
        inv = self._selected()
        if inv is None:
            return
        try:
            if inv.is_paid:
                if not confirm(self, tr("Vraćanje na neplaćeno"),
                               tr("Vratiti fakturu {number} na „neplaćeno“?\n"
                                  "Vezani unos o plaćanju biće uklonjen.", number=inv.number),
                               confirm_text=tr("Vrati")):
                    return
                self.invoices.set_paid(inv.id, False)
                self.status_message.emit(tr("Faktura {number} je vraćena na neplaćeno.", number=inv.number))
            else:
                dialog = PaymentDateDialog(tr("Datum plaćanja fakture {number}:", number=inv.number),
                                           max(date.today(), inv.issue_date), self,
                                           minimum=inv.issue_date)
                if inv.advance_total:
                    dialog.layout().insertWidget(1, QLabel(tr(
                        "Za uplatu (umanjeno za avans): {amount} RSD", amount=tax.format_money(inv.amount_due))))
                if not dialog.exec():
                    return
                self.invoices.set_paid(inv.id, True, dialog.value(), dialog.method())
                text = (tr_noop("Faktura {number} je označena kao plaćena (upisano u blagajnu).")
                        if dialog.method() == PAY_CASH else
                        tr_noop("Faktura {number} je označena kao plaćena (upisano u promet po računu)."))
                self.status_message.emit(tr(text, number=inv.number))
        except AppError as exc:
            show_app_error(self, exc)
            return
        self.refresh()

    def delete_invoice(self) -> None:
        inv = self._selected()
        if inv is None:
            return
        try:
            reason = self.invoices.can_delete(inv)
        except AppError as exc:
            show_app_error(self, exc)
            return
        if reason:
            show_info(self, reason, tr("Brisanje nije moguće"))
            return
        if not confirm(self, tr("Brisanje dokumenta"),
                       tr("Obrisati dokument {number} ({partner})?\nOva radnja se ne može poništiti.",
                          number=inv.number, partner=inv.partner_name),
                       confirm_text=tr("Obriši"), destructive=True):
            return
        try:
            self.invoices.delete(inv.id)
        except AppError as exc:
            show_app_error(self, exc)
            return
        self.refresh()
        self.status_message.emit(tr("Dokument {number} je obrisan.", number=inv.number))

    def storno_invoice(self) -> None:
        inv = self._selected()
        if inv is None:
            return
        try:
            next_no = self.invoices.next_number(INVOICE_OUT, max(date.today(), inv.issue_date).year,
                                                inv.doc_kind)
        except AppError as exc:
            show_app_error(self, exc)
            return
        if not confirm(self, tr("Storno fakture"),
                       tr("Stornirati fakturu {number} ({partner})?\n\nNapraviće se storno dokument "
                          "{storno} sa istim stavkama i negativnim iznosima, a faktura dobija status "
                          "„Stornirano“. Ovo se ne može poništiti.",
                          number=inv.number, partner=inv.partner_name, storno=next_no),
                       confirm_text=tr("Storniraj"), destructive=True):
            return
        try:
            doc_id = self.invoices.storno(inv.id)
        except AppError as exc:
            show_app_error(self, exc)
            return
        self.refresh()
        self._select_id(doc_id)
        self.status_message.emit(tr("Faktura {number} je stornirana (storno dokument {storno}).",
                                    number=inv.number, storno=next_no))

    # -- štampa ------------------------------------------------------------------#
    def _html_for_selected(self) -> tuple[str, str] | None:
        selected = self._selected()
        if selected is None:
            return None
        try:
            invoice = self.invoices.get(selected.id)
            partner = self.partners.get(invoice.partner_id) if invoice else None
        except AppError as exc:
            show_app_error(self, exc)
            return None
        if invoice is None or partner is None:
            show_error(self, tr("Podaci fakture se ne mogu učitati."))
            return None
        html = render_invoice_html(invoice, partner, self.settings.company)
        prefix = {DOC_PROFORMA: tr("predracun"), DOC_ADVANCE: tr("avans")}.get(invoice.doc_kind, tr("faktura"))
        return html, prefix + "_" + invoice.number.replace("/", "-") + ".pdf"

    def export_pdf(self) -> None:
        prepared = self._html_for_selected()
        if prepared is None:
            return
        try:
            path = printing.export_pdf(self, *prepared)
        except AppError as exc:
            show_app_error(self, exc)
            return
        if path:
            self.status_message.emit(tr("PDF sačuvan: {path}", path=path))

    def print_invoice(self) -> None:
        prepared = self._html_for_selected()
        if prepared is None:
            return
        try:
            if printing.print_document(self, prepared[0]):
                self.status_message.emit(tr("Faktura je poslata na štampač."))
        except AppError as exc:
            show_app_error(self, exc)
