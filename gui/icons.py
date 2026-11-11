"""Jednostavne vektorske ikone za navigaciju, crtane QPainter-om u boji teme."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

_RATIO = 2  # crtamo u dvostrukoj rezoluciji da ivice budu oštre na HiDPI ekranima


def _pixmap(name: str, color: str, size: int) -> QPixmap:
    pm = QPixmap(size * _RATIO, size * _RATIO)
    pm.setDevicePixelRatio(_RATIO)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.scale(size / 24, size / 24)   # dalje crtamo u koordinatama 24 x 24

    if name == "dashboard":
        for x, y in ((3, 3), (13.5, 3), (3, 13.5), (13.5, 13.5)):
            p.drawRoundedRect(QRectF(x, y, 7.5, 7.5), 2, 2)
    elif name == "partners":
        p.drawEllipse(QPointF(9, 8), 3.6, 3.6)
        p.drawArc(QRectF(2.5, 14, 13, 11), 0, 180 * 16)
        p.drawEllipse(QPointF(17.2, 9), 2.7, 2.7)
        p.drawArc(QRectF(14.6, 15, 8, 8), 0, 180 * 16)
    elif name == "articles":
        # privezak (etiketa sa cenom)
        p.drawPolygon([QPointF(3, 12), QPointF(11, 4), QPointF(20, 4), QPointF(20, 13),
                       QPointF(12, 21)])
        p.drawEllipse(QPointF(15.5, 8.5), 1.6, 1.6)
    elif name == "invoices":
        p.drawRoundedRect(QRectF(5, 3, 14, 18), 2, 2)
        for y in (9, 13, 17):
            p.drawLine(QPointF(8.5, y), QPointF(15.5, y))
    elif name == "cash":
        p.drawRoundedRect(QRectF(2.5, 6, 19, 12), 2.2, 2.2)
        p.drawEllipse(QPointF(12, 12), 2.8, 2.8)
        p.drawPoint(QPointF(6.3, 12))
        p.drawPoint(QPointF(17.7, 12))
    elif name == "settings":
        for y, knob in ((7, 9), (12, 15), (17, 8)):
            p.drawLine(QPointF(4, y), QPointF(knob - 2.4, y))
            p.drawLine(QPointF(knob + 2.4, y), QPointF(20, y))
            p.drawEllipse(QPointF(knob, y), 2.2, 2.2)
    p.end()
    return pm


def nav_icon(name: str, normal_color: str, checked_color: str, size: int = 20) -> QIcon:
    """Ikona sa dva stanja: obična i označena (checked) boja."""
    icon = QIcon()
    icon.addPixmap(_pixmap(name, normal_color, size), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_pixmap(name, checked_color, size), QIcon.Mode.Normal, QIcon.State.On)
    return icon
