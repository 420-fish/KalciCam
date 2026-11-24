"""On-screen overlays: REC HUD, click ripples, webcam bubble, region picker.

The webcam bubble and click ripples are real windows on top of the screen, so
they show up in the recording on every backend (X11, Wayland, Windows) without
any extra video filtering.
"""

from PySide6.QtCore import QEasingCurve, QPoint, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import paths, sysinfo, themes
from .widgets import hms

OVERLAY_FLAGS = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool


def exclude_from_capture(w: QWidget):
    """Windows 10 2004+: keep this window out of screen recordings."""
    if not paths.IS_WINDOWS:
        return
    try:
        import ctypes

        ctypes.windll.user32.SetWindowDisplayAffinity(int(w.winId()), 0x11)  # WDA_EXCLUDEFROMCAPTURE
    except Exception:
        pass


# ---------------- REC HUD ----------------

class Hud(QWidget):
    def __init__(self):
        super().__init__(None, OVERLAY_FLAGS | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(250, 40)
        self.text = ""
        self.paused = False
        self.theme = themes.THEMES[0]
        self._blink = True

    def place(self, screen_idx: int):
        screens = QGuiApplication.screens()
        if not screens:
            return
        g = screens[max(0, min(screen_idx, len(screens) - 1))].availableGeometry()
        self.move(g.right() - self.width() - 18, g.top() + 18)

    def showEvent(self, e):
        super().showEvent(e)
        exclude_from_capture(self)

    def update_info(self, elapsed: float, fps: float, paused: bool):
        self.paused = paused
        self._blink = not self._blink
        f = f" · {fps:.0f} fps" if fps > 0 else ""
        self.text = ("PAUSED  " if paused else "REC  ") + hms(elapsed) + f
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(QColor(0, 0, 0, 185))
        p.setPen(QPen(QColor(self.theme.accent), 2))
        p.drawRect(r)
        dot = QColor("#ffcc33") if self.paused else QColor(self.theme.rec)
        if self._blink or self.paused:
            p.setBrush(dot)
            p.setPen(Qt.NoPen)
            p.drawRect(QRectF(14, 13, 14, 14))
        p.setPen(QColor("white"))
        f = QFont(themes.HEAD)
        f.setPointSize(10)
        p.setFont(f)
        p.drawText(QRectF(38, 0, self.width() - 44, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text)


# ---------------- click ripple ----------------

class _Ripple(QWidget):
    SIZE = 80

    def __init__(self):
        super().__init__(None, OVERLAY_FLAGS | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(self.SIZE, self.SIZE)
        self.color = QColor("#39ff6a")
        self.t = 0.0
        self.anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=380)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._tick)
        self.anim.finished.connect(self.hide)

    def _tick(self, v):
        self.t = v
        self.update()

    def fire(self, pos: QPoint, color: QColor):
        self.color = color
        self.move(pos.x() - self.SIZE // 2, pos.y() - self.SIZE // 2)
        self.show()
        self.anim.stop()
        self.anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self.color)
        c.setAlphaF(max(0.0, 0.9 * (1 - self.t)))
        rad = 8 + (self.SIZE / 2 - 10) * self.t
        p.setPen(QPen(c, 4))
        fill = QColor(c)
        fill.setAlphaF(c.alphaF() * 0.3)
        p.setBrush(fill)
        p.drawEllipse(QRectF(self.SIZE / 2 - rad, self.SIZE / 2 - rad, rad * 2, rad * 2))


class ClickHighlighter:
    """Listens for mouse clicks globally (Windows/X11) and shows a ripple."""

    def __init__(self, bridge):
        self.bridge = bridge  # QObject with a `clicked` Signal(bool)
        self.pool = [_Ripple() for _ in range(3)]
        self.i = 0
        self.listener = None
        self.theme = themes.THEMES[0]
        bridge.clicked.connect(self._on_click)

    @staticmethod
    def supported() -> bool:
        return sysinfo.session_type() != "wayland"

    def start(self):
        if self.listener or not self.supported():
            return
        try:
            from pynput import mouse

            def on_click(_x, _y, button, pressed):
                if pressed:
                    self.bridge.clicked.emit(button == mouse.Button.right)

            self.listener = mouse.Listener(on_click=on_click)
            self.listener.daemon = True
            self.listener.start()
        except Exception as e:
            print("KalciCam: click highlights unavailable:", e)

    def stop(self):
        if self.listener:
            self.listener.stop()
            self.listener = None

    def _on_click(self, right: bool):
        r = self.pool[self.i]
        self.i = (self.i + 1) % len(self.pool)
        r.fire(QCursor.pos(), QColor(self.theme.accent2 if right else self.theme.accent))


# ---------------- webcam bubble ----------------

class WebcamBubble(QWidget):
    """A round, draggable webcam window. Uses the camera's smallest suitable
    resolution so it costs very little CPU."""

    def __init__(self):
        super().__init__(None, OVERLAY_FLAGS | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.theme = themes.THEMES[0]
        self.frame = None
        self.camera = None
        self.session = None
        self.sink = None
        self._drag = None
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("Drag me anywhere. Right-click to hide.")
        self.resize(220, 220)

    def start(self, device_id: str, size: int):
        from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoSink

        self.stop()
        devs = QMediaDevices.videoInputs()
        if not devs:
            return False
        dev = next((d for d in devs if bytes(d.id()).decode(errors="ignore") == device_id), devs[0])
        self.camera = QCamera(dev)
        # Pick the smallest format that's still big enough for the bubble.
        fmts = sorted(dev.videoFormats(), key=lambda f: f.resolution().width() * f.resolution().height())
        good = [f for f in fmts if f.resolution().height() >= size and f.maxFrameRate() >= 24]
        if good:
            self.camera.setCameraFormat(good[0])
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.session = QMediaCaptureSession(self)
        self.session.setCamera(self.camera)
        self.session.setVideoSink(self.sink)
        self.resize(size, size)
        if not self.isVisible():
            g = QGuiApplication.primaryScreen().availableGeometry()
            self.move(g.left() + 24, g.bottom() - size - 24)
        self.camera.start()
        self.show()
        return True

    def stop(self):
        if self.camera:
            self.camera.stop()
        self.camera = self.session = self.sink = None
        self.frame = None
        self.hide()

    def _on_frame(self, f):
        img = f.toImage()
        if not img.isNull():
            self.frame = img
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        path = QPainterPath()
        path.addEllipse(r)
        p.setClipPath(path)
        if self.frame is not None:
            img: QImage = self.frame.mirrored(True, False)
            # cover-fit into the circle
            s = max(r.width() / img.width(), r.height() / img.height())
            w, h = img.width() * s, img.height() * s
            p.drawImage(QRectF(r.center().x() - w / 2, r.center().y() - h / 2, w, h), img)
        else:
            p.fillPath(path, QColor(0, 0, 0, 160))
        p.setClipping(False)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(self.theme.accent), 5))
        p.drawEllipse(r)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif e.button() == Qt.RightButton:
            self.hide()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag = None


# ---------------- region selector ----------------

class RegionSelector(QWidget):
    """Full-screen dimmed overlay: drag to choose the area to record."""

    chosen = Signal(dict)  # {screen, x, y, w, h} in physical pixels, relative to that screen
    cancelled = Signal()

    def __init__(self, screen_idx: int, accent: str):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.CrossCursor)
        screens = QGuiApplication.screens()
        self.idx = max(0, min(screen_idx, len(screens) - 1))
        self.scr = screens[self.idx]
        self.accent = QColor(accent)
        self.a = self.b = None
        self.setGeometry(self.scr.geometry())

    def go(self):
        self.setScreen(self.scr)
        self.showFullScreen()
        self.activateWindow()
        self.setFocus()

    def _rect(self) -> QRect:
        return QRect(self.a, self.b).normalized() if self.a and self.b else QRect()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dim = QColor(0, 0, 0, 110)
        r = self._rect()
        full = QPainterPath()
        full.addRect(QRectF(self.rect()))
        if not r.isEmpty():
            hole = QPainterPath()
            hole.addRect(QRectF(r))
            full = full.subtracted(hole)
        p.fillPath(full, dim)
        f = QFont(self.font())
        f.setPointSize(13)
        f.setBold(True)
        p.setFont(f)
        if r.isEmpty():
            p.setPen(QColor("white"))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Drag to pick the area to record\nEnter = use whole screen · Esc = cancel")
            return
        p.setPen(QPen(self.accent, 3, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)
        dpr = self.scr.devicePixelRatio()
        txt = f" {round(r.width() * dpr)} × {round(r.height() * dpr)} "
        p.setPen(Qt.NoPen)
        p.setBrush(self.accent)
        tw = p.fontMetrics().horizontalAdvance(txt) + 8
        ty = r.top() - 30 if r.top() > 34 else r.bottom() + 6
        p.drawRect(QRectF(r.left(), ty, tw, 26))
        p.setPen(QColor("black"))
        p.drawText(QRectF(r.left(), ty, tw, 26), Qt.AlignCenter, txt)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self._cancel()
            return
        self.a = self.b = e.position().toPoint()
        self.update()

    def mouseMoveEvent(self, e):
        if self.a:
            self.b = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, _):
        r = self._rect()
        if r.width() > 16 and r.height() > 16:
            self._accept(r)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._cancel()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._accept(self._rect() if not self._rect().isEmpty() else self.rect())

    def _accept(self, r: QRect):
        dpr = self.scr.devicePixelRatio()
        w, h = round(r.width() * dpr), round(r.height() * dpr)
        self.chosen.emit({"screen": self.idx, "x": round(r.x() * dpr), "y": round(r.y() * dpr),
                          "w": w - w % 2, "h": h - h % 2})
        self.close()

    def _cancel(self):
        self.cancelled.emit()
        self.close()
