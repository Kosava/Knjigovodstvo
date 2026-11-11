"""Modul tr("Partneri"): kupci i dobavljači (tabela, pretraga, forma za unos)."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QWidget,
)

from core import (
    ENTITY_FOREIGN,
    ENTITY_LEGAL,
    ENTITY_PERSON,
    ENTITY_TYPES,
    PARTNER_BOTH,
    PARTNER_CUSTOMER,
    PARTNER_SUPPLIER,
    PARTNER_TYPES,
    AppError,
    DatabaseManager,
    Partner,
    PartnerRepository,
)
from core import validators
from settings import ThemeManager
from i18n import tr, tr_noop

from .common import (
    fill_combo,
    select_data,
    BaseDialog,
    BasePage,
    PageHeader,
    confirm,
    configure_table,
    danger,
    field_hint,
    primary,
    search_edit,
    set_property,
    show_app_error,
)
from .table_models import FilterProxyModel, ObjectTableModel, partner_columns


# =========================================================================== #
# Dijalog
# =========================================================================== #
class PartnerDialog(BaseDialog):
    """Forma za unos / izmenu partnera; upis radi sama pri "Sačuvaj"."""

    def __init__(self, repo: PartnerRepository, partner: Partner | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(tr("Novi partner") if partner is None else tr("Izmena partnera"), parent)
        self.repo = repo
        self.partner = partner or Partner()

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.name = QLineEdit(self.partner.name)
        self.name.setPlaceholderText(tr("Pun naziv firme ili ime i prezime"))
        self.entity = QComboBox()
        fill_combo(self.entity, ENTITY_TYPES)
        select_data(self.entity, self.partner.entity_type)
        self.type = QComboBox()
        fill_combo(self.type, PARTNER_TYPES)
        select_data(self.type, self.partner.partner_type)

        self.pib = QLineEdit(self.partner.pib)
        self.pib.setMaxLength(9)
        self.pib.setPlaceholderText(tr("9 cifara"))
        self.pib_hint = field_hint()

        self.address = QLineEdit(self.partner.address)
        self.city = QLineEdit(self.partner.city)
        self.account = QLineEdit(self.partner.account)
        self.account.setPlaceholderText("160-0000000000000-00")
        self.account_hint = field_hint()
        self.email = QLineEdit(self.partner.email)
        self.phone = QLineEdit(self.partner.phone)

        form.addRow(tr("Naziv *"), self.name)
        form.addRow(tr("Vrsta"), self.entity)
        form.addRow(tr("Tip"), self.type)
        form.addRow(tr("PIB *"), self.pib)
        self._form = form
        form.addRow("", self.pib_hint)
        form.addRow(tr("Adresa"), self.address)
        form.addRow(tr("Grad"), self.city)
        form.addRow(tr("Tekući račun"), self.account)
        form.addRow("", self.account_hint)
        form.addRow(tr("E-pošta"), self.email)
        form.addRow(tr("Telefon"), self.phone)
        self.root.addLayout(form)
        self.root.addWidget(field_hint(tr("Polja označena zvezdicom su obavezna.")))
        self.add_buttons()

        # Provera u realnom vremenu dok korisnik kuca
        self.pib.textChanged.connect(self._check_pib)
        self.account.textChanged.connect(self._check_account)
        self.entity.currentTextChanged.connect(self._on_entity_changed)
        self._on_entity_changed()
        self._check_pib()
        self._check_account()
        self.name.setFocus()

    def _mark(self, edit: QLineEdit, hint: QLabel, ok: bool | None, ok_text: str, bad_text: str) -> None:
        """ok=None -> neutralno (prazno polje)."""
        set_property(edit, "invalid", ok is False)
        hint.setText("" if ok is None else (ok_text if ok else bad_text))
        hint.setObjectName("FormHint" if ok is not False else "FormError")
        hint.style().unpolish(hint)
        hint.style().polish(hint)

    def _on_entity_changed(self) -> None:
        """PIB je obavezan za domaće pravno lice, fizičko lice ga nema, strani je slobodan unos."""
        kind = self.entity.currentData()
        label = self._form.labelForField(self.pib)
        person = kind == ENTITY_PERSON
        self.pib.setVisible(not person)
        self.pib_hint.setVisible(not person)
        if label is not None:
            label.setVisible(not person)
            label.setText(tr("Poreski broj") if kind == ENTITY_FOREIGN else tr("PIB *"))
        if kind == ENTITY_FOREIGN:
            self.pib.setMaxLength(30)
            self.pib.setPlaceholderText(tr("strani poreski / PDV broj (nije obavezan)"))
            self.account.setPlaceholderText(tr("IBAN ili broj računa"))
        else:
            self.pib.setMaxLength(9)
            self.pib.setPlaceholderText(tr("9 cifara"))
            self.account.setPlaceholderText("160-0000000000000-00")
        self._check_pib()
        self._check_account()

    def _check_pib(self) -> None:
        text = self.pib.text().strip()
        if self.entity.currentData() != ENTITY_LEGAL:
            self._mark(self.pib, self.pib_hint, None, "", "")
        elif not text:
            self._mark(self.pib, self.pib_hint, None, "", "")
        elif validators.is_valid_pib(text):
            self._mark(self.pib, self.pib_hint, True, tr("✓ PIB je ispravan"), "")
        else:
            self._mark(self.pib, self.pib_hint, False, "", tr("PIB nije ispravan (9 cifara, ispravna kontrolna cifra)"))

    def _check_account(self) -> None:
        text = self.account.text().strip()
        if not text or self.entity.currentData() == ENTITY_FOREIGN:
            self._mark(self.account, self.account_hint, None, "", "")
        elif validators.is_valid_account(text):
            self._mark(self.account, self.account_hint, True, tr("✓ Račun je ispravan"), "")
        else:
            self._mark(self.account, self.account_hint, False, "", tr("Račun nije ispravan (oblik 160-0000000000000-00)"))

    def save(self) -> bool:
        p = self.partner
        p.name, p.partner_type = self.name.text(), self.type.currentData()
        p.entity_type = self.entity.currentData()
        p.pib, p.address, p.city = self.pib.text(), self.address.text(), self.city.text()
        p.account, p.email, p.phone = self.account.text(), self.email.text(), self.phone.text()
        if p.id is None:
            self.repo.add(p)
        else:
            self.repo.update(p)
        return True


# =========================================================================== #
# Stranica
# =========================================================================== #
class PartnersPage(BasePage):
    TYPE_FILTERS = {
        tr_noop("Svi partneri"): None,
        tr_noop("Kupci"): (PARTNER_CUSTOMER, PARTNER_BOTH),
        tr_noop("Dobavljači"): (PARTNER_SUPPLIER, PARTNER_BOTH),
    }

    def __init__(self, db: DatabaseManager, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = PartnerRepository(db)
        self.theme = theme
        self._build_ui()
        theme.theme_changed.connect(lambda *_: self.table.viewport().update())

    # -- UI ------------------------------------------------------------------- #
    def _build_ui(self) -> None:
        root = self.root_layout()

        header = PageHeader(tr("Partneri"), tr("Kupci i dobavljači"))
        self.add_btn = primary(QPushButton(tr("Novi partner")))
        header.actions.addWidget(self.add_btn)
        root.addWidget(header)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = search_edit(tr("Pretraga po nazivu, PIB-u, gradu, računu, vrsti…"))
        self.type_filter = QComboBox()
        fill_combo(self.type_filter, self.TYPE_FILTERS.keys())
        self.edit_btn = QPushButton(tr("Izmeni"))
        self.delete_btn = danger(QPushButton(tr("Obriši")))
        bar.addWidget(self.search)
        bar.addWidget(self.type_filter)
        bar.addStretch(1)
        bar.addWidget(self.edit_btn)
        bar.addWidget(self.delete_btn)
        root.addLayout(bar)

        self.model = ObjectTableModel(partner_columns(), self.theme)
        self.proxy = FilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        configure_table(self.table, stretch_column=None)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        root.addWidget(self.table, 1)

        self.footer = QLabel()
        self.footer.setProperty("muted", True)
        root.addWidget(self.footer)

        # signali
        self.add_btn.clicked.connect(self.add_partner)
        self.edit_btn.clicked.connect(self.edit_partner)
        self.delete_btn.clicked.connect(self.delete_partner)
        self.table.doubleClicked.connect(lambda *_: self.edit_partner())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())
        self.search.textChanged.connect(self._apply_filters)
        self.type_filter.currentTextChanged.connect(self._apply_filters)
        self._update_buttons()

    # -- podaci ----------------------------------------------------------------#
    def refresh(self) -> None:
        selected = self._selected()
        try:
            self.model.set_rows(self.repo.list())
        except AppError as exc:
            show_app_error(self, exc)
            return
        self._apply_filters()
        if selected is not None:
            self._select_id(selected.id)

    def _apply_filters(self) -> None:
        allowed = self.TYPE_FILTERS[self.type_filter.currentData()]
        self.proxy.set_predicate("type", None if allowed is None else (lambda p: p.partner_type in allowed))
        self.proxy.set_search(self.search.text())
        total = self.model.rowCount()
        self.footer.setText(tr("Prikazano {shown} od {total} partnera", shown=self.proxy.rowCount(), total=total))
        self._update_buttons()

    def _selected(self) -> Partner | None:
        index = self.table.currentIndex()
        if not index.isValid() or not self.table.selectionModel().isRowSelected(index.row()):
            return None
        return self.proxy.source_object(index.row())

    def _select_id(self, partner_id: int | None) -> None:
        for row in range(self.proxy.rowCount()):
            if self.proxy.source_object(row).id == partner_id:
                self.table.selectRow(row)
                self.table.scrollTo(self.proxy.index(row, 0))
                return

    def _update_buttons(self) -> None:
        has = self._selected() is not None
        self.edit_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)

    # -- akcije ----------------------------------------------------------------#
    def add_partner(self) -> None:
        dialog = PartnerDialog(self.repo, parent=self)
        if dialog.exec():
            self.refresh()
            self._select_id(dialog.partner.id)
            self.status_message.emit(tr("Partner „{name}“ je sačuvan.", name=dialog.partner.name))

    def edit_partner(self) -> None:
        partner = self._selected()
        if partner is None:
            return
        fresh = self.repo.get(partner.id) or partner
        dialog = PartnerDialog(self.repo, fresh, parent=self)
        if dialog.exec():
            self.refresh()
            self._select_id(dialog.partner.id)
            self.status_message.emit(tr("Partner „{name}“ je ažuriran.", name=dialog.partner.name))

    def delete_partner(self) -> None:
        partner = self._selected()
        if partner is None:
            return
        if not confirm(self, tr("Brisanje partnera"), tr("Obrisati partnera „{name}“?", name=partner.name),
                       confirm_text=tr("Obriši"), destructive=True):
            return
        try:
            self.repo.delete(partner.id)
        except AppError as exc:
            show_app_error(self, exc)
            return
        self.refresh()
        self.status_message.emit(tr("Partner „{name}“ je obrisan.", name=partner.name))
