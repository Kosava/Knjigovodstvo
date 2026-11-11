#!/usr/bin/env python3
"""Ulazna tačka aplikacije "Računovodstvo".

Pokretanje:  python main.py
"""

from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PyQt6.QtCore import QLibraryInfo, QLocale, QTimer, QTranslator
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

import i18n
from core import AppError, DatabaseManager
from gui import MainWindow
from i18n import tr
from settings import SettingsManager, ThemeManager

ICON_PATH = Path(__file__).resolve().parent / "assets" / "icon.svg"


def setup_logging(data_dir) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(data_dir / "racunovodstvo.log", maxBytes=500_000,
                                  backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def install_exception_hook() -> None:
    """Neuhvaćena greška se upisuje u log i prikazuje korisniku umesto tihog rušenja."""
    def hook(exc_type, exc, tb):
        logging.getLogger("uncaught").error("Neuhvaćena greška", exc_info=(exc_type, exc, tb))
        if QApplication.instance() is not None:
            QMessageBox.critical(None, tr("Neočekivana greška"),
                                 "".join(traceback.format_exception_only(exc_type, exc)).strip()
                                 + "\n\n" + tr("Detalji su upisani u log fajl."))
    sys.excepthook = hook


def apply_language(app: QApplication, code: str) -> None:
    """Postavlja jezik programa, QLocale (datumi, brojevi, kalendar) i Qt prevode dijaloga."""
    i18n.set_language(code)
    lang = i18n.current()
    script = getattr(QLocale.Script, lang.qt_script) if lang.qt_script else QLocale.Script.AnyScript
    locale = QLocale(getattr(QLocale.Language, lang.qt_language), script,
                     getattr(QLocale.Country, lang.qt_country))
    QLocale.setDefault(locale)
    # Standardna Qt dugmad u sistemskim dijalozima; ako prevoda nema, ostaje engleski.
    translator = QTranslator(app)
    if translator.load(locale, "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Racunovodstvo")
    app.setOrganizationName("Racunovodstvo")
    app.setDesktopFileName("racunovodstvo")
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    settings = SettingsManager()
    setup_logging(settings.data_dir)       # pre load(), da i greška u podešavanjima ode u log
    settings.load()
    apply_language(app, str(settings.get("appearance.language", i18n.DEFAULT_LANGUAGE)))
    install_exception_hook()
    log = logging.getLogger("main")
    if settings.load_error:
        QMessageBox.warning(None, tr("Podešavanja"), settings.load_error)

    theme = ThemeManager(app, settings.data_dir / "cache")
    theme.apply(settings.get("appearance.theme", "light"), settings.get("appearance.accent", "blue"))

    db = DatabaseManager(settings.database_path)
    try:
        db.open()
    except AppError as exc:
        log.exception("Baza se ne može otvoriti")
        QMessageBox.critical(None, tr("Baza podataka"), str(exc))
        return 1

    window = MainWindow(db, settings, theme)
    window.show_restored()
    QTimer.singleShot(250, window.offer_demo_data)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
