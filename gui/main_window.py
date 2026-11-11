"""Glavni prozor: bočna navigacija, stranice, meni, statusna traka."""

from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import QDateTime, QProcess, QRect, QTimer, Qt
from PyQt6.QtGui import QAction, QActionGroup, QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import AppError, DatabaseManager, PartnerRepository
from core.demo_data import seed_demo_data
import i18n
from i18n import tr, tr_noop
from settings import ACCENTS, THEMES, SettingsManager, ThemeManager

from .articles_page import ArticlesPage
from .cash_page import CashPage
from .common import BasePage, confirm, show_app_error
from .dashboard_page import DashboardPage
from .icons import nav_icon
from .invoices_page import InvoicesPage
from .partners_page import PartnersPage
from .settings_page import SettingsPage

log = logging.getLogger(__name__)
APP_TITLE = tr_noop("Računovodstvo")
THEME_ACTIONS = {"light": tr_noop("Svetla tema"), "dark": tr_noop("Tamna tema")}
APP_VERSION = "1.0"


class MainWindow(QMainWindow):
    NAV = [("dashboard", tr_noop("Pregled")), ("partners", tr_noop("Partneri")),
           ("articles", tr_noop("Artikli")), ("invoices", tr_noop("Fakture")),
           ("cash", tr_noop("Blagajna")), ("settings", tr_noop("Podešavanja"))]

    def __init__(self, db: DatabaseManager, settings: SettingsManager, theme: ThemeManager) -> None:
        super().__init__()
        self.db, self.settings, self.theme = db, settings, theme
        self.setWindowTitle(tr("{app} – evidencija poslovanja", app=tr(APP_TITLE)))
        self.setMinimumSize(1000, 640)

        self.dashboard_page = DashboardPage(db, theme, settings)
        self.partners_page = PartnersPage(db, theme)
        self.articles_page = ArticlesPage(db, theme, settings)
        self.invoices_page = InvoicesPage(db, theme, settings)
        self.cash_page = CashPage(db, theme, settings)
        self.settings_page = SettingsPage(db, theme, settings)
        # Isti redosled kao u NAV
        self.pages: list[BasePage] = [self.dashboard_page, self.partners_page, self.articles_page,
                                      self.invoices_page, self.cash_page, self.settings_page]
        self._build_ui()
        self._build_menu()
        self._build_status_bar()
        self._restore_window_state()

        theme.theme_changed.connect(self._on_theme_changed)
        self._on_theme_changed(theme.theme, theme.accent, persist=False)

        for page in self.pages:
            page.status_message.connect(lambda text: self.statusBar().showMessage(text, 6000))
        self.stack.currentChanged.connect(self._on_page_changed)
        self.settings_page.restart_requested.connect(self.restart)
        keys = [key for key, _ in self.NAV]
        last = settings.get("window.last_page_key")
        self._go_to(keys.index(last) if last in keys else 0)

    # ------------------------------------------------------------------ #
    # Gradnja
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(224)
        col = QVBoxLayout(sidebar)
        col.setContentsMargins(14, 20, 14, 14)
        col.setSpacing(4)
        brand = QLabel(tr(APP_TITLE))
        brand.setObjectName("Brand")
        sub = QLabel(tr("Evidencija poslovanja"))
        sub.setObjectName("BrandSub")
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        logo = QLabel()
        app_icon = QApplication.windowIcon()
        if not app_icon.isNull():
            logo.setPixmap(app_icon.pixmap(36, 36))
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand_text.addWidget(brand)
        brand_text.addWidget(sub)
        brand_row.addWidget(logo)
        brand_row.addLayout(brand_text, 1)
        col.addLayout(brand_row)
        col.addSpacing(18)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        for index, (_key, label) in enumerate(self.NAV):
            btn = QPushButton(f"  {tr(label)}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"{tr(label)}  (Ctrl+{index + 1})")
            self.nav_group.addButton(btn, index)
            self.nav_buttons.append(btn)
            col.addWidget(btn)
        self.nav_group.idClicked.connect(self._go_to)
        col.addStretch(1)

        self.theme_toggle = QPushButton()
        self.theme_toggle.setObjectName("ThemeToggle")
        self.theme_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_toggle.clicked.connect(self.theme.toggle)
        col.addWidget(self.theme_toggle)
        version = QLabel(tr("Verzija {version}", version=APP_VERSION))
        version.setObjectName("BrandSub")
        col.addWidget(version)

        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(page)

        row.addWidget(sidebar)
        row.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        for i in range(len(self.NAV)):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self._go_to(i))

    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu(tr("&Datoteka"))
        act_invoice = QAction(tr("Nova faktura"), self, shortcut=QKeySequence("Ctrl+N"))
        act_invoice.triggered.connect(lambda: self._open(self.invoices_page).new_invoice())
        act_proforma = QAction(tr("Novi predračun"), self, shortcut=QKeySequence("Ctrl+Alt+N"))
        act_proforma.triggered.connect(lambda: self._open(self.invoices_page).new_proforma())
        act_partner = QAction(tr("Novi partner"), self, shortcut=QKeySequence("Ctrl+Shift+N"))
        act_partner.triggered.connect(lambda: self._open(self.partners_page).add_partner())
        act_article = QAction(tr("Novi artikal"), self)
        act_article.triggered.connect(lambda: self._open(self.articles_page).add_article())
        act_backup = QAction(tr("Rezervna kopija baze…"), self)
        act_backup.triggered.connect(lambda: self._open(self.settings_page).backup_database())
        act_quit = QAction(tr("Izlaz"), self, shortcut=QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        for a in (act_invoice, act_proforma, act_partner, act_article):
            file_menu.addAction(a)
        file_menu.addSeparator()
        file_menu.addAction(act_backup)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        view_menu = bar.addMenu(tr("&Prikaz"))
        theme_menu = view_menu.addMenu(tr("Tema"))
        self._theme_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        for key in THEMES:
            act = QAction(tr(THEME_ACTIONS[key]), self, checkable=True)
            act.triggered.connect(lambda _=False, k=key: self.theme.apply(k))
            group.addAction(act)
            theme_menu.addAction(act)
            self._theme_actions[key] = act
        accent_menu = view_menu.addMenu(tr("Akcentna boja"))
        self._accent_actions: dict[str, QAction] = {}
        agroup = QActionGroup(self)
        for key, label in ACCENTS.items():
            act = QAction(tr(label), self, checkable=True)
            act.triggered.connect(lambda _=False, k=key: self.theme.apply(self.theme.theme, k))
            agroup.addAction(act)
            accent_menu.addAction(act)
            self._accent_actions[key] = act
        toggle = QAction(tr("Zameni temu"), self, shortcut=QKeySequence("Ctrl+T"))
        toggle.triggered.connect(self.theme.toggle)
        view_menu.addAction(toggle)

        help_menu = bar.addMenu(tr("&Pomoć"))
        about = QAction(tr("O programu"), self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    def _build_status_bar(self) -> None:
        status = self.statusBar()
        status.setSizeGripEnabled(False)
        self.db_label = QLabel(tr("Baza: {status}", status=self.db.status_text))
        self.theme_label = QLabel()
        self.clock_label = QLabel()
        status.addPermanentWidget(self.db_label)
        status.addPermanentWidget(self.theme_label)
        status.addPermanentWidget(self.clock_label)
        self._tick()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _tick(self) -> None:
        self.clock_label.setText(QDateTime.currentDateTime().toString(i18n.current().date_qt + "  HH:mm:ss"))

    # ------------------------------------------------------------------ #
    # Navigacija i tema
    # ------------------------------------------------------------------ #
    def _open(self, page: BasePage) -> BasePage:
        self._go_to(self.pages.index(page))
        return page

    def _go_to(self, index: int) -> None:
        index = index if 0 <= index < len(self.pages) else 0
        self.nav_buttons[index].setChecked(True)
        if self.stack.currentIndex() != index:
            self.stack.setCurrentIndex(index)
        else:
            self._on_page_changed(index)

    def _on_page_changed(self, index: int) -> None:
        try:
            self.pages[index].refresh()
        except AppError as exc:
            show_app_error(self, exc)

    def _on_theme_changed(self, theme: str, accent: str, persist: bool = True) -> None:
        c = self.theme.colors()
        for btn, (key, _label) in zip(self.nav_buttons, self.NAV):
            btn.setIcon(nav_icon(key, c["sidebar_text"], c["sel_text"]))
        dark = theme == "dark"
        self.theme_toggle.setText(tr(THEME_ACTIONS["light" if dark else "dark"]))
        self.theme_label.setText(tr("Tema: {name}", name=tr(THEMES[theme])))
        self._theme_actions[theme].setChecked(True)
        self._accent_actions[accent].setChecked(True)
        if persist:
            self.settings.set("appearance.theme", theme)
            self.settings.set("appearance.accent", accent)
            self.settings.save()

    def _show_about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(tr("O programu"))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<b>{tr(APP_TITLE)}</b> {APP_VERSION}<br>"
                    + tr("Knjigovodstveno-računovodstvena evidencija poslovanja: partneri, "
                         "artikli, fakture, predračuni, avansi i blagajna.")
                    + "<br><br>Python · PyQt6 · SQLite")
        app_icon = QApplication.windowIcon()
        if not app_icon.isNull():
            box.setIconPixmap(app_icon.pixmap(64, 64))
        box.addButton(tr("U redu"), QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    # ------------------------------------------------------------------ #
    # Prvo pokretanje, stanje prozora
    # ------------------------------------------------------------------ #
    def offer_demo_data(self) -> None:
        """Pri prvom pokretanju nudi demo podatke (samo ako je baza prazna)."""
        if not self.settings.get("first_run", True):
            return
        self.settings.set("first_run", False)
        self.settings.save()
        try:
            if PartnerRepository(self.db).count() > 0:
                return
            if confirm(self, tr("Dobrodošli"),
                       tr("Baza je prazna. Želite li da učitate demo podatke (izmišljeni partneri, "
                          "fakture i blagajna) da isprobate program?\n\nMožete ih i kasnije učitati "
                          "u Podešavanjima."),
                       confirm_text=tr("Učitaj demo podatke")):
                seed_demo_data(self.db)
                self._on_page_changed(self.stack.currentIndex())
        except AppError as exc:
            show_app_error(self, exc)

    def _restore_window_state(self) -> None:
        w = int(self.settings.get("window.width", 1280))
        h = int(self.settings.get("window.height", 800))
        self.resize(max(w, 1000), max(h, 640))
        x, y = self.settings.get("window.x"), self.settings.get("window.y")
        if isinstance(x, int) and isinstance(y, int):
            rect = QRect(x, y, self.width(), self.height())
            if any(s.availableGeometry().intersects(rect) for s in QGuiApplication.screens()):
                self.move(x, y)
        self._start_maximized = bool(self.settings.get("window.maximized", False))

    def show_restored(self) -> None:
        self.showMaximized() if self._start_maximized else self.show()

    def restart(self) -> None:
        """Ponovo pokreće program (npr. posle promene jezika); stanje se čuva pri zatvaranju."""
        self.close()
        if not self.isVisible():
            QProcess.startDetached(sys.executable, sys.argv)

    def closeEvent(self, event: QCloseEvent) -> None:
        try:
            geo = self.normalGeometry() if self.isMaximized() else self.geometry()
            self.settings.set("window.width", geo.width())
            self.settings.set("window.height", geo.height())
            self.settings.set("window.x", geo.x())
            self.settings.set("window.y", geo.y())
            self.settings.set("window.maximized", self.isMaximized())
            self.settings.set("window.last_page_key", self.NAV[self.stack.currentIndex()][0])
            self.settings.save()
        except Exception:   # zatvaranje se nikad ne sme blokirati greškom u podešavanjima
            log.exception("Stanje prozora nije sačuvano")
        self.db.close()
        event.accept()
