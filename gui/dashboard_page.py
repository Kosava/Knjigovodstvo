"""Modul "Dashboard": ključni pokazatelji, grafikon prihoda/rashoda, dospele fakture."""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core import AppError, DatabaseManager, tax
from core.stats import DashboardStats, MonthPoint, load_dashboard_stats
from settings import SettingsManager, ThemeManager
from i18n import tr

from .common import BasePage, Card, CardGrid, PageHeader, StatCard, configure_table, show_app_error
from .table_models import FilterProxyModel, ObjectTableModel, invoice_columns


def _nice_step(raw: float) -> float:
    """Zaokružuje korak ose na "lepu" vrednost (1, 2, 2.5, 5 x 10^n)."""
    if raw <= 0:
        return 1.0
    exp = math.floor(math.log10(raw))
    base = raw / 10 ** exp
    for nice in (1, 2, 2.5, 5, 10):
        if base <= nice:
            return nice * 10 ** exp
    return 10 ** (exp + 1)


def _short(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:g} " + tr("mil")
    if value >= 1_000:
        return f"{value / 1_000:g}k"
    return f"{value:g}"


class MonthlyChart(QWidget):
    """Stubičasti grafikon prihoda i rashoda po mesecima (bez vanjskih biblioteka)."""

    def __init__(self, theme: ThemeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self._points: list[MonthPoint] = []
        self.setMinimumHeight(230)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        theme.theme_changed.connect(lambda *_: self.update())

    def set_points(self, points: list[MonthPoint]) -> None:
        self._points = points
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.theme
        muted, grid = t.color("text_muted"), t.color("border")
        rev_color, exp_color = t.color("chart_revenue"), t.color("chart_expense")
        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)

        # Legenda
        x = 4.0
        for color, label in ((rev_color, tr("Prihodi (izlazne)")), (exp_color, tr("Rashodi (ulazne)"))):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, 6, 10, 10), 2, 2)
            p.setPen(muted)
            p.drawText(QPointF(x + 15, 15), label)
            x += 24 + p.fontMetrics().horizontalAdvance(label)

        top_values = [max(float(m.revenue), float(m.expense)) for m in self._points]
        highest = max(top_values, default=0.0)
        step = _nice_step(highest / 4) if highest > 0 else 1.0
        ticks = max(1, math.ceil(highest / step)) if highest > 0 else 4
        axis_max = step * ticks

        left, bottom = 46.0, 22.0
        plot = QRectF(left, 30, self.width() - left - 6, self.height() - 30 - bottom)
        if plot.width() <= 0 or plot.height() <= 0:
            return

        if highest <= 0:
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
            p.setPen(muted)
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, tr("Nema faktura u ovom periodu"))
            return

        # Mreža i oznake y ose
        for i in range(ticks + 1):
            y = plot.bottom() - plot.height() * i / ticks
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(muted)
            p.drawText(QRectF(0, y - 8, left - 6, 16),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _short(step * i))

        if not self._points:
            return
        group_w = plot.width() / len(self._points)
        bar_w = min(30.0, group_w * 0.28)
        p.setPen(Qt.PenStyle.NoPen)
        for idx, point in enumerate(self._points):
            center = plot.left() + group_w * (idx + 0.5)
            for offset, value, color in ((-bar_w - 1, point.revenue, rev_color),
                                         (1, point.expense, exp_color)):
                h = plot.height() * float(value) / axis_max if axis_max else 0
                if h > 0:
                    p.setBrush(color)
                    p.drawRoundedRect(QRectF(center + offset, plot.bottom() - h, bar_w, h), 3, 3)
            p.setPen(muted)
            p.drawText(QRectF(center - group_w / 2, plot.bottom() + 4, group_w, 16),
                       Qt.AlignmentFlag.AlignCenter, point.label)
            p.setPen(Qt.PenStyle.NoPen)


class DashboardPage(BasePage):
    def __init__(self, db: DatabaseManager, theme: ThemeManager, settings: SettingsManager,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.db, self.theme, self.settings = db, theme, settings
        self._build_ui()
        theme.theme_changed.connect(lambda *_: self._repaint_tables())

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
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        self.header = PageHeader(tr("Pregled poslovanja"), "")
        root.addWidget(self.header)

        self.c_receivable = StatCard(tr("Potraživanja od kupaca"))
        self.c_payable = StatCard(tr("Obaveze prema dobavljačima"))
        self.c_cash = StatCard(tr("Gotovina u blagajni"))
        self.c_bank = StatCard(tr("Promet po računu"))
        self.c_vat = StatCard(tr("PDV obaveza (tekući mesec)"))
        root.addWidget(CardGrid((self.c_receivable, self.c_payable, self.c_vat,
                                 self.c_cash, self.c_bank)))

        mid = QHBoxLayout()
        mid.setSpacing(14)
        chart_card = Card()
        cl = QVBoxLayout(chart_card)
        cl.setContentsMargins(18, 14, 18, 14)
        title = QLabel(tr("Prihodi i rashodi, poslednjih 6 meseci (bez PDV-a, RSD)"))
        title.setObjectName("SectionTitle")
        self.chart = MonthlyChart(self.theme)
        cl.addWidget(title)
        cl.addWidget(self.chart, 1)
        mid.addWidget(chart_card, 1)

        overdue_card = Card()
        ol = QVBoxLayout(overdue_card)
        ol.setContentsMargins(18, 14, 18, 14)
        self.overdue_title = QLabel(tr("Dospele neplaćene fakture"))
        self.overdue_title.setObjectName("SectionTitle")
        self.overdue_model, self.overdue_view = self._mini_table(
            only=("Broj", "Rok plaćanja", "Partner", "Ukupno"), stretch=2)
        ol.addWidget(self.overdue_title)
        ol.addWidget(self.overdue_view, 1)
        mid.addWidget(overdue_card, 1)
        root.addLayout(mid)

        recent_card = Card()
        rl = QVBoxLayout(recent_card)
        rl.setContentsMargins(18, 14, 18, 14)
        t2 = QLabel(tr("Poslednje fakture"))
        t2.setObjectName("SectionTitle")
        self.recent_model, self.recent_view = self._mini_table()
        self.recent_view.setMinimumHeight(8 * 36 + 44)
        rl.addWidget(t2)
        rl.addWidget(self.recent_view)
        root.addWidget(recent_card)
        root.addStretch(1)

    def _mini_table(self, only: tuple[str, ...] | None = None, stretch: int = 3
                    ) -> tuple[ObjectTableModel, QTableView]:
        model = ObjectTableModel(invoice_columns(compact=True, only=only), self.theme)
        proxy = FilterProxyModel(self)
        proxy.setSourceModel(model)
        view = QTableView()
        view.setModel(proxy)
        configure_table(view, stretch_column=stretch, row_height=34)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setMinimumHeight(250)
        return model, view

    def _repaint_tables(self) -> None:
        self.overdue_view.viewport().update()
        self.recent_view.viewport().update()

    def _money(self, value) -> str:
        return f"{tax.format_money(value)} {self.settings.get('company.currency', 'RSD')}"

    def refresh(self) -> None:
        try:
            stats: DashboardStats = load_dashboard_stats(self.db)
        except AppError as exc:
            show_app_error(self, exc)
            return
        name = self.settings.get("company.name", "")
        self.header.title.setText(tr("Pregled poslovanja") + (f" · {name}" if name else ""))

        self.c_receivable.set_value(self._money(stats.receivables), tr("Neplaćene izlazne fakture"))
        self.c_payable.set_value(self._money(stats.payables), tr("Neplaćene ulazne fakture"))
        self.c_cash.set_value(self._money(stats.cash_balance),
                              role="success" if stats.cash_balance >= 0 else "danger")
        self.c_bank.set_value(self._money(stats.bank_balance), tr("uplate − isplate preko računa"))
        owed = stats.vat_liability
        self.c_vat.set_value(self._money(abs(owed)),
                             tr("za uplatu državi") if owed > 0 else (tr("pretporez (povraćaj)") if owed < 0
                                                                     else tr("nema obaveze")),
                             role="warning" if owed > 0 else None)
        self.chart.set_points(stats.months)
        self.overdue_model.set_rows(stats.overdue_invoices)
        self.recent_model.set_rows(stats.recent_invoices)
        self.overdue_title.setText(tr("Dospele neplaćene fakture ({count})", count=stats.overdue_count))
