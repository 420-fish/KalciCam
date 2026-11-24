"""First-run setup wizard: theme, dependencies, GPU test, folder, hotkeys, app menu."""

import os
import shutil
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFileDialog, QGridLayout, QHBoxLayout,
                               QLabel, QPlainTextEdit, QStackedWidget, QVBoxLayout, QWidget)

from . import APP_NAME, __version__, config, encoders, hotkeys, icons, paths, sysinfo, themes
from .widgets import (Background, StepBar, ThemeCard, app_icon, button, icon_label, label, panel, photo,
                      pynput_to_qt, row, toggle)


class _Sig(QObject):
    log = Signal(str)
    done = Signal(str)
    installed = Signal(bool)


class RoundPhoto(QWidget):
    def __init__(self, path, w, h):
        super().__init__()
        self.setFixedSize(w, h)
        src = photo(path).scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        self.pm = src.copy((src.width() - w) // 2, (src.height() - h) // 2, w, h)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 18, 18)
        p.setClipPath(path)
        p.drawPixmap(0, 0, self.pm)


class SetupWizard(QDialog):
    theme_changed = Signal(str)

    def __init__(self, s: config.Settings, parent=None):
        super().__init__(parent)
        self.s = s
        self.sig = _Sig()
        self.session = sysinfo.session_type()
        self.setWindowTitle(f"{APP_NAME} setup")
        self.setWindowIcon(app_icon())
        self.resize(900, 600)
        self.setMinimumSize(820, 560)

        self.bg = Background(self)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 18)
        self.stack = QStackedWidget()
        self.pages = [self._welcome(), self._look(), self._deps(), self._gpu(), self._folder(), self._finish()]
        for p in self.pages:
            self.stack.addWidget(p)
        card = panel(self._v(self.stack), (26, 24, 26, 20), tape=("tl", "tr"), seed=5)
        root.addWidget(card, 1)

        nav = QHBoxLayout()
        self.dots = StepBar(len(self.pages))
        self.back = button("Back", icon="back")
        self.next = button("Next", "primary", icon="next")
        self.back.clicked.connect(lambda: self._go(self.stack.currentIndex() - 1))
        self.next.clicked.connect(self._next)
        nav.addWidget(self.dots)
        nav.addStretch(1)
        nav.addWidget(self.back)
        nav.addWidget(self.next)
        root.addSpacing(10)
        root.addWidget(panel(nav, (18, 10, 16, 10), seed=6))

        self.sig.log.connect(self._log)
        self.sig.done.connect(self._gpu_done)
        self.sig.installed.connect(self._on_installed)
        self._apply_theme(themes.get(s.theme))
        self._go(0)

    def resizeEvent(self, e):
        self.bg.setGeometry(self.rect())
        self.bg.lower()
        super().resizeEvent(e)

    @staticmethod
    def _v(w=None):
        v = QVBoxLayout()
        if w is not None:
            v.addWidget(w)
        return v

    def _page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        return w, v

    def _apply_theme(self, t):
        themes.set_current(t)
        QApplication.instance().setStyleSheet(themes.stylesheet(t))
        icons.refresh()
        self.bg.set_theme(t)

    # ----------------------------------------------------------- pages
    def _welcome(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setSpacing(24)
        h.addWidget(RoundPhoto(themes.get("cyber_chaos").path, 340, 290))
        v = QVBoxLayout()
        v.addStretch(1)
        v.addWidget(label(f"Welcome to {APP_NAME}", "title"))
        v.addWidget(label("A screen recorder that's easy on your computer.\n\n"
                          "This quick setup will:\n"
                          "  • pick a look\n"
                          "  • check that everything needed is installed\n"
                          "  • find the fastest way to record on your graphics card\n"
                          "  • set up hotkeys and add KalciCam to your app menu\n\n"
                          "It takes about a minute.", "sub", wrap=True))
        sys_name = {"windows": "Windows (test build)", "x11": "Linux · X11", "wayland": "Linux · Wayland"}[self.session]
        d = sysinfo.distro().get("PRETTY_NAME", "")
        v.addWidget(label(f"Detected: {sys_name}{'  ·  ' + d if d else ''}", "stat"))
        v.addStretch(1)
        h.addLayout(v, 1)
        return w

    def _look(self):
        w, v = self._page()
        v.addWidget(label("Pick your look", "title"))
        v.addWidget(label("Each picture comes with its own colours. You can switch any time, or let them rotate.",
                          "sub", wrap=True))
        grid = QGridLayout()
        grid.setSpacing(12)
        self.tgroup = QButtonGroup(self)
        for i, t in enumerate(themes.THEMES):
            c = ThemeCard(t, 220, 124)
            c.setChecked(t.key == self.s.theme)
            self.tgroup.addButton(c, i)
            grid.addWidget(c, i // 3, i % 3)
        self.tgroup.idClicked.connect(self._pick_theme)
        v.addLayout(grid)
        self.rot = toggle("Rotate through all of them every 10 minutes", self.s.rotate_backgrounds)
        v.addWidget(self.rot)
        v.addStretch(1)
        return w

    def _pick_theme(self, i):
        t = themes.THEMES[i]
        self.s.theme = t.key
        self._apply_theme(t)
        self.theme_changed.emit(t.key)

    def _deps(self):
        w, v = self._page()
        v.addWidget(label("Checking your system", "title"))
        self.dep_box = QVBoxLayout()
        v.addLayout(self.dep_box)
        self.dep_cmd = label("", "code")
        self.dep_cmd.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.dep_cmd.setWordWrap(True)
        v.addWidget(self.dep_cmd)
        self.copy_btn = button("Copy command", icon="copy")
        self.copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._cmd_text))
        self.install_btn = button("Install it for me", "primary", icon="bolt", tip=
                                  
                                  "Asks for your password, then installs the missing packages")
        self.install_btn.clicked.connect(self._install)
        again = button("Check again", icon="refresh")
        again.clicked.connect(self._recheck)
        v.addLayout(row(self.install_btn, self.copy_btn, again))
        self.dep_log = QPlainTextEdit()
        self.dep_log.setReadOnly(True)
        self.dep_log.setMaximumHeight(120)
        self.dep_log.hide()
        v.addWidget(self.dep_log)
        v.addStretch(1)
        self._cmd_text = ""
        return w

    def _recheck(self):
        while self.dep_box.count():
            it = self.dep_box.takeAt(0)
            lay = it.layout()
            if lay:
                while lay.count():
                    x = lay.takeAt(0).widget()
                    if x:
                        x.deleteLater()
            elif it.widget():
                it.widget().deleteLater()
        checks = sysinfo.check_dependencies()
        missing = []
        for c in checks:
            mark = icon_label("check" if c.ok else ("cross" if c.required else "warn"),
                              "accent" if c.ok else "plain", 20)
            txt = label(f"<b>{c.label}</b>: {c.why}" + ("" if c.ok or c.required else "  <i>(optional)</i>"),
                        wrap=True)
            h = QHBoxLayout()
            h.addWidget(mark)
            h.addWidget(txt, 1)
            self.dep_box.addLayout(h)
            if not c.ok:
                missing.append(c.key)
        if self.session == "windows":
            self._cmd_text = "winget install Gyan.FFmpeg" if missing else ""
        else:
            self._cmd_text = sysinfo.install_command(missing)
        if not missing:
            self.dep_cmd.setText("Everything's here.")
        elif self._cmd_text:
            self.dep_cmd.setText("To install what's missing, run:\n" + self._cmd_text)
        else:
            self.dep_cmd.setText("Install the missing items with your distro's software centre.")
        can_auto = bool(missing) and self.session != "windows" and shutil.which("pkexec") and \
            sysinfo.package_manager() in ("apt", "dnf", "pacman", "zypper")
        self.install_btn.setVisible(bool(can_auto))
        self.copy_btn.setVisible(bool(self._cmd_text))

    def _install(self):
        self._run_install(self._cmd_text.replace("sudo ", "", 1).split(), self.dep_log, self.install_btn)

    def _run_install(self, cmd, log_box, btn):
        """Run a package install through pkexec (graphical password prompt)."""
        if cmd and cmd[0] == "pacman":
            cmd.append("--noconfirm")
        log_box.show()
        log_box.setPlainText("$ pkexec " + " ".join(cmd) + "\n")
        btn.setEnabled(False)
        self._install_btn_busy = btn

        def work():
            try:
                p = subprocess.Popen(["pkexec"] + cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in p.stdout:
                    self.sig.log.emit(line.rstrip())
                ok = p.wait() == 0
                self.sig.log.emit("Done." if ok else "The install didn't finish. Try the command in a terminal.")
            except Exception as e:
                ok = False
                self.sig.log.emit(str(e))
            self.sig.installed.emit(ok)
        threading.Thread(target=work, daemon=True).start()

    def _on_installed(self, ok):
        btn = getattr(self, "_install_btn_busy", None)
        if btn:
            btn.setEnabled(True)
        if self.stack.currentIndex() == 2:
            self._recheck()
        elif ok and self.stack.currentIndex() == 3:
            self._run_gpu()  # driver installed: test again straight away

    def _log(self, line):
        target = self.dep_log if self.stack.currentIndex() == 2 else self.gpu_log
        target.appendPlainText(line)

    def _gpu(self):
        w, v = self._page()
        v.addWidget(label("Finding your fastest encoder", "title"))
        v.addWidget(label("KalciCam tries each option for a split second and keeps the best one that works. "
                          "A GPU encoder means recording barely touches the CPU, so games stay smooth.",
                          "sub", wrap=True))
        self.gpu_log = QPlainTextEdit()
        self.gpu_log.setReadOnly(True)
        self.gpu_log.setMaximumHeight(160)
        v.addWidget(self.gpu_log)
        self.gpu_result = label("", "h2", wrap=True)
        v.addWidget(self.gpu_result)
        self.gpu_advice = label("", "sub", wrap=True)
        self.gpu_advice.hide()
        v.addWidget(self.gpu_advice)
        self.gpu_again = button("Test again", icon="refresh")
        self.gpu_again.clicked.connect(self._run_gpu)
        self.gpu_install = button("Install GPU driver", "primary", "Asks for your password", icon="bolt")
        self.gpu_install.clicked.connect(self._install_gpu)
        self.gpu_install.hide()
        self.gpu_dm = button("Open Driver Manager", icon="wand")
        self.gpu_dm.clicked.connect(self._open_driver_manager)
        self.gpu_dm.hide()
        v.addLayout(row(self.gpu_install, self.gpu_dm, self.gpu_again))
        v.addStretch(1)
        self._gpu_packages = []
        self._gpu_ran = False
        return w

    def _run_gpu(self):
        self._gpu_ran = True
        self.gpu_log.clear()
        self.gpu_result.setText("Testing...")
        self.gpu_again.setEnabled(False)
        self.next.setEnabled(False)

        def work():
            def show(lbl, ok, err=""):
                self.sig.log.emit(f"{'[ OK ]' if ok else '[FAIL]'}  {lbl}" + (f"\n        {err}" if err and not ok else ""))
            key = encoders.detect(show)
            self.sig.done.emit(key)
        threading.Thread(target=work, daemon=True).start()

    def _gpu_done(self, key):
        self.s.detected_encoder = key
        self.s.vaapi_device = encoders.VAAPI_DEVICE
        self.s.detected_version = __version__
        self.s.encoder = "auto"
        gpu = any(e.key == key and e.gpu for e in encoders.candidates())
        self.gpu_result.setText(f"Using {encoders.label(key)} " +
                                ("- your graphics card does the heavy lifting." if gpu else
                                 "(no GPU encoder found, so CPU mode is set to its lightest preset)"))
        self.gpu_again.setEnabled(True)
        self.next.setEnabled(True)
        # Explain how to get GPU recording working, with one-click fixes where possible.
        advice = [] if gpu else sysinfo.gpu_advice()
        self._gpu_packages = [p for a in advice for p in a.packages]
        self.gpu_advice.setText("\n\n".join(a.text for a in advice) +
                                ("\n\nAfter installing, press Test again." if advice else ""))
        self.gpu_advice.setVisible(bool(advice))
        can_pk = bool(self._gpu_packages) and shutil.which("pkexec") and sysinfo.package_manager()
        self.gpu_install.setVisible(bool(can_pk))
        self.gpu_dm.setVisible(any(a.driver_manager for a in advice) and sysinfo.driver_manager_command() is not None)

    def _install_gpu(self):
        pm = sysinfo.package_manager()
        self._run_install(sysinfo.INSTALL_CMD[pm].replace("sudo ", "", 1).split() + self._gpu_packages,
                          self.gpu_log, self.gpu_install)

    def _open_driver_manager(self):
        cmd = sysinfo.driver_manager_command()
        if cmd:
            subprocess.Popen(cmd, start_new_session=True)

    def _folder(self):
        w, v = self._page()
        v.addWidget(label("Where & how", "title"))
        v.addWidget(label("Recordings go to:", "h2"))
        self.dir_lbl = label(str(self.s.video_dir()), "sub")
        ch = button("Change", icon="folder")
        ch.clicked.connect(self._pick_dir)
        v.addLayout(row(self.dir_lbl, ch))

        v.addWidget(label("Hotkeys (Bandicam style)", "h2"))
        hk = self.s.hotkeys
        v.addWidget(label(f"{pynput_to_qt(hk['record'])}: start/stop   ·   {pynput_to_qt(hk['pause'])}: pause   ·   "
                          f"{pynput_to_qt(hk['screenshot'])}: screenshot   ·   {pynput_to_qt(hk['replay'])}: save replay",
                          "sub", wrap=True))
        if self.session == "wayland":
            v.addWidget(label("Wayland doesn't let apps listen for keys in the background, so these become desktop "
                              "keyboard shortcuts.", "hint", wrap=True))
            if hotkeys.is_gnome():
                self.gnome_hk = toggle("Add them to GNOME's keyboard shortcuts for me", True)
                v.addWidget(self.gnome_hk)
            else:
                exe = hotkeys.launch_command()
                c = label(f"In System Settings > Shortcuts, add:\n{exe} --toggle   (F12)\n{exe} --pause   "
                          f"(Shift+F12)\n{exe} --screenshot   (F11)\n{exe} --replay   (F10)", "code")
                c.setTextInteractionFlags(Qt.TextSelectableByMouse)
                v.addWidget(c)

        if paths.IS_LINUX:
            v.addWidget(label("App menu", "h2"))
            self.menu_entry = toggle("Add KalciCam to my app menu", True)
            v.addWidget(self.menu_entry)
            if os.environ.get("APPIMAGE"):
                v.addWidget(label("Tip: move the AppImage where you want to keep it first (for example ~/Applications), "
                                  "since the menu entry points to where it is now.", "hint", wrap=True))
        else:
            v.addWidget(label("Windows test build: nothing gets installed. Delete the folder to remove it.", "hint",
                              wrap=True))
        v.addStretch(1)
        return w

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Where should recordings go?", str(self.s.video_dir()))
        if d:
            self.s.save_dir = d
            self.dir_lbl.setText(d)

    def _finish(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setSpacing(24)
        v = QVBoxLayout()
        v.addStretch(1)
        v.addWidget(label("All set", "title"))
        self.finish_txt = label("", "sub", wrap=True)
        v.addWidget(self.finish_txt)
        v.addStretch(1)
        h.addLayout(v, 1)
        self.finish_photo = QVBoxLayout()
        h.addLayout(self.finish_photo)
        return w

    # ----------------------------------------------------------- navigation
    def _go(self, i):
        i = max(0, min(i, len(self.pages) - 1))
        self.stack.setCurrentIndex(i)
        self.back.setEnabled(i > 0)
        self.next.setText("Start recording" if i == len(self.pages) - 1 else "Next")
        self.dots.set_current(i)
        if i == 2:
            self._recheck()
        if i == 3 and not self._gpu_ran:
            self._run_gpu()
        if i == len(self.pages) - 1:
            self._apply_choices()

    def _next(self):
        i = self.stack.currentIndex()
        if i == len(self.pages) - 1:
            self.s.first_run_done = True
            config.save(self.s)
            self.accept()
        else:
            self._go(i + 1)

    def _apply_choices(self):
        notes = []
        self.s.rotate_backgrounds = self.rot.isChecked()
        if paths.IS_LINUX and getattr(self, "menu_entry", None) and self.menu_entry.isChecked():
            ok, msg = install_desktop_entry()
            notes.append(msg)
        if getattr(self, "gnome_hk", None) and self.gnome_hk.isChecked():
            notes.append(hotkeys.add_gnome_shortcuts(self.s.hotkeys)[1])
        hk = pynput_to_qt(self.s.hotkeys["record"])
        notes.insert(0, f"Press {hk} (or the big REC button) to start recording.\n"
                        f"Videos are saved in {self.s.video_dir()}.")
        self.finish_txt.setText("\n\n".join(notes))
        while self.finish_photo.count():
            self.finish_photo.takeAt(0).widget().deleteLater()
        self.finish_photo.addWidget(RoundPhoto(themes.get(self.s.theme).path, 300, 240))
        config.save(self.s)


def install_desktop_entry() -> tuple[bool, str]:
    """Add KalciCam to the Linux app menu (per user, no root needed)."""
    try:
        exe = hotkeys.launch_command()
        share = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        icon_dir = share / "icons" / "hicolor" / "256x256" / "apps"
        icon_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(paths.asset("icon.png"), icon_dir / "kalcicam.png")
        apps = share / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        (apps / "kalcicam.desktop").write_text(f"""[Desktop Entry]
Type=Application
Name=KalciCam
GenericName=Screen Recorder
Comment=Lightweight themed screen recorder
Exec={exe} %U
Icon=kalcicam
Terminal=false
Categories=AudioVideo;Video;Recorder;
Keywords=record;screen;capture;video;bandicam;
StartupWMClass=KalciCam
Actions=toggle;screenshot;replay;

[Desktop Action toggle]
Name=Start / stop recording
Exec={exe} --toggle

[Desktop Action screenshot]
Name=Take screenshot
Exec={exe} --screenshot

[Desktop Action replay]
Name=Save replay
Exec={exe} --replay
""", encoding="utf-8")
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", str(apps)], capture_output=True, timeout=10)
        return True, "KalciCam is now in your app menu."
    except Exception as e:
        return False, f"Couldn't add the menu entry: {e}"
