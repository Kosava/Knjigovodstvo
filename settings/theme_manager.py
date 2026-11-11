"""Centralni menadžer tema (Light / Dark) zasnovan na QSS-u.

Sav izgled aplikacije definiše se na jednom mestu: paleta boja po temi +
jedan QSS šablon. Promena teme u realnom vremenu znači samo ponovnu primenu
stila na ``QApplication``; ostatak aplikacije se obaveštava signalom
``theme_changed``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from string import Template

from PyQt6.QtCore import QObject, QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import QApplication

from i18n import tr_noop

log = logging.getLogger(__name__)

# Nazivi se prevode pri prikazu: tr(THEMES[...]).
THEMES = {"light": tr_noop("Svetla"), "dark": tr_noop("Tamna")}
ACCENTS = {"blue": tr_noop("Plava"), "green": tr_noop("Tamnozelena")}

# --------------------------------------------------------------------------- #
# Palete
# --------------------------------------------------------------------------- #
_BASE = {
    "light": {
        "bg": "#F3F4F6",            # pozadina stranica
        "surface": "#FFFFFF",       # kartice, tabele, paneli
        "surface_alt": "#F8F9FB",   # onemogućena polja
        "input_bg": "#FFFFFF",
        "border": "#E1E4E8",
        "border_strong": "#CDD2D9",
        "text": "#2B3038",
        "text_muted": "#6A7280",
        "text_disabled": "#A3AAB5",
        "alt_row": "#F7F8FA",
        "header_bg": "#F3F5F8",
        "hover_bg": "#EEF0F3",
        "pressed_bg": "#E4E7EB",
        "sidebar_bg": "#FBFBFC",
        "sidebar_text": "#4B5360",
        "sidebar_hover": "#EEF0F3",
        "tooltip_bg": "#2B3038",
        "tooltip_text": "#FFFFFF",
        "success": "#177A43",
        "danger": "#B42318",
        "danger_bg": "#FDECEA",
        "warning": "#A15C07",
        "scroll_handle": "#C9CED6",
        "scroll_handle_hover": "#AEB5C0",
        "chart_revenue": "#2563EB",
        "chart_expense": "#9AA3B2",
        "accent_text": "#FFFFFF",
    },
    "dark": {
        "bg": "#14161A",
        "surface": "#1C1F25",
        "surface_alt": "#22262D",
        "input_bg": "#171A1F",
        "border": "#2C313A",
        "border_strong": "#3A404B",
        "text": "#E6E8EC",
        "text_muted": "#9099A8",
        "text_disabled": "#5F6775",
        "alt_row": "#20242B",
        "header_bg": "#232730",
        "hover_bg": "#262B33",
        "pressed_bg": "#2E343E",
        "sidebar_bg": "#181B20",
        "sidebar_text": "#B4BBC7",
        "sidebar_hover": "#242831",
        "tooltip_bg": "#E6E8EC",
        "tooltip_text": "#14161A",
        "success": "#4CC38A",
        "danger": "#F2706A",
        "danger_bg": "#3A2022",
        "warning": "#E0A24A",
        "scroll_handle": "#3A404B",
        "scroll_handle_hover": "#4C5460",
        "chart_revenue": "#4C8DF6",
        "chart_expense": "#6B7482",
        "accent_text": "#FFFFFF",
    },
}

_ACCENT = {
    ("light", "blue"): {"accent": "#2563EB", "accent_hover": "#1D4ED8", "accent_pressed": "#1E40AF",
                        "sel_bg": "#E3ECFE", "sel_text": "#1E3A8A"},
    ("light", "green"): {"accent": "#1F7A4D", "accent_hover": "#18653F", "accent_pressed": "#125033",
                         "sel_bg": "#DFF1E7", "sel_text": "#14532D"},
    ("dark", "blue"): {"accent": "#3B82F6", "accent_hover": "#5B96F8", "accent_pressed": "#2563EB",
                       "sel_bg": "#1E3A63", "sel_text": "#DCE8FF"},
    ("dark", "green"): {"accent": "#2E9E6B", "accent_hover": "#3BB27C", "accent_pressed": "#238055",
                        "sel_bg": "#1C4030", "sel_text": "#D6F5E5"},
}

# --------------------------------------------------------------------------- #
# QSS šablon ($naziv se zamenjuje bojom iz palete)
# --------------------------------------------------------------------------- #
_QSS = Template(r"""
* { outline: 0; }
QWidget { color: $text; font-size: 13px; }
QMainWindow, QDialog { background: $bg; }
QWidget#Page { background: $bg; }
QLabel { background: transparent; }
QToolTip { background: $tooltip_bg; color: $tooltip_text; border: none; padding: 5px 8px; }

/* ---- Tipografija ---- */
QLabel#PageTitle { font-size: 22px; font-weight: 600; }
QLabel#PageSubtitle { color: $text_muted; }
QLabel#SectionTitle { font-size: 14px; font-weight: 600; }
QLabel[muted="true"] { color: $text_muted; }
QLabel[role="success"] { color: $success; font-weight: 600; }
QLabel[role="danger"] { color: $danger; font-weight: 600; }
QLabel[role="warning"] { color: $warning; font-weight: 600; }
QLabel#FormHint { color: $text_muted; font-size: 12px; }
QLabel#FormError { color: $danger; font-size: 12px; }

/* ---- Kartice ---- */
QFrame#Card { background: $surface; border: 1px solid $border; border-radius: 10px; }
QLabel#CardTitle { color: $text_muted; font-size: 12px; font-weight: 600; }
QLabel#CardValue { font-size: 22px; font-weight: 600; }
QLabel#CardNote { color: $text_muted; font-size: 12px; }
QFrame#Divider { background: $border; border: none; max-height: 1px; min-height: 1px; }

/* ---- Bočna navigacija ---- */
QFrame#Sidebar { background: $sidebar_bg; border: none; border-right: 1px solid $border; }
QLabel#Brand { font-size: 17px; font-weight: 700; padding: 0; }
QLabel#BrandSub { color: $text_muted; font-size: 11px; }
QPushButton#NavButton {
    background: transparent; color: $sidebar_text; border: none; border-radius: 8px;
    padding: 10px 12px; text-align: left; font-size: 13px;
}
QPushButton#NavButton:hover { background: $sidebar_hover; }
QPushButton#NavButton:checked { background: $sel_bg; color: $sel_text; font-weight: 600; }
QPushButton#ThemeToggle {
    background: transparent; color: $text_muted; border: 1px solid $border_strong;
    border-radius: 8px; padding: 8px 12px; text-align: left;
}
QPushButton#ThemeToggle:hover { background: $sidebar_hover; color: $text; }

/* ---- Dugmad ---- */
QPushButton {
    background: $surface; color: $text; border: 1px solid $border_strong;
    border-radius: 7px; padding: 7px 14px; min-height: 18px;
}
QPushButton:hover { background: $hover_bg; }
QPushButton:pressed { background: $pressed_bg; }
QPushButton:focus { border-color: $accent; }
QPushButton:disabled { color: $text_disabled; background: $surface_alt; border-color: $border; }
QPushButton[variant="primary"] {
    background: $accent; color: $accent_text; border: 1px solid $accent; font-weight: 600;
}
QPushButton[variant="primary"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton[variant="primary"]:pressed { background: $accent_pressed; border-color: $accent_pressed; }
QPushButton[variant="primary"]:disabled { background: $border_strong; border-color: $border_strong; color: $surface; }
QPushButton[variant="danger"] { color: $danger; }
QPushButton[variant="danger"]:hover { background: $danger_bg; border-color: $danger; }
QPushButton[variant="danger"]:disabled { color: $text_disabled; }
QPushButton::menu-indicator { image: none; width: 0px; }

/* ---- Polja za unos ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox {
    background: $input_bg; color: $text; border: 1px solid $border_strong; border-radius: 7px;
    padding: 6px 9px; min-height: 18px;
    selection-background-color: $accent; selection-color: $accent_text;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QDateEdit:focus, QComboBox:focus { border: 1px solid $accent; }
QLineEdit:disabled, QTextEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QDateEdit:disabled, QComboBox:disabled { background: $surface_alt; color: $text_disabled; }
QLineEdit:read-only { background: $surface_alt; color: $text_muted; }
QLineEdit[invalid="true"], QDoubleSpinBox[invalid="true"] { border: 1px solid $danger; }

QComboBox::drop-down, QDateEdit::drop-down {
    subcontrol-origin: padding; subcontrol-position: center right; width: 26px; border: none;
}
QComboBox::down-arrow, QDateEdit::down-arrow { image: url("$icon_down"); width: 12px; height: 12px; }
QComboBox QAbstractItemView {
    background: $surface; color: $text; border: 1px solid $border_strong; padding: 3px;
    selection-background-color: $sel_bg; selection-color: $sel_text;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right; width: 20px; border: none;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right; width: 20px; border: none;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("$icon_up"); width: 10px; height: 10px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("$icon_down"); width: 10px; height: 10px; }

QCalendarWidget QWidget { background: $surface; color: $text; }
QCalendarWidget QToolButton { background: transparent; color: $text; border: none; padding: 4px 8px; }
QCalendarWidget QAbstractItemView { selection-background-color: $accent; selection-color: $accent_text; }

/* ---- Kvačice ---- */
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid $border_strong; border-radius: 4px; background: $input_bg; }
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; image: url("$icon_check"); }

/* ---- Tabele ---- */
QTableView, QTableWidget {
    background: $surface; alternate-background-color: $alt_row; color: $text;
    border: 1px solid $border; border-radius: 8px; gridline-color: transparent;
    selection-background-color: $sel_bg; selection-color: $sel_text;
}
QTableView::item { padding: 5px 8px; border: none; }
QTableView::item:selected { background: $sel_bg; color: $sel_text; }
QHeaderView { background: $header_bg; border: none; }
QHeaderView::section {
    background: $header_bg; color: $text_muted; padding: 8px 10px; border: none;
    border-bottom: 1px solid $border; border-right: 1px solid $border; font-weight: 600;
}
QHeaderView::section:hover { background: $hover_bg; color: $text; }
QHeaderView::up-arrow { image: url("$icon_up"); width: 10px; height: 10px; }
QHeaderView::down-arrow { image: url("$icon_down"); width: 10px; height: 10px; }
QTableCornerButton::section { background: $header_bg; border: none; }

/* ---- Grupe i kartice ---- */
QGroupBox {
    background: $surface; border: 1px solid $border; border-radius: 10px;
    margin-top: 14px; padding: 22px 16px 14px 16px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 14px; padding: 0 6px; color: $text; }
QGroupBox QLabel { font-weight: 400; }

/* ---- Klizači ---- */
QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll_handle; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:horizontal { background: $scroll_handle; border-radius: 4px; min-width: 28px; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: $scroll_handle_hover; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QScrollArea { border: none; background: transparent; }

/* ---- Meniji i statusna traka ---- */
QMenuBar { background: $surface; border-bottom: 1px solid $border; padding: 2px 6px; }
QMenuBar::item { background: transparent; padding: 6px 10px; border-radius: 6px; }
QMenuBar::item:selected { background: $hover_bg; }
QMenu { background: $surface; border: 1px solid $border_strong; border-radius: 8px; padding: 5px; }
QMenu::item { padding: 7px 26px 7px 14px; border-radius: 5px; }
QMenu::item:selected { background: $sel_bg; color: $sel_text; }
QMenu::item:disabled { color: $text_disabled; }
QMenu::separator { height: 1px; background: $border; margin: 5px 8px; }
QStatusBar { background: $surface; border-top: 1px solid $border; color: $text_muted; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: $text_muted; padding: 0 10px; }

QMessageBox { background: $bg; }
QSplitter::handle { background: $border; }
""")


def _draw_icon(path: Path, kind: str, color: str, size: int = 24) -> None:
    """Crta male strelice/kvačicu u PNG (QSS ``image: url()`` ne podržava boje)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), size * 0.11)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    m, c = size * 0.28, size / 2
    if kind == "down":
        p.drawPolyline([QPointF(m, c - size * 0.08), QPointF(c, c + size * 0.12), QPointF(size - m, c - size * 0.08)])
    elif kind == "up":
        p.drawPolyline([QPointF(m, c + size * 0.08), QPointF(c, c - size * 0.12), QPointF(size - m, c + size * 0.08)])
    elif kind == "check":
        p.drawPolyline([QPointF(size * 0.24, c), QPointF(size * 0.43, size * 0.68), QPointF(size * 0.76, size * 0.32)])
    p.end()
    pm.save(str(path), "PNG")


class ThemeManager(QObject):
    """Primenjuje temu na celu aplikaciju i javlja kad se promeni."""

    theme_changed = pyqtSignal(str, str)   # (tema, akcent)

    def __init__(self, app: QApplication, cache_dir: Path) -> None:
        super().__init__()
        self._app = app
        self._cache_dir = Path(cache_dir)
        self._theme = "light"
        self._accent = "blue"
        self._colors: dict[str, str] = {}
        app.setStyle("Fusion")   # ista osnova na svim platformama; QSS gradi izgled preko nje

    # -- stanje ------------------------------------------------------------ #
    @property
    def theme(self) -> str:
        return self._theme

    @property
    def accent(self) -> str:
        return self._accent

    @property
    def is_dark(self) -> bool:
        return self._theme == "dark"

    def color(self, name: str) -> QColor:
        """Boja iz trenutne palete (za delegate, grafikone, bojenje redova)."""
        return QColor(self._colors.get(name, "#FF00FF"))

    def colors(self) -> dict[str, str]:
        return dict(self._colors)

    # -- primena -------------------------------------------------------------#
    def apply(self, theme: str = "light", accent: str | None = None) -> None:
        """Primenjuje temu (i opcionalno akcentnu boju) odmah, bez restarta."""
        theme = theme if theme in THEMES else "light"
        accent = accent if accent in ACCENTS else (self._accent if self._accent in ACCENTS else "blue")
        self._theme, self._accent = theme, accent
        self._colors = {**_BASE[theme], **_ACCENT[(theme, accent)]}
        self._colors["chart_revenue"] = self._colors["accent"]

        icons = self._build_icons()
        try:
            self._app.setPalette(self._build_palette())
            self._app.setStyleSheet(_QSS.safe_substitute(self._colors, **icons))
        except Exception:   # pragma: no cover - stil nikad ne sme srušiti aplikaciju
            log.exception("Primena teme nije uspela")
        self.theme_changed.emit(theme, accent)

    def toggle(self) -> None:
        self.apply("dark" if self._theme == "light" else "light")

    # -- interno -------------------------------------------------------------- #
    def _build_palette(self) -> QPalette:
        """QPalette za delove koje QSS ne pokriva (kalendar, dijalozi, nativni elementi)."""
        c, pal = self._colors, QPalette()
        roles = {
            QPalette.ColorRole.Window: c["bg"], QPalette.ColorRole.WindowText: c["text"],
            QPalette.ColorRole.Base: c["input_bg"], QPalette.ColorRole.AlternateBase: c["alt_row"],
            QPalette.ColorRole.Text: c["text"], QPalette.ColorRole.Button: c["surface"],
            QPalette.ColorRole.ButtonText: c["text"], QPalette.ColorRole.Highlight: c["accent"],
            QPalette.ColorRole.HighlightedText: c["accent_text"],
            QPalette.ColorRole.ToolTipBase: c["tooltip_bg"], QPalette.ColorRole.ToolTipText: c["tooltip_text"],
            QPalette.ColorRole.PlaceholderText: c["text_disabled"], QPalette.ColorRole.Link: c["accent"],
        }
        for role, value in roles.items():
            pal.setColor(role, QColor(value))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
            pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["text_disabled"]))
        return pal

    def _build_icons(self) -> dict[str, str]:
        """Pravi strelice/kvačicu u boji teme i vraća putanje za QSS."""
        out: dict[str, str] = {}
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            for kind, color in (("down", self._colors["text_muted"]),
                                ("up", self._colors["text_muted"]),
                                ("check", "#FFFFFF")):
                path = self._cache_dir / f"{kind}_{self._theme}.png"
                _draw_icon(path, kind, color)
                out[f"icon_{kind}"] = path.as_posix()
        except OSError:
            log.exception("Ikone teme nisu napravljene")
            for kind in ("down", "up", "check"):
                out.setdefault(f"icon_{kind}", "")
        return out
