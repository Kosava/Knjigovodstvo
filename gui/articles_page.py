"""Modul "Artikli": šifarnik robe i usluga koji se biraju pri unosu fakture."""

from __future__ import annotations

from decimal import Decimal

from PyQt6.QtCore import QLocale, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core import (
    VAT_CATEGORIES,
    VAT_GENERAL,
    ARTICLE_GOODS,
    ARTICLE_KINDS,
    ARTICLE_SERVICE,
    AppError,
    Article,
    ArticleRepository,
    DatabaseManager,
    tax,
)
from i18n import tr, tr_noop
from settings import ThemeManager

from .common import (
    BaseDialog,
    BasePage,
    PageHeader,
    configure_table,
    confirm,
    danger,
    field_hint,
    fill_combo,
    primary,
    search_edit,
    select_data,
    show_app_error,
)
from .table_models import FilterProxyModel, ObjectTableModel, article_columns

#: Uobičajene jedinice mere (može se upisati i bilo koja druga).
UNITS = ["kom", "h", "kg", "m", "m²", "l", "mj", "usluga"]


def rate_key(rate: Decimal) -> str:
    """Ključ stope PDV-a u listi ("20", "10", "0") - nezavisno od jezika i decimalnog zareza."""
    text = f"{Decimal(rate):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


_FIXED = "x:"   # ključ stavke čija stopa ne odgovara nijednoj vrsti na taj dan (npr. stara faktura)


def fill_vat_combo(combo: QComboBox, rates: dict[str, Decimal], category: str | None = None,
                   rate: Decimal | None = None, long: bool = False) -> None:
    """Puni listu vrstama stope sa iznosom na dati dan; ``currentData()`` je vrsta stope.

    Ako je zadat samo broj (stavka postojeće fakture), bira se vrsta sa tim iznosom; broj koji
    ne odgovara nijednoj vrsti ostaje kao posebna stavka, da se stara faktura ne promeni.
    """
    combo.blockSignals(True)
    combo.clear()
    for cat, value in rates.items():
        text = f"{tr(cat)} – {tax.format_quantity(value)} %" if long else f"{tax.format_quantity(value)} %"
        combo.addItem(text, cat)
        combo.setItemData(combo.count() - 1, tr(cat), Qt.ItemDataRole.ToolTipRole)
    if category is None and rate is not None:
        category = next((c for c, v in rates.items() if v == rate), None)
        if category is None:
            combo.addItem(f"{tax.format_quantity(rate)} %", _FIXED + rate_key(rate))
            category = _FIXED + rate_key(rate)
    index = combo.findData(category or VAT_GENERAL)
    combo.setCurrentIndex(max(0, index))
    combo.blockSignals(False)


def combo_rate(combo: QComboBox, rates: dict[str, Decimal]) -> Decimal:
    """Stopa izabrana u listi (iznos vrste na dati dan ili zadržan stari broj)."""
    key = combo.currentData() or VAT_GENERAL
    if key.startswith(_FIXED):
        return tax.to_decimal(key[len(_FIXED):])
    return rates.get(key, tax.DEFAULT_VAT_RATE)


# =========================================================================== #
# Dijalog za unos / izmenu
# =========================================================================== #
class ArticleDialog(BaseDialog):
    def __init__(self, repo: ArticleRepository, article: Article | None = None,
                 default_vat: str = VAT_GENERAL, parent: QWidget | None = None) -> None:
        super().__init__(tr("Novi artikal") if article is None else tr("Izmena artikla"), parent)
        self.repo = repo
        self.article = article or Article(vat_category=default_vat)
        self._rates = repo.vat_rates.rates_on()
        a = self.article

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.name = QLineEdit(a.name)
        self.name.setPlaceholderText(tr("Naziv kako će pisati na fakturi"))
        self.code = QLineEdit(a.code)
        self.code.setMaxLength(30)
        self.code.setPlaceholderText(tr("nije obavezna"))
        self.kind = QComboBox()
        fill_combo(self.kind, ARTICLE_KINDS)
        select_data(self.kind, a.kind)
        self.unit = QComboBox()
        self.unit.setEditable(True)
        self.unit.addItems(UNITS)
        self.unit.setCurrentText(a.unit)
        self.price = QDoubleSpinBox()
        self.price.setDecimals(2)
        self.price.setRange(0, 999_999_999.99)
        self.price.setGroupSeparatorShown(True)
        self.price.setLocale(QLocale())
        self.price.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.price.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.price.setValue(float(a.price))
        self.vat = QComboBox()
        fill_vat_combo(self.vat, self._rates, category=a.vat_category, long=True)
        self.vat.setToolTip(tr("Artikal pamti vrstu stope; iznos se uzima prema datumu prometa fakture."))
        self.gross = field_hint()
        self.active = QCheckBox(tr("Nudi se pri unosu fakture"))
        self.active.setChecked(a.active)
        self.note = QLineEdit(a.note)

        form.addRow(tr("Naziv *"), self.name)
        form.addRow(tr("Šifra"), self.code)
        form.addRow(tr("Vrsta"), self.kind)
        form.addRow(tr("Jedinica mere"), self.unit)
        form.addRow(tr("Cena bez PDV-a"), self.price)
        form.addRow(tr("Stopa PDV-a"), self.vat)
        form.addRow("", self.gross)
        form.addRow(tr("Aktivan"), self.active)
        form.addRow(tr("Napomena"), self.note)
        self.root.addLayout(form)
        self.root.addWidget(field_hint(tr("Polja označena zvezdicom su obavezna.")))
        self.add_buttons()

        self.price.valueChanged.connect(self._update_gross)
        self.vat.currentIndexChanged.connect(self._update_gross)
        self.kind.currentIndexChanged.connect(self._on_kind_changed)
        self._update_gross()
        self.name.setFocus()

    def _on_kind_changed(self) -> None:
        # Za uslugu je najčešća jedinica "usluga" ili "h", za robu "kom".
        if self.unit.currentText() in ("kom", "usluga"):
            self.unit.setCurrentText("usluga" if self.kind.currentData() == ARTICLE_SERVICE else "kom")

    def _values(self) -> tuple[Decimal, Decimal]:
        return Decimal(f"{self.price.value():.2f}"), combo_rate(self.vat, self._rates)

    def _update_gross(self) -> None:
        price, rate = self._values()
        self.gross.setText(tr("Cena sa PDV-om: {amount}",
                              amount=tax.format_money(tax.calc_line(1, price, rate).total)))

    def save(self) -> bool:
        a = self.article
        a.name, a.code, a.kind = self.name.text(), self.code.text(), self.kind.currentData()
        a.unit, a.note, a.active = self.unit.currentText(), self.note.text(), self.active.isChecked()
        a.price, a.vat_rate = self._values()
        a.vat_category = self.vat.currentData()
        if a.id is None:
            self.repo.add(a)
        else:
            self.repo.update(a)
        return True


# =========================================================================== #
# Izbor artikala za fakturu
# =========================================================================== #
class ArticlePickerDialog(QDialog):
    """Pretraga šifarnika; izabrani artikli (jedan ili više) postaju stavke fakture."""

    def __init__(self, articles: list[Article], theme: ThemeManager | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Izbor iz šifarnika"))
        self.resize(820, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        self.search = search_edit(tr("Pretraga po šifri ili nazivu…"))
        layout.addWidget(self.search)
        self.model = ObjectTableModel(article_columns()[:-1], theme)
        self.model.set_rows(articles)
        self.proxy = FilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        configure_table(self.table, stretch_column=1)
        self.table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        layout.addWidget(self.table, 1)
        layout.addWidget(field_hint(tr("Više artikala birate držeći Ctrl; dvoklik dodaje jedan.")))
        box = QDialogButtonBox()
        self.ok = primary(box.addButton(tr("Dodaj u fakturu"), QDialogButtonBox.ButtonRole.AcceptRole))
        box.addButton(tr("Otkaži"), QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.search.textChanged.connect(self._on_search)
        self.table.doubleClicked.connect(lambda *_: self.accept())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_ok())
        if self.proxy.rowCount():
            self.table.selectRow(0)
        self._update_ok()
        self.search.setFocus()

    def _on_search(self, text: str) -> None:
        self.proxy.set_search(text)
        if self.proxy.rowCount():
            self.table.selectRow(0)
        self._update_ok()

    def _update_ok(self) -> None:
        self.ok.setEnabled(bool(self.table.selectionModel().selectedRows()))

    def selected(self) -> list[Article]:
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        return [self.proxy.source_object(r) for r in rows]


# =========================================================================== #
# Stranica
# =========================================================================== #
class ArticlesPage(BasePage):
    KIND_FILTERS = {tr_noop("Svi artikli"): None, tr_noop("Roba"): ARTICLE_GOODS,
                    tr_noop("Usluge"): ARTICLE_SERVICE, tr_noop("Neaktivni"): "inactive"}

    def __init__(self, db: DatabaseManager, theme: ThemeManager, settings=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.repo = ArticleRepository(db)
        self.theme = theme
        self.settings = settings
        self._build_ui()
        theme.theme_changed.connect(lambda *_: self.table.viewport().update())

    def _build_ui(self) -> None:
        root = self.root_layout()
        header = PageHeader(tr("Artikli i usluge"), tr("Šifarnik sa cenama koji se bira pri unosu fakture"))
        self.add_btn = primary(QPushButton(tr("Novi artikal")))
        header.actions.addWidget(self.add_btn)
        root.addWidget(header)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = search_edit(tr("Pretraga po šifri, nazivu, jedinici mere…"))
        self.kind_filter = QComboBox()
        fill_combo(self.kind_filter, self.KIND_FILTERS.keys())
        self.edit_btn = QPushButton(tr("Izmeni"))
        self.delete_btn = danger(QPushButton(tr("Obriši")))
        bar.addWidget(self.search)
        bar.addWidget(self.kind_filter)
        bar.addStretch(1)
        bar.addWidget(self.edit_btn)
        bar.addWidget(self.delete_btn)
        root.addLayout(bar)

        self.model = ObjectTableModel(article_columns(), self.theme)
        self.proxy = FilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        configure_table(self.table, stretch_column=1)
        self.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        root.addWidget(self.table, 1)
        self.footer = QLabel()
        self.footer.setProperty("muted", True)
        root.addWidget(self.footer)

        self.add_btn.clicked.connect(self.add_article)
        self.edit_btn.clicked.connect(self.edit_article)
        self.delete_btn.clicked.connect(self.delete_article)
        self.table.doubleClicked.connect(lambda *_: self.edit_article())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())
        self.search.textChanged.connect(self._apply_filters)
        self.kind_filter.currentTextChanged.connect(self._apply_filters)
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
        kind = self.KIND_FILTERS[self.kind_filter.currentData()]
        if kind is None:
            self.proxy.set_predicate("kind", None)
        elif kind == "inactive":
            self.proxy.set_predicate("kind", lambda a: not a.active)
        else:
            self.proxy.set_predicate("kind", lambda a: a.kind == kind)
        self.proxy.set_search(self.search.text())
        self.footer.setText(tr("Prikazano {shown} od {total} artikala", shown=self.proxy.rowCount(),
                               total=self.model.rowCount()))
        self._update_buttons()

    def _selected(self) -> Article | None:
        index = self.table.currentIndex()
        if not index.isValid() or not self.table.selectionModel().isRowSelected(index.row()):
            return None
        return self.proxy.source_object(index.row())

    def _select_id(self, article_id: int | None) -> None:
        for row in range(self.proxy.rowCount()):
            if self.proxy.source_object(row).id == article_id:
                self.table.selectRow(row)
                self.table.scrollTo(self.proxy.index(row, 0))
                return

    def _update_buttons(self) -> None:
        has = self._selected() is not None
        self.edit_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)

    # -- akcije ----------------------------------------------------------------#
    def _default_vat(self) -> str:
        value = self.settings.get("defaults.vat_category", VAT_GENERAL) if self.settings else VAT_GENERAL
        return value if value in VAT_CATEGORIES else VAT_GENERAL

    def add_article(self) -> None:
        dialog = ArticleDialog(self.repo, default_vat=self._default_vat(), parent=self)
        if dialog.exec():
            self.refresh()
            self._select_id(dialog.article.id)
            self.status_message.emit(tr("Artikal „{name}“ je sačuvan.", name=dialog.article.name))

    def edit_article(self) -> None:
        article = self._selected()
        if article is None:
            return
        try:
            fresh = self.repo.get(article.id) or article
        except AppError as exc:
            show_app_error(self, exc)
            return
        dialog = ArticleDialog(self.repo, fresh, parent=self)
        if dialog.exec():
            self.refresh()
            self._select_id(dialog.article.id)
            self.status_message.emit(tr("Artikal „{name}“ je ažuriran.", name=dialog.article.name))

    def delete_article(self) -> None:
        article = self._selected()
        if article is None:
            return
        if not confirm(self, tr("Brisanje artikla"),
                       tr("Obrisati artikal „{name}“ iz šifarnika?\nStavke na ranijim fakturama ostaju "
                          "kakve su bile. Ako ga samo više ne koristite, možete ga označiti kao neaktivan.",
                          name=article.name),
                       confirm_text=tr("Obriši"), destructive=True):
            return
        try:
            self.repo.delete(article.id)
        except AppError as exc:
            show_app_error(self, exc)
            return
        self.refresh()
        self.status_message.emit(tr("Artikal „{name}“ je obrisan.", name=article.name))


__all__ = ["ArticlesPage", "ArticleDialog", "ArticlePickerDialog", "UNITS", "rate_key", "fill_vat_combo",
           "combo_rate"]
