# PyInstaller spec for KalciCam (Windows test build and Linux AppImage).
# Build from the project root:  pyinstaller packaging/kalcicam.spec --noconfirm
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
IS_WIN = sys.platform.startswith("win")

from PyInstaller.utils.hooks import collect_submodules

hidden = []
if IS_WIN:
    hidden += ["pynput.keyboard._win32", "pynput.mouse._win32"]
else:
    # pynput picks its backend at runtime and python-xlib loads its X extensions with
    # __import__, so PyInstaller can't see them: without these, global hotkeys die
    # silently in the AppImage. pynput's modules are listed by name because
    # collect_submodules("pynput") has to import it, which needs an X display that
    # the build machine doesn't have (it then silently collects nothing).
    hidden += ["pynput.keyboard._xorg", "pynput.mouse._xorg", "pynput._util.xorg",
               "pynput._util.xorg_keysyms"]
    hidden += collect_submodules("Xlib")  # imports fine without a display
    hidden += ["dbus_fast", "dbus_fast.aio"]

# Big Qt parts KalciCam never uses: leaving them out keeps the download small.
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtQuick3D", "PySide6.QtDesigner", "PySide6.QtBluetooth",
    "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtTextToSpeech", "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSql", "PySide6.QtTest",
    "tkinter", "unittest", "pydoc",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "kalcicam" / "assets"), "kalcicam/assets")],
    hiddenimports=hidden,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="KalciCam" if IS_WIN else "kalcicam",
    console=False,
    icon=str(ROOT / "kalcicam" / "assets" / "icon.ico") if IS_WIN else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="KalciCam")
