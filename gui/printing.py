"""Štampa i izvoz u PDF (HTML iz ``core.printing`` -> QTextDocument -> QPrinter)."""

from __future__ import annotations

from i18n import tr

from pathlib import Path

from PyQt6.QtCore import QMarginsF
from PyQt6.QtGui import QPageLayout, QPageSize, QTextDocument
from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
from PyQt6.QtWidgets import QDialog, QFileDialog, QWidget

from core import AppError


def _document(html: str) -> QTextDocument:
    doc = QTextDocument()
    doc.setHtml(html)
    return doc


def _print(doc: QTextDocument, printer: QPrinter) -> None:
    # PyQt6 izlaže metodu kao ``print``; stariji omotači kao ``print_``
    (getattr(doc, "print", None) or getattr(doc, "print_"))(printer)


def _a4_printer(mode: QPrinter.PrinterMode = QPrinter.PrinterMode.ScreenResolution) -> QPrinter:
    printer = QPrinter(mode)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    # Margine su 0 jer QTextDocument sam ostavlja ~20 mm; veće vrednosti bi suzile tabelu.
    printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
    return printer


def write_pdf(html: str, path: str | Path) -> Path:
    """Upisuje HTML u PDF fajl (bez dijaloga - korisno i za testove)."""
    path = Path(path)
    printer = _a4_printer()
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(path))
    _print(_document(html), printer)
    if not path.exists():
        raise AppError(tr("PDF nije napravljen: {path}", path=path))
    return path


def export_pdf(parent: QWidget | None, html: str, suggested_name: str) -> Path | None:
    """Pita korisnika gde da sačuva PDF. Vraća putanju ili ``None`` ako je otkazano."""
    start = str(Path.home() / suggested_name)
    chosen, _ = QFileDialog.getSaveFileName(parent, tr("Izvoz u PDF"), start, tr("PDF dokument (*.pdf)"))
    if not chosen:
        return None
    if not chosen.lower().endswith(".pdf"):
        chosen += ".pdf"
    return write_pdf(html, chosen)


def print_document(parent: QWidget | None, html: str) -> bool:
    """Otvara sistemski dijalog za štampu. Vraća ``True`` ako je štampano."""
    printer = _a4_printer()
    dialog = QPrintDialog(printer, parent)
    dialog.setWindowTitle(tr("Štampa fakture"))
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    _print(_document(html), printer)
    return True
