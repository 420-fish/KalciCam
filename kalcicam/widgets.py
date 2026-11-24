"""Reusable UI pieces in the metro-grunge style."""

import random

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider,
                               QVBoxLayout, QWidget)

from . import icons, paths, themes

_pix_cache: dict[str, QPixmap] = {}


def photo(path: str) -> QPixmap:
    """Decode each picture once per run (the pictures are only ever displayed, never changed)."""
    p = _pix_cache.get(path)
    if p is None:
        p = QPixmap(path)
        _pix_cache[path] = p
    return p


def app_icon() -> QIcon:
    return QIcon(paths.asset("icon.png"))


def head_font(pt: float) -> QFont:
    f = QFont(themes.HEAD)
    f.setPointSizeF(pt)
    return f


# ---------------- grunge drawing helpers ----------------

def rough_rect(r: QRectF, rnd: random.Random, jitter=1.6, step=16) -> QPolygonF:
    """A rectangle whose edges wobble a little, like a torn/painted border."""
    pts = []

    def edge(x0, y0, x1, y1):
        n = max(2, int(max(abs(x1 - x0), abs(y1 - y0)) / step))
        for i in range(n):
            t = i / n
            x = x0 + (x1 - x0) * t
            y = y0 + (y1 - y0) * t
            if i:
                x += rnd.uniform(-jitter, jitter)
                y += rnd.uniform(-jitter, jitter)
            pts.append(QPointF(x, y))

    edge(r.left(), r.top(), r.right(), r.top())
    edge(r.right(), r.top(), r.right(), r.bottom())
    edge(r.right(), r.bottom(), r.left(), r.bottom())
    edge(r.left(), r.bottom(), r.left(), r.top())
    return QPolygonF(pts)


_tex_pix: dict[bool, QPixmap] = {}


def texture(light: bool) -> QPixmap:
    if light not in _tex_pix:
        _tex_pix[light] = QPixmap(themes.texture_path(light))
    return _tex_pix[light]


def draw_tape(p: QPainter, center: QPointF, angle: float, length: float, rnd: random.Random, light: bool):
    """A strip of masking tape with torn ends."""
    p.save()
    p.translate(center)
    p.rotate(angle)
    h = 17
    half = length / 2
    pts = [QPointF(-half, -h / 2)]
    # top edge straight, torn right end, bottom straight, torn left end
    pts.append(QPointF(half, -h / 2))
    for i in range(1, 5):
        pts.append(QPointF(half + rnd.uniform(-3, 3), -h / 2 + h * i / 5))
    pts.append(QPointF(half, h / 2))
    pts.append(QPointF(-half, h / 2))
    for i in range(4, 0, -1):
        pts.append(QPointF(-half + rnd.uniform(-3, 3), -h / 2 + h * i / 5))
    poly = QPolygonF(pts)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(0, 0, 0, 50))
    p.drawPolygon(poly.translated(1.5, 2))
    p.setBrush(QColor(242, 232, 196, 205) if not light else QColor(255, 246, 205, 225))
    p.drawPolygon(poly)
    path = QPainterPath()
    path.addPolygon(poly)
    p.setClipPath(path)
    p.setOpacity(0.9)
    p.drawTiledPixmap(QRectF(-half - 4, -h, length + 8, h * 2), texture(True))
    p.setOpacity(1)
    p.setPen(QPen(QColor(150, 130, 90, 60), 1))
    p.drawLine(QPointF(-half, -h / 2 + 3), QPointF(half, -h / 2 + 3))
    p.restore()


class GrungePanel(QFrame):
    """A translucent tile with a scratched texture, a rough border and optional tape.

    The whole background is rendered once per size/theme and cached, so a repaint
    is a single blit.
    """

    def __init__(self, tape=(), seed=0):
        super().__init__()
        self.setObjectName("panel")
        self.tape = tuple(tape)
        self.seed = seed
        self._cache = None
        self._key = None

    def paintEvent(self, _):
        t = themes.current
        key = (self.width(), self.height(), t.key)
        if self._cache is None or self._key != key:
            self._cache = self._render(t)
            self._key = key
        QPainter(self).drawPixmap(0, 0, self._cache)

    def _render(self, t) -> QPixmap:
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        rnd = random.Random(hash((self.width(), self.height(), self.seed)))
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        poly = rough_rect(r, rnd)
        path = QPainterPath()
        path.addPolygon(poly)
        path.closeSubpath()
        p.fillPath(path, QColor(*t.panel_rgba()))
        p.setClipPath(path)
        p.drawTiledPixmap(r.adjusted(-4, -4, 4, 4), texture(t.light))
        p.setClipping(False)
        acc = QColor(t.accent)
        acc.setAlphaF(0.85)
        p.setPen(QPen(acc, 2.2))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(poly)
        acc.setAlphaF(0.3)
        p.setPen(QPen(acc, 1))
        p.drawPolygon(rough_rect(r.adjusted(3, 3, -3, -3), rnd, 1.2, 22))
        for corner in self.tape:
            if corner == "tl":
                draw_tape(p, QPointF(r.left() + 20, r.top() + 6), -38, 62, rnd, t.light)
            elif corner == "tr":
                draw_tape(p, QPointF(r.right() - 20, r.top() + 6), 38, 62, rnd, t.light)
            elif corner == "bl":
                draw_tape(p, QPointF(r.left() + 20, r.bottom() - 6), 38, 62, rnd, t.light)
            elif corner == "br":
                draw_tape(p, QPointF(r.right() - 20, r.bottom() - 6), -38, 62, rnd, t.light)
        p.end()
        return pm


def panel(layout=None, margins=(18, 16, 18, 16), tape=(), seed=0) -> GrungePanel:
    f = GrungePanel(tape, seed)
    if layout is None:
        layout = QVBoxLayout()
    layout.setContentsMargins(*margins)
    f.setLayout(layout)
    return f


class Background(QWidget):
    """Paints the theme picture, scaled to cover the window (cached; the picture is untouched)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self._theme = themes.THEMES[0]
        self._scaled = None
        self._scaled_for = None

    def set_theme(self, t: themes.Theme):
        self._theme = t
        self._scaled = None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        key = (self._theme.key, self.width(), self.height())
        if self._scaled is None or self._scaled_for != key:
            src = photo(self._theme.path)
            if not src.isNull():
                s = src.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                x = (s.width() - self.width()) // 2
                y = (s.height() - self.height()) // 2
                self._scaled = s.copy(x, y, self.width(), self.height())
            else:
                self._scaled = QPixmap(self.size())
                self._scaled.fill(Qt.black)
            self._scaled_for = key
        p.drawPixmap(0, 0, self._scaled)
        g = QLinearGradient(0, self.height() * 0.6, 0, self.height())
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(255, 255, 255, 60) if self._theme.light else QColor(0, 0, 0, 120))
        p.fillRect(self.rect(), g)


class RecButton(QPushButton):
    """The big REC tile: stamped square, rough edges, a strip of tape."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(84, 84)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Start / stop recording")
        self.recording = False
        self._rnd_seed = 3

    def set_recording(self, on: bool):
        self.recording = on
        self.update()

    def paintEvent(self, _):
        t = themes.current
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rnd = random.Random(self._rnd_seed)
        r = QRectF(self.rect()).adjusted(6, 8, -6, -4)
        poly = rough_rect(r, rnd, 1.8, 12)
        path = QPainterPath()
        path.addPolygon(poly)
        path.closeSubpath()
        p.fillPath(path.translated(3, 3), QColor(0, 0, 0, 110))
        base = QColor(t.rec)
        if self.underMouse():
            base = base.lighter(115)
        if self.isDown():
            base = base.darker(115)
        p.fillPath(path, base)
        p.setClipPath(path)
        p.drawTiledPixmap(r, texture(False))
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 230), 2.4))
        p.drawPolygon(poly)
        p.setPen(QColor("white"))
        if self.recording:
            p.setBrush(QColor("white"))
            p.setPen(Qt.NoPen)
            p.drawRect(QRectF(r.center().x() - 9, r.center().y() - 16, 18, 18))
            p.setPen(QColor("white"))
            p.setFont(head_font(8.5))
            p.drawText(QRectF(r.left(), r.center().y() + 4, r.width(), 22), Qt.AlignCenter, "STOP")
        else:
            p.setFont(head_font(14))
            p.drawText(r, Qt.AlignCenter, "REC")
        draw_tape(p, QPointF(r.left() + 10, r.top() + 2), -40, 40, rnd, False)


class StepBar(QWidget):
    """Setup progress: a row of square tiles."""

    def __init__(self, count: int):
        super().__init__()
        self.count = count
        self.current = 0
        self.setFixedSize(count * 24, 20)

    def set_current(self, i):
        self.current = i
        self.update()

    def paintEvent(self, _):
        t = themes.current
        p = QPainter(self)
        for i in range(self.count):
            r = QRectF(i * 24 + 2, 3, 14, 14)
            if i <= self.current:
                p.fillRect(r, QColor(t.accent if i == self.current else t.sub))
            else:
                p.setPen(QPen(QColor(t.sub), 2))
                p.drawRect(r)


def label(text, obj=None, wrap=False) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    if wrap:
        lb.setWordWrap(True)
    return lb


def icon_label(name: str, role="accent", size=20) -> QLabel:
    lb = QLabel()
    lb.setFixedSize(size + 4, size + 4)
    icons.bind(lb, name, role, size)
    return lb


def button(text, obj=None, tip=None, checkable=False, icon=None, role=None, icon_size=18) -> QPushButton:
    b = QPushButton(text)
    if obj:
        b.setObjectName(obj)
    if tip:
        b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    if icon:
        if role is None:
            role = "primary" if obj == "primary" else ("toggle" if checkable else "plain")
        icons.bind(b, icon, role, icon_size)
    return b


def tool_button(icon_name, tip, size=48) -> QPushButton:
    b = button("", "tool", tip, icon=icon_name, icon_size=22)
    b.setFixedSize(size, size)
    return b


def toggle(text, checked=False, tip=None, icon=None) -> QCheckBox:
    c = QCheckBox(text)
    c.setChecked(checked)
    c.setCursor(Qt.PointingHandCursor)
    if tip:
        c.setToolTip(tip)
    if icon:
        icons.bind(c, icon, "plain", 18)
    return c


def row(*widgets, stretch=True) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setSpacing(10)
    for w in widgets:
        if isinstance(w, int):
            h.addSpacing(w)
        elif w is None:
            h.addStretch(1)
        else:
            h.addWidget(w)
    if stretch:
        h.addStretch(1)
    return h


class VolumeSlider(QWidget):
    changed = Signal(int)

    def __init__(self, value=100):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.s = QSlider(Qt.Horizontal)
        self.s.setRange(0, 200)
        self.s.setValue(value)
        self.s.setMinimumWidth(180)
        self.lb = QLabel(f"{value}%")
        self.lb.setMinimumWidth(46)
        h.addWidget(self.s, 1)
        h.addWidget(self.lb)
        self.s.valueChanged.connect(self._on)

    def _on(self, v):
        self.lb.setText(f"{v}%")
        self.changed.emit(v)


class ThemeCard(QPushButton):
    """A square-cut thumbnail of a theme picture with its name stamped underneath."""

    def __init__(self, t: themes.Theme, w=168, h=104):
        super().__init__()
        self.theme = t
        self.setObjectName("card")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(t.name)
        self._w, self._h = w, h
        self.setFixedSize(QSize(w + 16, h + 40))
        src = photo(t.path).scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        self._thumb = src.copy((src.width() - w) // 2, (src.height() - h) // 2, w, h)

    def paintEvent(self, _):
        cur = themes.current
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rnd = random.Random(hash(self.theme.key))
        r = QRectF(8, 8, self._w, self._h)
        p.fillRect(r.translated(4, 4), QColor(0, 0, 0, 110))
        p.drawPixmap(r.toRect(), self._thumb)
        col = QColor(cur.accent) if self.isChecked() else QColor(cur.sub)
        if self.underMouse() and not self.isChecked():
            col = QColor(cur.accent2)
        p.setPen(QPen(col, 4 if self.isChecked() else 2))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(rough_rect(r, rnd, 1.2, 14))
        if self.isChecked():
            draw_tape(p, QPointF(r.right() - 14, r.top() + 2), 35, 48, rnd, cur.light)
        p.setPen(QColor(cur.accent) if self.isChecked() else QColor(cur.text))
        p.setFont(head_font(9.5))
        p.drawText(QRectF(0, self._h + 12, self.width(), 26), Qt.AlignCenter, self.theme.name.upper())


class Expand(QWidget):
    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)


# ---------- hotkey text conversion (Qt <-> pynput) ----------

_MODS = {"ctrl": "<ctrl>", "shift": "<shift>", "alt": "<alt>", "meta": "<cmd>"}


def qt_to_pynput(seq: str) -> str:
    """'Shift+F12' -> '<shift>+<f12>'"""
    if not seq:
        return ""
    out = []
    for part in seq.split("+"):
        k = part.strip().lower()
        if not k:
            continue
        if k in _MODS:
            out.append(_MODS[k])
        elif len(k) == 1:
            out.append(k)
        else:
            out.append(f"<{k.replace(' ', '_')}>")
    return "+".join(out)


def pynput_to_qt(hk: str) -> str:
    """'<shift>+<f12>' -> 'Shift+F12'"""
    if not hk:
        return ""
    rev = {v: k.capitalize() for k, v in _MODS.items()}
    rev["<cmd>"] = "Meta"
    parts = []
    for p in hk.split("+"):
        if p in rev:
            parts.append(rev[p])
        else:
            k = p.strip("<>")
            parts.append(k.upper() if (k.startswith("f") and k[1:].isdigit()) else k.capitalize())
    return "+".join(parts)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def hms(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
