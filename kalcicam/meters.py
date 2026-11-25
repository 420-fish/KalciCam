"""Live sound level bars for the desktop-sound and mic toggles.

Each meter opens its device at a low sample rate (mono, 16 kHz) and only redraws
about 15 times a second, so it costs next to nothing. Meters run only while the
KalciCam window is visible and the toggle is on.
"""

import array

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from . import themes


def _find_device(kind: str, wanted: str):
    """Qt audio input matching a KalciCam device id (PulseAudio name / DirectShow name)."""
    from PySide6.QtMultimedia import QMediaDevices

    devs = QMediaDevices.audioInputs()
    if not wanted:
        if kind == "mic":
            d = QMediaDevices.defaultAudioInput()
            return d if not d.isNull() else None
        from . import sysinfo

        wanted = sysinfo.default_desktop_source() if not sysinfo.paths.IS_WINDOWS else ""
        if not wanted:
            return None
    for d in devs:
        ident = bytes(d.id()).decode(errors="ignore")
        if ident == wanted or d.description() == wanted:
            return d
    return None


class LevelMeter(QWidget):
    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind  # "mic" | "desktop"
        self.setFixedSize(9, 38)
        self.level = 0.0
        self.peak = 0.0
        self.source = None
        self.io = None
        self._fmt_float = False
        self._timer = QTimer(self, interval=66, timeout=self._decay)
        self.setToolTip("Sound level")

    def start(self, device_id: str):
        self.stop()
        try:
            from PySide6.QtMultimedia import QAudioFormat, QAudioSource

            dev = _find_device(self.kind, device_id)
            if dev is None:
                self.setVisible(False)
                return
            fmt = QAudioFormat()
            fmt.setSampleRate(16000)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.Int16)
            if not dev.isFormatSupported(fmt):
                fmt = dev.preferredFormat()
            self._fmt_float = fmt.sampleFormat() == QAudioFormat.Float
            self._int32 = fmt.sampleFormat() == QAudioFormat.Int32
            self.source = QAudioSource(dev, fmt, self)
            self.source.setBufferSize(4096)
            self.io = self.source.start()
            if self.io is None:
                raise RuntimeError("could not open device")
            self.io.readyRead.connect(self._read)
            self._timer.start()
            self.setVisible(True)
        except Exception:
            self.stop()
            self.setVisible(False)

    def stop(self):
        self._timer.stop()
        if self.source is not None:
            try:
                self.source.stop()
            except Exception:
                pass
        self.source = self.io = None
        self.level = self.peak = 0.0
        self.update()

    def _read(self):
        data = bytes(self.io.readAll())
        if not data:
            return
        try:
            if self._fmt_float:
                a = array.array("f")
                a.frombytes(data[: len(data) // 4 * 4])
                peak = max((abs(x) for x in a), default=0.0)
            elif self._int32:
                a = array.array("i")
                a.frombytes(data[: len(data) // 4 * 4])
                peak = max((abs(x) for x in a), default=0) / 2147483648
            else:
                a = array.array("h")
                a.frombytes(data[: len(data) // 2 * 2])
                peak = max((abs(x) for x in a), default=0) / 32768
        except Exception:
            return
        self.level = max(self.level, min(1.0, peak ** 0.6))  # boost quiet sounds a bit

    def _decay(self):
        if self.level > self.peak:
            self.peak = self.level
        self.level *= 0.75
        self.peak = max(self.level, self.peak - 0.03)
        self.update()

    def paintEvent(self, _):
        t = themes.current
        p = QPainter(self)
        h = self.height()
        p.fillRect(self.rect(), QColor(0, 0, 0, 150))
        frame = QColor(t.accent)
        frame.setAlpha(140)
        p.setPen(frame)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.setPen(Qt.NoPen)
        segs = 9
        gap = 1
        seg_h = (h - gap * (segs - 1)) / segs
        lit = int(round(self.level * segs))
        for i in range(segs):
            y = h - (i + 1) * seg_h - i * gap
            if i < lit:
                c = QColor(t.rec) if i >= segs - 2 else (QColor("#ffcc33") if i >= segs - 4 else QColor(t.accent))
            else:
                c = QColor(255, 255, 255, 55)
            p.fillRect(QRectF(1, y, self.width() - 2, seg_h), c)
        if self.peak > 0.02:
            y = h - self.peak * h
            p.fillRect(QRectF(0, y, self.width(), 2), QColor(t.text))
