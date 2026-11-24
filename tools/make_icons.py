"""Make the app icon from the sleepy cat in pastel_dream.webp.

Run once: python tools/make_icons.py  (writes kalcicam/assets/icon.png + icon.ico)
"""

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

ASSETS = Path(__file__).resolve().parent.parent / "kalcicam" / "assets"


def main():
    QGuiApplication(sys.argv)
    src = QImage(str(ASSETS / "pastel_dream.webp"))
    # The cat's head sits around the middle of the 2000x1125 picture.
    s = src.width() / 2000
    crop = src.copy(int(930 * s), int(350 * s), int(340 * s), int(340 * s))
    size = 256
    out = QImage(size, size, QImage.Format_ARGB32)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    r = QRectF(8, 8, size - 16, size - 16)
    path = QPainterPath()
    path.addRoundedRect(r, 58, 58)
    g = QLinearGradient(0, 0, 0, size)
    g.setColorAt(0, QColor("#9ff3fb"))
    g.setColorAt(1, QColor("#efb0f5"))
    p.fillPath(path, g)
    p.setClipPath(path)
    p.drawImage(r, crop)
    p.setClipping(False)
    p.setPen(QPen(QColor("#1b20a0"), 8))
    p.drawPath(path)
    # little REC dot
    p.setPen(QPen(QColor("white"), 6))
    p.setBrush(QColor("#ff3f9a"))
    p.drawEllipse(QRectF(size - 82, size - 82, 64, 64))
    p.end()
    out.save(str(ASSETS / "icon.png"))
    out.scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(ASSETS / "icon.ico"))
    print("wrote", ASSETS / "icon.png")


if __name__ == "__main__":
    main()
