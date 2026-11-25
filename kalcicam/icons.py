"""Hand-drawn style line icons, painted in code (no emoji, no image files).

Each stroke is drawn twice with a tiny offset so it looks sketched with a
marker. Icons are recoloured whenever the theme changes.
"""

import weakref

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from . import themes


def _poly(*pts, close=False):
    path = QPainterPath(QPointF(*pts[0]))
    for x, y in pts[1:]:
        path.lineTo(x, y)
    if close:
        path.closeSubpath()
    return path


def _rect(x, y, w, h):
    path = QPainterPath()
    path.addRect(QRectF(x, y, w, h))
    return path


def _circle(cx, cy, r):
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), r, r)
    return path


def _arc(x, y, w, h, start, span):
    path = QPainterPath()
    path.arcMoveTo(QRectF(x, y, w, h), start)
    path.arcTo(QRectF(x, y, w, h), start, span)
    return path


# name -> (stroke paths, filled paths), on a 24x24 grid
def _shapes(name):
    S, F = [], []
    if name == "home":
        S += [_poly((3, 12), (12, 4), (21, 12)), _poly((6, 10), (6, 20), (18, 20), (18, 10)),
              _poly((10, 20), (10, 14), (14, 14), (14, 20))]
    elif name == "video":
        S += [_rect(3, 6, 12, 12), _poly((15, 10), (21, 6.5), (21, 17.5), (15, 14))]
    elif name in ("audio", "speaker"):
        S += [_poly((3, 9), (7, 9), (12, 4.5), (12, 19.5), (7, 15), (3, 15), close=True),
              _arc(10, 8, 6, 8, -60, 120), _arc(10, 5, 10, 14, -60, 120)]
    elif name == "overlays":
        S += [_circle(12, 12, 8.5), _circle(12, 12, 3.5)]
    elif name in ("extras", "bolt"):
        S += [_poly((13.5, 2.5), (5, 13.5), (11.5, 13.5), (9.5, 21.5), (19, 10), (12.5, 10), close=True)]
    elif name == "recordings":
        S += [_rect(4, 3, 16, 18), _poly((8, 3), (8, 21)), _poly((16, 3), (16, 21))]
        for y in (6, 10, 14, 18):
            S += [_poly((4, y), (8, y)), _poly((16, y), (20, y))]
    elif name == "themes":
        S += [_poly((4, 20), (9, 15)), _rect(9, 9, 6, 6)]
        S += [_poly((15, 9), (20, 4))]
        F += [_circle(17, 17, 2.2), _circle(6, 6, 1.6)]
    elif name == "about":
        S += [_circle(12, 12, 9), _poly((12, 10.5), (12, 17))]
        F += [_circle(12, 7.3, 1.3)]
    elif name == "camera":
        S += [_rect(3, 7.5, 18, 12), _poly((8, 7.5), (9.5, 4.5), (14.5, 4.5), (16, 7.5)), _circle(12, 13.5, 3.8)]
    elif name == "pause":
        F += [_rect(6.5, 5, 3.5, 14), _rect(14, 5, 3.5, 14)]
    elif name == "play":
        F += [_poly((7, 4.5), (19.5, 12), (7, 19.5), close=True)]
    elif name == "stop":
        F += [_rect(6, 6, 12, 12)]
    elif name == "mic":
        p = QPainterPath()
        p.addRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
        S += [p, _arc(6, 6, 12, 11, 180, 180), _poly((12, 17), (12, 21)), _poly((8.5, 21), (15.5, 21))]
    elif name == "webcam":
        S += [_circle(12, 10, 6.5), _circle(12, 10, 2.2), _poly((12, 16.5), (12, 20)), _poly((7.5, 20.5), (16.5, 20.5))]
    elif name == "cursor":
        S += [_poly((6, 3), (6, 19), (10, 15), (13, 21), (15.5, 20), (12.5, 14), (18, 14), close=True)]
    elif name == "folder":
        S += [_poly((3, 6), (9, 6), (11, 8.5), (21, 8.5), (21, 19), (3, 19), close=True)]
    elif name == "refresh":
        S += [_arc(4.5, 4.5, 15, 15, 40, 290), _poly((19.5, 3.5), (18.5, 8.5), (13.8, 7.2))]
    elif name == "trash":
        S += [_poly((4, 6.5), (20, 6.5)), _poly((9.5, 6.5), (10, 3.5), (14, 3.5), (14.5, 6.5)),
              _poly((6, 6.5), (7, 20.5), (17, 20.5), (18, 6.5)), _poly((10, 10), (10, 17)), _poly((14, 10), (14, 17))]
    elif name == "search":
        S += [_circle(10, 10, 6), _poly((14.5, 14.5), (20.5, 20.5))]
    elif name == "save":
        S += [_poly((4, 4), (17, 4), (20, 7), (20, 20), (4, 20), close=True), _rect(8, 4, 7, 5), _rect(7, 13, 10, 7)]
    elif name == "check":
        S += [_poly((4.5, 12.5), (9.5, 17.5), (19.5, 6))]
    elif name == "cross":
        S += [_poly((6, 6), (18, 18)), _poly((18, 6), (6, 18))]
    elif name == "warn":
        S += [_poly((12, 3.5), (21, 19.5), (3, 19.5), close=True), _poly((12, 9.5), (12, 14))]
        F += [_circle(12, 16.8, 1.1)]
    elif name == "next":
        S += [_poly((4, 12), (19, 12)), _poly((13, 6), (19, 12), (13, 18))]
    elif name == "back":
        S += [_poly((20, 12), (5, 12)), _poly((11, 6), (5, 12), (11, 18))]
    elif name == "copy":
        S += [_rect(8, 8, 12, 12), _poly((5, 15), (4, 15), (4, 4), (15, 4), (15, 5))]
    elif name == "wand":
        S += [_poly((4, 20), (15, 9)), _poly((17, 3), (17, 7)), _poly((15, 5), (19, 5)),
              _poly((20, 10), (20, 13)), _poly((18.5, 11.5), (21.5, 11.5))]
    elif name == "monitor":
        S += [_rect(3, 4, 18, 12), _poly((12, 16), (12, 20)), _poly((7.5, 20), (16.5, 20))]
    elif name == "region":
        for (x, y, dx, dy) in ((3, 3, 1, 1), (21, 3, -1, 1), (3, 21, 1, -1), (21, 21, -1, -1)):
            S += [_poly((x, y + 5 * dy), (x, y), (x + 5 * dx, y))]
        F += [_rect(10, 10, 4, 4)]
    elif name == "window":
        S += [_rect(3, 4, 18, 16), _poly((3, 8.5), (21, 8.5))]
        F += [_circle(6, 6.3, 0.9), _circle(8.8, 6.3, 0.9)]
    elif name == "rect":  # rectangle on a screen: frame with a resize arrow
        S += [_rect(3, 5, 18, 14), _poly((9, 15), (15, 9)), _poly((11, 9), (15, 9), (15, 13))]
    elif name == "mouse_area":  # around mouse: dashed box with a cursor inside
        for (x0, y0, x1, y1) in ((3, 3, 8, 3), (12, 3, 16, 3), (21, 3, 21, 8), (21, 12, 21, 16),
                                 (3, 21, 6, 21), (3, 3, 3, 8), (3, 12, 3, 17)):
            S += [_poly((x0, y0), (x1, y1))]
        S += [_poly((10, 9), (10, 21), (13, 18), (15.5, 22.5), (17.5, 21.5), (15, 17), (19, 17), close=True)]
    elif name == "gamepad":
        p = QPainterPath()
        p.addRoundedRect(QRectF(2.5, 7, 19, 11), 5, 5)
        S += [p, _poly((7, 10.5), (7, 14.5)), _poly((5, 12.5), (9, 12.5))]
        F += [_circle(15.5, 11, 1.3), _circle(18, 13.5, 1.3)]
    elif name == "chevron_up":
        S += [_poly((5, 15), (12, 8), (19, 15))]
    elif name == "chevron_down":
        S += [_poly((5, 9), (12, 16), (19, 9))]
    elif name == "mic_off":
        p = QPainterPath()
        p.addRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
        S += [p, _arc(6, 6, 12, 11, 180, 180), _poly((12, 17), (12, 21)), _poly((4, 4), (20, 20))]
    elif name == "speaker_off":
        S += [_poly((3, 9), (7, 9), (12, 4.5), (12, 19.5), (7, 15), (3, 15), close=True),
              _poly((15, 9), (21, 15)), _poly((21, 9), (15, 15))]
    elif name == "keyboard":
        p = QPainterPath()
        p.addRoundedRect(QRectF(2.5, 6, 19, 12), 2, 2)
        S += [p, _poly((7, 14.5), (17, 14.5))]
        for x in (6, 9.5, 13, 16.5):
            F += [_rect(x - 0.9, 8.6, 1.8, 1.8)]
        F += [_rect(4.6, 13.6, 1.8, 1.8), _rect(17.6, 13.6, 1.8, 1.8)]
    elif name == "power":
        S += [_arc(4.5, 5, 15, 15, 120, 300), _poly((12, 3), (12, 11))]
    elif name == "rec":
        F += [_circle(12, 12, 7)]
    return S, F


def pixmap(name: str, color, size: int = 20, dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size * dpr / 24, size * dpr / 24)
    col = QColor(color)
    strokes, fills = _shapes(name)
    # main stroke
    p.setPen(QPen(col, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    for s in strokes:
        p.drawPath(s)
    # second, thinner "marker" pass, slightly offset
    ghost = QColor(col)
    ghost.setAlphaF(0.45)
    p.setPen(QPen(ghost, 1.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.translate(0.7, -0.5)
    for s in strokes:
        p.drawPath(s)
    p.translate(-0.7, 0.5)
    p.setPen(QPen(col, 1.2))
    p.setBrush(col)
    for f in fills:
        p.drawPath(f)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color, on_color=None, size: int = 20) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Normal, QIcon.Off)
    ic.addPixmap(pixmap(name, on_color or color, size), QIcon.Normal, QIcon.On)
    dim = QColor(color)
    dim.setAlphaF(0.4)
    ic.addPixmap(pixmap(name, dim, size), QIcon.Disabled, QIcon.Off)
    return ic


# ---- keep icons in step with the theme ----
# role: "plain" (text colour), "accent", "primary" (sits on accent fill), "toggle" (flips when checked)
_bound: list = []


def _make(name, role, size):
    t = themes.current
    if role == "primary":
        return icon(name, t.on_accent, size=size)
    if role == "accent":
        return icon(name, t.accent, size=size)
    if role == "toggle":
        return icon(name, t.text, t.on_accent, size=size)
    return icon(name, t.text, size=size)


def bind(widget, name: str, role: str = "plain", size: int = 20):
    """Give a button/action/label an icon that recolours with the theme."""
    _bound[:] = [b for b in _bound if b[0]() is not widget]  # re-binding replaces the old icon
    _bound.append((weakref.ref(widget), name, role, size))
    _apply(widget, name, role, size)
    return widget


def _apply(w, name, role, size):
    from PySide6.QtWidgets import QLabel

    ic = _make(name, role, size)
    if isinstance(w, QLabel):
        w.setPixmap(ic.pixmap(size, size))
    else:
        w.setIcon(ic)
        if hasattr(w, "setIconSize"):
            from PySide6.QtCore import QSize

            w.setIconSize(QSize(size, size))


def refresh():
    alive = []
    for ref, name, role, size in _bound:
        w = ref()
        if w is None:
            continue
        try:
            _apply(w, name, role, size)
            alive.append((ref, name, role, size))
        except RuntimeError:  # widget already deleted on the C++ side
            pass
    _bound[:] = alive
