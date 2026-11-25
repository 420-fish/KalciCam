"""Start-up: single instance, splash screen, first-run wizard, main window."""

import argparse
import os
import sys
import threading
import time

from . import APP_NAME, __version__, paths


def _parse(argv):
    p = argparse.ArgumentParser(prog="kalcicam", description=f"{APP_NAME}: lightweight screen recorder")
    g = p.add_mutually_exclusive_group()
    for c, h in (("toggle", "start or stop recording"), ("pause", "pause or resume"),
                 ("screenshot", "take a screenshot"), ("replay", "save the replay buffer"),
                 ("show", "show the window")):
        g.add_argument(f"--{c}", action="store_true", help=h)
    p.add_argument("--setup", action="store_true", help="run the setup wizard again")
    p.add_argument("--minimized", action="store_true", help="start hidden in the tray")
    p.add_argument("--selftest", action="store_true", help="record a few seconds and report what works")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return p.parse_args(argv)


def _preload_pynput():
    """Import pynput BEFORE Qt. PySide6 installs an import hook that breaks the
    `six` helper pynput uses ('_SixMetaPathImporter' has no '_path') if pynput is
    imported afterwards, which silently killed the global hotkeys."""
    try:
        import pynput.keyboard  # noqa: F401
        import pynput.mouse  # noqa: F401
    except Exception:
        pass  # e.g. no display; GlobalHotkeys reports the real reason later


def main(argv=None):
    paths.clean_environment()
    _preload_pynput()
    args = _parse(sys.argv[1:] if argv is None else argv)
    cmd = next((c for c in ("toggle", "pause", "screenshot", "replay", "show") if getattr(args, c)), None)

    if paths.IS_LINUX and os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY") \
            and "QT_QPA_PLATFORM" not in os.environ:
        # Run the UI through XWayland: Wayland won't let apps place always-on-top
        # overlays (HUD, webcam bubble). Capture itself still uses the PipeWire portal.
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    from PySide6.QtCore import QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
    from PySide6.QtWidgets import QApplication, QSplashScreen

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("kalcicam")
    app.setQuitOnLastWindowClosed(False)

    if args.selftest:
        from . import selftest

        return selftest.run(app)

    from . import hotkeys

    # Already running? Hand the command over and leave.
    if hotkeys.send_command(cmd or "show"):
        return 0

    from .widgets import app_icon, photo
    from . import themes

    themes.load_fonts()
    app.setWindowIcon(app_icon())

    # Splash: the cosmic picture with the app name on it.
    splash = None
    if not args.minimized and not cmd:
        src = photo(themes.get("cyber_chaos").path)
        w = 560
        h = round(src.height() * w / src.width())
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(0, 0, src.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
        p.fillRect(0, h - 78, w, 78, QColor(10, 4, 30, 200))
        p.fillRect(0, h - 78, w, 4, QColor("#ff7ad9"))
        f = QFont(themes.HEAD)
        f.setPointSize(24)
        p.setFont(f)
        p.setPen(QColor("#ff7ad9"))
        p.drawText(QRectF(22, h - 74, w, 50), Qt.AlignVCenter, APP_NAME.upper())
        f = QFont(themes.BODY)
        f.setPointSize(12)
        p.setFont(f)
        p.setPen(QColor("#d9ceff"))
        p.drawText(QRectF(24, h - 32, w, 26), Qt.AlignVCenter, f"V{__version__}  /  LOADING...")
        p.end()
        splash = QSplashScreen(pm, Qt.WindowStaysOnTopHint)
        splash.setAttribute(Qt.WA_TranslucentBackground)
        splash.show()
        app.processEvents()
    t0 = time.monotonic()

    from . import config, encoders
    from .main_window import MainWindow
    from .wizard import SetupWizard

    s = config.load()
    bridge = hotkeys.Bridge()
    server = hotkeys.CommandServer(bridge)  # noqa: F841  (kept alive by reference)

    def run_wizard(parent=None):
        wiz = SetupWizard(s, parent)
        wiz.exec()
        return wiz

    if splash:
        # Keep the splash up a moment so it doesn't just flicker.
        while time.monotonic() - t0 < 1.4:
            app.processEvents()
            time.sleep(0.02)

    if not s.first_run_done or args.setup:
        if splash:
            splash.close()
            splash = None
        run_wizard()
        if not s.first_run_done:
            s.first_run_done = True
            config.save(s)

    win = MainWindow(s, bridge)

    def setup_again():
        run_wizard(win)
        win.apply_theme(themes.get(s.theme))
        win._fill_encoders()
        win._update_status_static()
        win.dir_lbl.setText(str(s.video_dir()))
    win.request_setup.connect(setup_again)

    if not s.detected_encoder or s.detected_version != __version__:
        # First start, or KalciCam was updated: test the encoders again in the background.
        win.detect_encoder(advise=s.first_run_done)

    if args.minimized and win.tray:
        win.tray.showMessage(APP_NAME, "KalciCam is ready in the tray.", app_icon(), 2500)
    else:
        win.show()
    if splash:
        splash.finish(win)
    if cmd and cmd != "show":
        QTimer.singleShot(600, lambda: win.handle_command(cmd))

    return app.exec()
