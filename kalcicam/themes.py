"""The five themes (one per picture) and the metro-grunge stylesheet.

The pictures themselves are never modified: all the grunge (scratch texture,
rough edges, tape) is drawn on top of the UI in code.
"""

import random
from dataclasses import dataclass

from . import paths

HEAD = "Bungee"  # headings, buttons, timer
BODY = "Barlow Condensed"  # small text, so settings stay readable


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    image: str
    panel: str  # translucent panel colour "r,g,b,a"
    text: str
    sub: str
    accent: str
    accent2: str
    on_accent: str
    rec: str
    light: bool = False

    @property
    def path(self) -> str:
        return paths.asset(self.image)

    def panel_rgba(self):
        return tuple(int(x) for x in self.panel.split(","))


THEMES = [
    Theme("neon_space", "Theme 1", "neon_space.webp",
          panel="6,14,9,210", text="#dcffe4", sub="#8fd9a3", accent="#39ff6a", accent2="#9dffb5",
          on_accent="#03140a", rec="#ff3b5c"),
    Theme("purple_night", "Theme 2", "purple_night.webp",
          panel="36,6,62,215", text="#f5ebff", sub="#cfa8f2", accent="#2dff8e", accent2="#b46cff",
          on_accent="#14052a", rec="#ff4f8b"),
    Theme("pastel_dream", "Theme 3", "pastel_dream.webp",
          panel="255,255,255,200", text="#1c1650", sub="#4a4490", accent="#1b20a0", accent2="#c060e0",
          on_accent="#ffffff", rec="#ff3f9a", light=True),
    Theme("halftone_pop", "Theme 4", "halftone_pop.webp",
          panel="30,20,12,220", text="#fff3e6", sub="#f5b97a", accent="#f24bd0", accent2="#f2a03a",
          on_accent="#ffffff", rec="#ff3b3b"),
    Theme("cyber_chaos", "Theme 5", "cyber_chaos.webp",
          panel="16,10,42,215", text="#f4f0ff", sub="#b7a8ff", accent="#ff7ad9", accent2="#7ae1ff",
          on_accent="#1a0b2e", rec="#ff3b6b"),
]

BY_KEY = {t.key: t for t in THEMES}
current = THEMES[0]


def get(key: str) -> Theme:
    return BY_KEY.get(key, THEMES[0])


def set_current(t: Theme):
    global current
    current = t


def next_theme(key: str) -> Theme:
    i = [t.key for t in THEMES].index(get(key).key)
    return THEMES[(i + 1) % len(THEMES)]


def load_fonts():
    """Register the bundled fonts with Qt (call once, after QApplication exists)."""
    from PySide6.QtGui import QFontDatabase

    for f in ("Bungee-Regular.ttf", "BarlowCondensed-Medium.ttf", "BarlowCondensed-SemiBold.ttf"):
        QFontDatabase.addApplicationFont(paths.asset(f"fonts/{f}"))


# ---------------- scratch texture ----------------

_tex_paths: dict[bool, str] = {}


def texture_path(light: bool) -> str:
    """A tileable worn/scratched texture, generated once and cached as a PNG."""
    if light in _tex_paths:
        return _tex_paths[light]
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen

    size = 192
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    rnd = random.Random(7 if light else 11)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    ink = (40, 20, 60) if light else (255, 255, 255)
    dark = (0, 0, 0)
    # specks
    for _ in range(900):
        c = ink if rnd.random() < 0.6 else dark
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(*c, rnd.randint(8, 30)))
        r = rnd.uniform(0.4, 1.3)
        p.drawEllipse(QPointF(rnd.uniform(0, size), rnd.uniform(0, size)), r, r)
    # scratches (drawn wrapped so the tile repeats seamlessly)
    for _ in range(26):
        x, y = rnd.uniform(0, size), rnd.uniform(0, size)
        length = rnd.uniform(14, 60)
        dx = rnd.uniform(-1, 1)
        dy = rnd.uniform(-0.35, 0.35)
        p.setPen(QPen(QColor(*ink, rnd.randint(18, 45)), rnd.uniform(0.5, 1.1)))
        for ox in (-size, 0, size):
            for oy in (-size, 0, size):
                p.drawLine(QPointF(x + ox, y + oy), QPointF(x + ox + dx * length, y + oy + dy * length))
    p.end()
    path = paths.cache_dir() / f"grunge_{'light' if light else 'dark'}.png"
    img.save(str(path))
    _tex_paths[light] = str(path).replace("\\", "/")
    return _tex_paths[light]


def _rgba(hex_color: str, a: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{int(a * 255)})"


def stylesheet(t: Theme) -> str:
    tex = texture_path(t.light)
    field_bg = "rgba(255,255,255,215)" if t.light else "rgba(0,0,0,140)"
    soft = _rgba(t.accent, 0.10)
    hover = _rgba(t.accent, 0.24)
    border = _rgba(t.accent, 0.55)
    menu_bg = "#f4f2ff" if t.light else "#0c0c0c"
    return f"""
* {{ font-family: "{BODY}"; }}
QWidget {{ color: {t.text}; font-size: 11.5pt; font-weight: 500; }}
QMainWindow, QDialog {{ background: #000; }}
QFrame#panel {{ background: transparent; border: none; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-family: "{HEAD}"; font-size: 21pt; color: {t.accent}; }}
QLabel#h2 {{ font-family: "{HEAD}"; font-size: 11.5pt; color: {t.accent}; padding-top: 4px; }}
QLabel#sub, QLabel#hint {{ color: {t.sub}; }}
QLabel#tag {{ font-family: "{HEAD}"; font-size: 8pt; color: {t.sub}; letter-spacing: 2px; }}
QLabel#timer {{ font-family: "{HEAD}"; font-size: 46pt; color: {t.text}; }}
QLabel#stat {{ color: {t.sub}; font-weight: 600; font-size: 12pt; }}
QLabel#tile_ok, QLabel#tile_rec, QLabel#tile_busy {{ font-family: "{HEAD}"; font-size: 9pt; padding: 4px 10px; }}
QLabel#tile_ok {{ background: {t.accent}; color: {t.on_accent}; }}
QLabel#tile_rec {{ background: {t.rec}; color: white; }}
QLabel#tile_busy {{ background: {t.sub}; color: {'#ffffff' if t.light else '#000000'}; }}
QLabel#code {{ font-family: "JetBrains Mono", "Consolas", "DejaVu Sans Mono", monospace; font-size: 10pt;
              background: {field_bg}; border-left: 4px solid {t.accent}; padding: 8px; }}

QPushButton {{ font-family: "{HEAD}"; font-size: 9pt; background-color: {soft};
              background-image: url({tex}); border: 2px solid {border}; border-radius: 0; padding: 8px 14px; }}
QPushButton:hover {{ background-color: {hover}; border-color: {t.accent}; }}
QPushButton:pressed {{ background-color: {_rgba(t.accent, 0.38)}; }}
QPushButton:disabled {{ color: {_rgba(t.sub, 0.55)}; border-color: {_rgba(t.sub, 0.25)}; }}
QPushButton#primary {{ background-color: {t.accent}; color: {t.on_accent}; border: 2px solid {t.accent}; }}
QPushButton#primary:hover {{ background-color: {t.accent2}; border-color: {t.accent2}; }}
QPushButton#nav {{ font-size: 9.5pt; text-align: left; padding: 11px 12px; border: none;
                   border-left: 4px solid transparent; background-color: transparent; background-image: none; }}
QPushButton#nav:hover {{ background-color: {soft}; border-left-color: {border}; }}
QPushButton#nav:checked {{ background-color: {t.accent}; color: {t.on_accent}; border-left-color: {t.text};
                           background-image: url({tex}); }}
QPushButton#mode {{ padding: 10px 14px; }}
QPushButton#mode:checked {{ background-color: {t.accent}; color: {t.on_accent}; border-color: {t.accent}; }}
QPushButton#tool {{ padding: 0; }}
QPushButton#card {{ background: transparent; background-image: none; border: none; padding: 0; }}

QCheckBox {{ spacing: 10px; font-weight: 600; font-size: 12pt; background: transparent; }}
QCheckBox::indicator {{ width: 34px; height: 16px; border-radius: 0; background: {field_bg};
                        border: 2px solid {border}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
QCheckBox:disabled {{ color: {_rgba(t.sub, 0.55)}; }}

QComboBox, QLineEdit, QSpinBox, QKeySequenceEdit {{ background: {field_bg}; border: 2px solid {border};
    border-radius: 0; padding: 5px 9px; min-height: 20px; font-weight: 600;
    selection-background-color: {t.accent}; selection-color: {t.on_accent}; }}
QComboBox:hover, QSpinBox:hover, QKeySequenceEdit:hover {{ border-color: {t.accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {menu_bg}; border: 2px solid {t.accent}; border-radius: 0;
    selection-background-color: {t.accent}; selection-color: {t.on_accent}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; }}

QSlider::groove:horizontal {{ height: 6px; background: {field_bg}; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; }}
QSlider::handle:horizontal {{ width: 12px; height: 20px; margin: -7px 0; border-radius: 0;
    background: {t.text}; border: 2px solid {t.accent}; }}

QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 0; padding: 6px; color: {t.text}; }}
QListWidget::item:selected {{ background: {hover}; color: {t.text}; border: 2px solid {t.accent}; }}
QListWidget::item:hover {{ background: {soft}; }}
QTextEdit, QPlainTextEdit {{ background: {field_bg}; border: 2px solid {border}; border-radius: 0;
    font-family: "JetBrains Mono", "Consolas", monospace; font-size: 10pt; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ width: 8px; background: transparent; }}
QScrollBar::handle:vertical {{ background: {border}; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}

QToolTip {{ background: {menu_bg}; color: {t.text}; border: 2px solid {t.accent}; padding: 4px; }}
QMenu {{ background: {menu_bg}; border: 2px solid {t.accent}; padding: 4px; }}
QMenu::item {{ padding: 7px 20px 7px 10px; font-weight: 600; }}
QMenu::item:selected {{ background: {t.accent}; color: {t.on_accent}; }}
QMenu::separator {{ height: 2px; background: {border}; margin: 4px 6px; }}
QMessageBox QLabel {{ font-size: 12pt; }}
QFrame#section {{ background: {'rgba(255,255,255,120)' if t.light else 'rgba(0,0,0,70)'};
    border: 1px solid {_rgba(t.accent, 0.25)}; border-left: 4px solid {t.accent}; }}
QFrame#banner {{ background: {_rgba(t.rec, 0.18)}; border: 2px solid {t.rec}; }}
QFrame#tile {{ background: {'rgba(255,255,255,140)' if t.light else 'rgba(0,0,0,90)'};
    border: 2px solid {_rgba(t.accent, 0.35)}; }}
QLabel#tilenum {{ font-family: "{HEAD}"; font-size: 20pt; color: {t.accent}; }}
QLabel#toast {{ font-size: 12pt; font-weight: 600; padding: 12px 18px; color: {t.on_accent};
    background: {t.accent}; border: 2px solid {t.text}; }}
QLabel#toast[kind="error"] {{ background: {t.rec}; color: white; }}
QLabel#empty {{ color: {t.sub}; font-size: 13pt; padding: 40px; }}
QLabel#hk_ok {{ color: {t.accent}; font-weight: 700; }}
QLabel#hk_bad {{ color: {t.rec}; font-weight: 700; }}
"""
