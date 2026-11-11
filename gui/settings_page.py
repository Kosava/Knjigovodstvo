"""Modul tr("Podešavanja"): tema, podaci o firmi, zadane vrednosti, rezervna kopija."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import shutil

from datetime import date
from decimal import Decimal

from PyQt6.QtCore import QDate, QLocale, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import (VAT_CATEGORIES, VAT_GENERAL, VAT_NONE, AppError, DatabaseManager, PartnerRepository,
                  ValidationError, VatRate, VatRateRepository, ips_qr, tax, validators)
from core.demo_data import seed_demo_data
from settings import ACCENTS, THEMES, SettingsManager, ThemeManager
import i18n
from i18n import tr, tr_noop

from .common import (BasePage, PageHeader, confirm, primary, select_data, set_property, show_app_error,
                     show_info)


class VatRateDialog(QDialog):
    """Nova stopa PDV-a: vrsta, procenat i datum od kada važi."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Nova stopa PDV-a"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        self.category = QComboBox()
        for cat in VAT_CATEGORIES:
            if cat != VAT_NONE:             # "bez obračuna PDV-a" je uvek 0 %
                self.category.addItem(tr(cat), cat)
        self.rate = QDoubleSpinBox()
        self.rate.setDecimals(2)
        self.rate.setRange(0.01, 99.99)
        self.rate.setValue(20)
        self.rate.setSuffix(" %")
        self.rate.setLocale(QLocale())
        self.valid_from = QDateEdit()
        self.valid_from.setCalendarPopup(True)
        self.valid_from.setDisplayFormat(i18n.current().date_qt)
        today = date.today()
        self.valid_from.setDate(QDate(today.year, today.month, today.day))
        form.addRow(tr("Vrsta stope"), self.category)
        form.addRow(tr("Stopa"), self.rate)
        form.addRow(tr("Važi od"), self.valid_from)
        layout.addLayout(form)
        hint = QLabel(tr("Nova stopa važi za fakture čiji je datum prometa od tog dana. "
                         "Ranije fakture se ne menjaju."))
        hint.setWordWrap(True)
        hint.setObjectName("FormHint")
        layout.addWidget(hint)
        box = QDialogButtonBox()
        primary(box.addButton(tr("Dodaj"), QDialogButtonBox.ButtonRole.AcceptRole))
        box.addButton(tr("Otkaži"), QDialogButtonBox.ButtonRole.RejectRole)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def value(self) -> VatRate:
        d = self.valid_from.date()
        return VatRate(category=self.category.currentData(), rate=Decimal(f"{self.rate.value():.2f}"),
                       valid_from=date(d.year(), d.month(), d.day()))


class SettingsPage(BasePage):
    restart_requested = pyqtSignal()
    def __init__(self, db: DatabaseManager, theme: ThemeManager, settings: SettingsManager,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db, self.theme, self.settings = db, theme, settings
        self.vat_rates = VatRateRepository(db)
        self._build_ui()
        self._load_values()
        theme.theme_changed.connect(self._sync_theme_controls)

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        content = QWidget()
        content.setObjectName("Page")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        scroll.setWidget(content)
        wrap = QHBoxLayout(content)
        wrap.setContentsMargins(28, 24, 28, 24)
        column_box = QWidget()                 # uža kolona da polja ne budu razvučena na veliki ekran
        column_box.setMaximumWidth(780)
        column = QVBoxLayout(column_box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(16)
        wrap.addWidget(column_box, 1, Qt.AlignmentFlag.AlignTop)
        wrap.addStretch(1)

        column.addWidget(PageHeader(tr("Podešavanja"), tr("Izgled, podaci o firmi i baza podataka")))

        # -- Izgled ---------------------------------------------------------
        look = QGroupBox(tr("Izgled"))
        form = QFormLayout(look)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        self.theme_combo = QComboBox()
        for key, label in THEMES.items():
            self.theme_combo.addItem(tr(label), key)
        self.accent_combo = QComboBox()
        for key, label in ACCENTS.items():
            self.accent_combo.addItem(tr(label), key)
        self.language_combo = QComboBox()
        for code, lang in i18n.LANGUAGES.items():
            self.language_combo.addItem(lang.name, code)
        form.addRow(tr("Jezik"), self.language_combo)
        form.addRow(tr("Tema"), self.theme_combo)
        form.addRow(tr("Akcentna boja"), self.accent_combo)
        hint = QLabel(tr("Promena se primenjuje odmah i pamti se za sledeće pokretanje."))
        hint.setObjectName("FormHint")
        form.addRow("", hint)
        column.addWidget(look)
        self.theme_combo.activated.connect(self._on_look_changed)
        self.language_combo.activated.connect(self._on_language_changed)
        self.accent_combo.activated.connect(self._on_look_changed)

        # -- Firma ----------------------------------------------------------
        company = QGroupBox(tr("Podaci o firmi (koriste se na štampi i izvozu faktura)"))
        cform = QFormLayout(company)
        cform.setHorizontalSpacing(18)
        cform.setVerticalSpacing(10)
        self.c_name, self.c_pib, self.c_mb = QLineEdit(), QLineEdit(), QLineEdit()
        self.c_address, self.c_city = QLineEdit(), QLineEdit()
        self.c_account, self.c_email, self.c_phone = QLineEdit(), QLineEdit(), QLineEdit()
        self.c_pib.setMaxLength(9)
        self.c_account.setPlaceholderText("160-0000000000000-00")
        for label, w in ((tr_noop("Naziv firme"), self.c_name), (tr_noop("PIB"), self.c_pib),
                         (tr_noop("Matični broj"), self.c_mb), (tr_noop("Adresa"), self.c_address),
                         (tr_noop("Grad"), self.c_city), (tr_noop("Tekući račun"), self.c_account),
                         (tr_noop("E-pošta"), self.c_email), (tr_noop("Telefon"), self.c_phone)):
            cform.addRow(tr(label), w)

        # Logo: kopira se u folder sa podacima, pa ne zavisi od originalnog fajla.
        self._logo_path = ""
        self.logo_preview = QLabel()
        self.logo_preview.setFixedHeight(56)
        self.logo_preview.setMinimumWidth(56)
        self.logo_btn = QPushButton(tr("Izaberi…"))
        self.logo_remove = QPushButton(tr("Ukloni"))
        logo_row = QHBoxLayout()
        logo_row.addWidget(self.logo_preview)
        logo_row.addWidget(self.logo_btn)
        logo_row.addWidget(self.logo_remove)
        logo_row.addStretch(1)
        cform.addRow(tr("Logo"), logo_row)
        self.logo_btn.clicked.connect(self._choose_logo)
        self.logo_remove.clicked.connect(lambda: self._set_logo(""))
        column.addWidget(company)

        # -- Plaćanje (IPS QR) ------------------------------------------------
        pay = QGroupBox(tr("Plaćanje putem IPS QR koda"))
        pform = QFormLayout(pay)
        pform.setHorizontalSpacing(18)
        pform.setVerticalSpacing(10)
        self.qr_check = QCheckBox(tr("Štampaj NBS IPS QR kod na neplaćenim izlaznim fakturama i predračunima"))
        self.pay_code = QComboBox()
        self.pay_code.setEditable(True)
        self.pay_code.setMaxCount(20)
        for code, label in ((tr_noop("221 – promet robe i usluga (međufazna potrošnja)"), "221"),
                            (tr_noop("220 – promet robe i usluga (finalna potrošnja)"), "220"),
                            (tr_noop("289 – transakcije po nalogu građana"), "289")):
            self.pay_code.addItem(tr(code), label)
        self.pay_model = QComboBox()
        self.pay_model.addItem(tr("00 – bez kontrolnog broja (npr. 00 2026-0001)"), ips_qr.MODEL_00)
        self.pay_model.addItem(tr("97 – sa kontrolnim brojem"), ips_qr.MODEL_97)
        qr_hint = QLabel(tr("Za QR kod su potrebni naziv firme, ispravan tekući račun i valuta RSD. "
                            "Kupcima koji su fizička lica uvek se upisuje šifra 289."))
        qr_hint.setObjectName("FormHint")
        qr_hint.setWordWrap(True)
        pform.addRow("", self.qr_check)
        pform.addRow(tr("Šifra plaćanja"), self.pay_code)
        pform.addRow(tr("Model poziva na broj"), self.pay_model)
        pform.addRow("", qr_hint)
        column.addWidget(pay)

        # -- Zadane vrednosti -----------------------------------------------
        defaults = QGroupBox(tr("Podrazumevane vrednosti za nove fakture"))
        dform = QFormLayout(defaults)
        dform.setHorizontalSpacing(18)
        dform.setVerticalSpacing(10)
        self.vat_combo = QComboBox()
        for cat in VAT_CATEGORIES:
            self.vat_combo.addItem(tr(cat), cat)
        self.terms = QSpinBox()
        self.terms.setRange(0, 365)
        self.terms.setSuffix(" " + tr("dana"))
        self.currency = QLineEdit()
        self.currency.setMaxLength(5)
        dform.addRow(tr("Stopa PDV-a"), self.vat_combo)
        dform.addRow(tr("Rok plaćanja"), self.terms)
        dform.addRow(tr("Valuta"), self.currency)
        column.addWidget(defaults)

        # -- Stope PDV-a ------------------------------------------------------
        vat_box = QGroupBox(tr("Stope PDV-a"))
        vl_vat = QVBoxLayout(vat_box)
        vl_vat.setSpacing(10)
        self.vat_table = QTableWidget(0, 3)
        self.vat_table.setHorizontalHeaderLabels([tr("Vrsta stope"), tr("Stopa"), tr("Važi od")])
        self.vat_table.verticalHeader().setVisible(False)
        self.vat_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.vat_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.vat_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.vat_table.setShowGrid(False)
        self.vat_table.setAlternatingRowColors(True)
        self.vat_table.setMinimumHeight(190)
        header = self.vat_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        vl_vat.addWidget(self.vat_table)
        vat_hint = QLabel(tr("Kad država propiše novu stopu, dodajte je sa datumom od kada važi. "
                             "Program na fakturi bira stopu prema datumu prometa, a artikli prate vrstu stope."))
        vat_hint.setObjectName("FormHint")
        vat_hint.setWordWrap(True)
        vl_vat.addWidget(vat_hint)
        vat_row = QHBoxLayout()
        self.vat_add_btn = QPushButton(tr("Nova stopa…"))
        self.vat_del_btn = QPushButton(tr("Obriši stopu"))
        vat_row.addWidget(self.vat_add_btn)
        vat_row.addWidget(self.vat_del_btn)
        vat_row.addStretch(1)
        vl_vat.addLayout(vat_row)
        column.addWidget(vat_box)
        self.vat_add_btn.clicked.connect(self.add_vat_rate)
        self.vat_del_btn.clicked.connect(self.delete_vat_rate)
        self.vat_table.itemSelectionChanged.connect(
            lambda: self.vat_del_btn.setEnabled(bool(self.vat_table.selectedItems())))

        save_row = QHBoxLayout()
        self.save_btn = primary(QPushButton(tr("Sačuvaj podešavanja")))
        save_row.addStretch(1)
        save_row.addWidget(self.save_btn)
        column.addLayout(save_row)
        self.save_btn.clicked.connect(self.save_company_and_defaults)

        # -- Baza -----------------------------------------------------------
        data = QGroupBox(tr("Baza podataka"))
        vl = QVBoxLayout(data)
        vl.setSpacing(10)
        self.db_path = QLabel()
        self.db_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.db_path.setWordWrap(True)
        self.db_path.setProperty("muted", True)
        vl.addWidget(QLabel(tr("Lokacija baze:")))
        vl.addWidget(self.db_path)
        row = QHBoxLayout()
        self.backup_btn = QPushButton(tr("Napravi rezervnu kopiju…"))
        self.restore_btn = QPushButton(tr("Vrati iz rezervne kopije…"))
        self.demo_btn = QPushButton(tr("Učitaj demo podatke"))
        row.addWidget(self.backup_btn)
        row.addWidget(self.restore_btn)
        row.addWidget(self.demo_btn)
        row.addStretch(1)
        vl.addLayout(row)
        column.addWidget(data)
        column.addStretch(1)
        self.backup_btn.clicked.connect(self.backup_database)
        self.restore_btn.clicked.connect(self.restore_database)
        self.demo_btn.clicked.connect(self.load_demo)

    # ------------------------------------------------------------------ #
    def _load_values(self) -> None:
        c = self.settings.company
        self.c_name.setText(c.get("name", ""))
        self.c_pib.setText(c.get("pib", ""))
        self.c_mb.setText(c.get("registration_number", ""))
        self.c_address.setText(c.get("address", ""))
        self.c_city.setText(c.get("city", ""))
        self.c_account.setText(c.get("account", ""))
        self.c_email.setText(c.get("email", ""))
        self.c_phone.setText(c.get("phone", ""))
        self.currency.setText(c.get("currency", "RSD"))
        self._set_logo(c.get("logo", ""), copy=False)
        self.qr_check.setChecked(bool(c.get("ips_qr", True)))
        code = str(c.get("payment_code", ips_qr.DEFAULT_PAYMENT_CODE))
        index = self.pay_code.findData(code)
        if index >= 0:
            self.pay_code.setCurrentIndex(index)
        else:
            self.pay_code.setEditText(code)
        select_data(self.pay_model, c.get("payment_model", ips_qr.MODEL_00))
        select_data(self.vat_combo, self.settings.get("defaults.vat_category", VAT_GENERAL))
        self._load_vat_rates()
        self.terms.setValue(int(self.settings.get("defaults.payment_term_days", 15)))
        self.db_path.setText(str(self.db.path))
        select_data(self.language_combo, self.settings.get("appearance.language", i18n.DEFAULT_LANGUAGE))
        self._sync_theme_controls(self.theme.theme, self.theme.accent)

    def _sync_theme_controls(self, theme: str, accent: str) -> None:
        """Usklađuje padajuće liste kad se tema promeni na drugom mestu (meni, bočna traka)."""
        for combo, key in ((self.theme_combo, theme), (self.accent_combo, accent)):
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(key)))
            combo.blockSignals(False)

    def _on_language_changed(self) -> None:
        """Jezik se primenjuje posle ponovnog pokretanja (svi tekstovi se grade pri pokretanju)."""
        code = self.language_combo.currentData()
        if code == self.settings.get("appearance.language", i18n.DEFAULT_LANGUAGE):
            return
        self.settings.set("appearance.language", code)
        if not self.settings.save():
            show_app_error(self, AppError(tr("Podešavanja nisu sačuvana (proverite dozvole nad folderom).")))
            return
        if confirm(self, tr("Jezik"),
                   tr("Novi jezik će se primeniti posle ponovnog pokretanja programa. "
                      "Pokrenuti program ponovo sada?"),
                   confirm_text=tr("Pokreni ponovo")):
            self.restart_requested.emit()
        else:
            self.status_message.emit(tr("Jezik će biti promenjen pri sledećem pokretanju."))

    def _on_look_changed(self) -> None:
        self.theme.apply(self.theme_combo.currentData(), self.accent_combo.currentData())

    # ------------------------------------------------------------------ #
    def _choose_logo(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, tr("Logo firme"), str(Path.home()),
            tr("Slike (*.png *.jpg *.jpeg *.svg *.gif *.bmp *.webp)"))
        if chosen:
            self._set_logo(chosen)

    def _set_logo(self, path: str, copy: bool = True) -> None:
        """Prikazuje logo; novi fajl kopira u folder sa podacima (čuva se tek na "Sačuvaj")."""
        if path and copy:
            source = Path(path)
            target = self.settings.data_dir / f"logo{source.suffix.lower()}"
            try:
                if source.stat().st_size > 3 * 1024 * 1024:
                    raise OSError(tr("fajl je veći od 3 MB"))
                self.settings.data_dir.mkdir(parents=True, exist_ok=True)
                if source.resolve() != target.resolve():
                    shutil.copyfile(source, target)
                path = str(target)
            except OSError as exc:
                show_app_error(self, AppError(tr("Logo nije učitan: {error}", error=exc)))
                return
        pixmap = QPixmap(path) if path else QPixmap()
        if path and pixmap.isNull():
            if copy:
                show_app_error(self, AppError(tr("Izabrani fajl nije slika koju program može da prikaže.")))
            path = ""
        self._logo_path = path
        self.logo_preview.setPixmap(
            pixmap.scaledToHeight(56, Qt.TransformationMode.SmoothTransformation) if path else QPixmap())
        self.logo_preview.setText("" if path else tr("bez logoa"))
        self.logo_remove.setEnabled(bool(path))

    # -- stope PDV-a ------------------------------------------------------ #
    def refresh(self) -> None:
        self._load_vat_rates()

    def _load_vat_rates(self) -> None:
        try:
            rows = self.vat_rates.list()
        except AppError as exc:
            show_app_error(self, exc)
            return
        today = date.today()
        self.vat_table.setRowCount(0)
        seen: set[str] = set()
        for r in rows:
            row = self.vat_table.rowCount()
            self.vat_table.insertRow(row)
            # Stopa koja važi danas je podebljana; buduće su označene u napomeni.
            active = r.category not in seen and r.valid_from <= today
            if r.valid_from <= today:
                seen.add(r.category)
            cells = [tr(r.category), f"{tax.format_quantity(r.rate)} %", tax.format_date(r.valid_from)]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, r.id)
                if col:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if active:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setToolTip(tr("Važi danas"))
                elif r.valid_from > today:
                    item.setToolTip(tr("Počinje da važi {date}", date=tax.format_date(r.valid_from)))
                self.vat_table.setItem(row, col, item)
        self.vat_del_btn.setEnabled(False)

    def add_vat_rate(self) -> None:
        dialog = VatRateDialog(self)
        if not dialog.exec():
            return
        rate = dialog.value()
        try:
            self.vat_rates.add(rate)
        except AppError as exc:
            show_app_error(self, exc)
            return
        self._load_vat_rates()
        self.status_message.emit(tr("Dodata stopa {rate} % ({category}) od {date}.",
                                    rate=tax.format_quantity(rate.rate), category=tr(rate.category),
                                    date=tax.format_date(rate.valid_from)))

    def delete_vat_rate(self) -> None:
        items = self.vat_table.selectedItems()
        if not items:
            return
        row = items[0].row()
        rate_id = items[0].data(Qt.ItemDataRole.UserRole)
        text = " ".join(self.vat_table.item(row, c).text() for c in range(3))
        if not confirm(self, tr("Brisanje stope PDV-a"),
                       tr("Obrisati stopu „{rate}“?\nVeć sačuvane fakture se ne menjaju.", rate=text),
                       confirm_text=tr("Obriši"), destructive=True):
            return
        try:
            self.vat_rates.delete(rate_id)
        except AppError as exc:
            show_app_error(self, exc)
            return
        self._load_vat_rates()

    def _mark(self, edit: QLineEdit, bad: bool) -> None:
        set_property(edit, "invalid", bad)

    def save_company_and_defaults(self) -> None:
        errors: list[str] = []
        pib, account, email = self.c_pib.text().strip(), self.c_account.text().strip(), self.c_email.text().strip()
        bad_pib = bool(pib) and not validators.is_valid_pib(pib)
        bad_acc = bool(account) and not validators.is_valid_account(account)
        bad_mail = bool(email) and not validators.is_valid_email(email)
        self._mark(self.c_pib, bad_pib)
        self._mark(self.c_account, bad_acc)
        self._mark(self.c_email, bad_mail)
        if bad_pib:
            errors.append(tr("PIB firme nije ispravan."))
        if bad_acc:
            errors.append(tr("Tekući račun firme nije ispravan."))
        if bad_mail:
            errors.append(tr("E-adresa firme nije ispravna."))
        code = (self.pay_code.currentData() if self.pay_code.currentText() == self.pay_code.itemText(
            self.pay_code.currentIndex()) else None) or self.pay_code.currentText().strip()[:3]
        if not (len(code) == 3 and code.isdigit() and code[0] in "12"):
            errors.append(tr("Šifra plaćanja mora imati tri cifre i počinjati sa 1 ili 2."))
        if errors:
            show_app_error(self, ValidationError(errors))
            return

        self.settings.update_section("company", {
            "name": self.c_name.text().strip(), "pib": pib,
            "registration_number": self.c_mb.text().strip(),
            "address": self.c_address.text().strip(), "city": self.c_city.text().strip(),
            "account": validators.normalize_account(account), "email": email,
            "phone": self.c_phone.text().strip(),
            "currency": self.currency.text().strip() or "RSD",
            "logo": self._logo_path, "ips_qr": self.qr_check.isChecked(),
            "payment_code": code, "payment_model": self.pay_model.currentData(),
        })
        self.settings.set("defaults.vat_category", self.vat_combo.currentData())
        self.settings.set("defaults.payment_term_days", self.terms.value())
        if self.settings.save():
            self.status_message.emit(tr("Podešavanja su sačuvana."))
        else:
            show_app_error(self, AppError(tr("Podešavanja nisu sačuvana (proverite dozvole nad folderom).")))

    def backup_database(self) -> None:
        suggestion = Path.home() / f"racunovodstvo_{datetime.now():%Y%m%d_%H%M}.db"
        chosen, _ = QFileDialog.getSaveFileName(self, tr("Rezervna kopija baze"), str(suggestion),
                                                tr("SQLite baza (*.db)"))
        if not chosen:
            return
        try:
            path = self.db.backup(chosen)
        except AppError as exc:
            show_app_error(self, exc)
            return
        self.status_message.emit(tr("Rezervna kopija sačuvana: {path}", path=path))
        show_info(self, tr("Rezervna kopija je sačuvana:\n{path}", path=path), tr("Gotovo"))

    def restore_database(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, tr("Vrati iz rezervne kopije"), str(Path.home()),
                                                tr("SQLite baza (*.db);;Svi fajlovi (*)"))
        if not chosen:
            return
        try:
            self.db.check_backup_file(chosen)
        except AppError as exc:
            show_app_error(self, exc)
            return
        if not confirm(self, tr("Vraćanje rezervne kopije"),
                       tr("Zameniti sve trenutne podatke sadržajem fajla\n{path}?\n\n"
                          "Trenutna baza se prvo čuva kao posebna kopija pored baze, pa se ovo "
                          "može poništiti vraćanjem te kopije.", path=chosen),
                       confirm_text=tr("Vrati kopiju"), destructive=True):
            return
        try:
            safety = self.db.restore(chosen)
        except AppError as exc:
            show_app_error(self, exc)
            return
        self._load_vat_rates()
        self.status_message.emit(tr("Rezervna kopija je vraćena."))
        show_info(self, tr("Podaci su vraćeni iz:\n{source}\n\nPrethodno stanje je sačuvano u:\n{safety}",
                           source=chosen, safety=safety), tr("Gotovo"))

    def load_demo(self) -> None:
        if PartnerRepository(self.db).count() > 0:
            show_info(self, tr("Demo podaci se mogu učitati samo u praznu bazu.\n"
                               "Napravite novu bazu ili obrišite postojeće partnere."), tr("Baza nije prazna"))
            return
        if not confirm(self, tr("Demo podaci"), tr("Učitati izmišljene partnere, fakture i unose u blagajnu?"),
                       confirm_text=tr("Učitaj")):
            return
        try:
            seed_demo_data(self.db)
        except AppError as exc:
            show_app_error(self, exc)
            return
        self.status_message.emit(tr("Demo podaci su učitani."))
