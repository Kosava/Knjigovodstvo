"""Zajedničke GUI komponente: osnovna stranica, kartice, dijalozi, pomoćne funkcije."""

from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core import AppError, ValidationError
from i18n import tr


# --------------------------------------------------------------------------- #
# Stil
# --------------------------------------------------------------------------- #
def repolish(widget: QWidget) -> None:
    """Natera QSS da ponovo pročita dinamička svojstva (npr. ``variant``)."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_property(widget: QWidget, name: str, value: object) -> None:
    widget.setProperty(name, value)
    repolish(widget)


def primary(button: QPushButton) -> QPushButton:
    set_property(button, "variant", "primary")
    return button


def danger(button: QPushButton) -> QPushButton:
    set_property(button, "variant", "danger")
    return button


# --------------------------------------------------------------------------- #
# Poruke
# --------------------------------------------------------------------------- #
def _box(parent: QWidget | None, icon: QMessageBox.Icon, title: str, text: str) -> QMessageBox:
    box = QMessageBox(icon, title, text, parent=parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    return box


def show_error(parent: QWidget | None, text: str, title: str | None = None) -> None:
    box = _box(parent, QMessageBox.Icon.Critical, title or tr("Greška"), text)
    box.addButton(tr("U redu"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def show_info(parent: QWidget | None, text: str, title: str | None = None) -> None:
    box = _box(parent, QMessageBox.Icon.Information, title or tr("Obaveštenje"), text)
    box.addButton(tr("U redu"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def show_validation(parent: QWidget | None, exc: ValidationError) -> None:
    """Prikazuje sve poruke validacije, jednu ispod druge."""
    text = "\n".join(f"•  {m}" for m in exc.messages)
    box = _box(parent, QMessageBox.Icon.Warning, tr("Proverite unos"), text)
    box.addButton(tr("U redu"), QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def show_app_error(parent: QWidget | None, exc: Exception) -> None:
    """Jedno mesto za obradu grešaka iz core sloja."""
    if isinstance(exc, ValidationError):
        show_validation(parent, exc)
    else:
        show_error(parent, str(exc))


def confirm(parent: QWidget | None, title: str, text: str, confirm_text: str | None = None,
            destructive: bool = False) -> bool:
    box = _box(parent, QMessageBox.Icon.Question, title, text)
    yes = box.addButton(confirm_text or tr("Da"), QMessageBox.ButtonRole.AcceptRole)
    no = box.addButton(tr("Otkaži"), QMessageBox.ButtonRole.RejectRole)
    (danger if destructive else primary)(yes)
    box.setDefaultButton(no if destructive else yes)
    box.exec()
    return box.clickedButton() is yes


# --------------------------------------------------------------------------- #
# Osnovni elementi
# --------------------------------------------------------------------------- #
class BasePage(QWidget):
    """Osnova za sve module u glavnom prozoru."""

    status_message = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def root_layout(self) -> QVBoxLayout:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(16)
        return layout

    def refresh(self) -> None:
        """Ponovo učitava podatke iz baze (poziva se pri svakom otvaranju stranice)."""


class PageHeader(QWidget):
    """Naslov stranice + podnaslov, sa prostorom za dugmad sa desne strane."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("PageTitle")
        text.addWidget(self.title)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("PageSubtitle")
            text.addWidget(sub)
        row.addLayout(text, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        row.addLayout(self.actions)


class Card(QFrame):
    """Bela kartica sa tankom ivicom."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")


class StatCard(Card):
    """Kartica sa naslovom, velikom vrednošću i opcionom napomenom."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(4)
        self.title = QLabel(title)
        self.title.setObjectName("CardTitle")
        self.value = QLabel("–")
        self.value.setObjectName("CardValue")
        self.note = QLabel("")
        self.note.setObjectName("CardNote")
        for w in (self.title, self.value, self.note):
            layout.addWidget(w)

    def set_value(self, value: str, note: str = "", role: str | None = None) -> None:
        self.value.setText(value)
        self.note.setText(note)
        self.note.setVisible(bool(note))
        set_property(self.value, "role", role)


class CardGrid(QWidget):
    """Kartice u redu koji se prelama: koliko kolona stane zavisi od širine prozora.

    Tako se iznosi nikad ne seku i ne nastaje vodoravni klizač na užim ekranima.
    """

    def __init__(self, cards: Iterable[QWidget], min_card_width: int = 230,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards = list(cards)
        self._min_w = min_card_width
        self._cols = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(14)
        self._grid.setVerticalSpacing(14)
        self._relayout(len(self._cards))

    def _columns_for(self, width: int) -> int:
        n = len(self._cards)
        fits = max(1, min(n, (width + 14) // (self._min_w + 14)))
        rows = -(-n // fits)                 # ravnomerno: 5 kartica u 2 reda = 3 + 2, ne 4 + 1
        return -(-n // rows)

    def _relayout(self, cols: int) -> None:
        if cols == self._cols:
            return
        self._cols = cols
        for card in self._cards:
            self._grid.removeWidget(card)
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)
        for i, card in enumerate(self._cards):
            self._grid.addWidget(card, i // cols, i % cols)
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naziv
        self._relayout(self._columns_for(event.size().width()))
        super().resizeEvent(event)

    def minimumSizeHint(self):  # noqa: N802 - jedna kolona je najuži mogući raspored
        hint = super().minimumSizeHint()
        hint.setWidth(self._min_w)
        return hint


def search_edit(placeholder: str) -> QLineEdit:
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    edit.setMinimumWidth(280)
    return edit


def configure_table(view: QTableView, stretch_column: int | None = 0, row_height: int = 36) -> None:
    """Zajednička podešavanja tabela: sortiranje, zebra redovi, auto-širina kolona.

    Poziva se *nakon* ``setModel``. Sve kolone se prilagođavaju sadržaju, a
    ``stretch_column`` zauzima preostali prostor. Sa ``None`` se sve kolone
    prilagođavaju sadržaju (uz vodoravni klizač ako ne stanu), a poslednja
    popunjava ostatak.
    """
    view.setAlternatingRowColors(True)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setShowGrid(False)
    view.setWordWrap(False)
    view.setSortingEnabled(True)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.verticalHeader().setVisible(False)
    view.verticalHeader().setDefaultSectionSize(row_height)
    header = view.horizontalHeader()
    header.setHighlightSections(False)
    header.setSortIndicatorShown(True)
    header.setMinimumSectionSize(70)
    header.setStretchLastSection(stretch_column is None)
    for col in range(view.model().columnCount()):
        mode = QHeaderView.ResizeMode.Stretch if col == stretch_column \
            else QHeaderView.ResizeMode.ResizeToContents
        header.setSectionResizeMode(col, mode)


def clear_selection_safe(view: QTableView) -> None:
    view.clearSelection()
    view.setCurrentIndex(view.model().index(-1, -1))


# --------------------------------------------------------------------------- #
# Osnovni dijalog sa "Sačuvaj / Otkaži"
# --------------------------------------------------------------------------- #
class BaseDialog(QDialog):
    """Dijalog čiji ``save()`` upisuje podatke; greške ostavljaju dijalog otvorenim."""

    def __init__(self, title: str, parent: QWidget | None = None, min_width: int = 520) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(min_width)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(24, 22, 24, 20)
        self.root.setSpacing(16)
        self.buttons = QDialogButtonBox()
        self.save_button = self.buttons.addButton(tr("Sačuvaj"), QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = self.buttons.addButton(tr("Otkaži"), QDialogButtonBox.ButtonRole.RejectRole)
        primary(self.save_button)
        self.save_button.setDefault(True)
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        cancel.setAutoDefault(False)

    def add_buttons(self) -> None:
        """Poziva se na kraju gradnje sadržaja."""
        self.root.addWidget(self.buttons)

    def save(self) -> bool:  # pragma: no cover - implementiraju podklase
        raise NotImplementedError

    def _on_accept(self) -> None:
        try:
            if self.save():
                self.accept()
        except AppError as exc:
            show_app_error(self, exc)


def field_hint(text: str = "", error: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName("FormError" if error else "FormHint")
    return label


def combo_items(combo, values: Iterable[str]) -> None:
    combo.clear()
    combo.addItems(list(values))


def fill_combo(combo, keys: Iterable[str], current: str | None = None) -> None:
    """Puni listu prevedenim nazivima; ključ (vrednost iz baze) je u ``currentData()``."""
    combo.clear()
    for key in keys:
        combo.addItem(tr(key), key)
    if current is not None:
        select_data(combo, current)


def select_data(combo, value: object) -> None:
    """Bira stavku po ključu (``currentData``), a ne po prikazanom (prevedenom) tekstu."""
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)
