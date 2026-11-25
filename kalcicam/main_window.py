"""The main KalciCam window (Bandicam-style layout, themed with the photos)."""

import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QKeySequence, QPainter, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QKeySequenceEdit, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QScrollArea, QSpinBox, QStackedWidget, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from . import APP_NAME, __version__, capture, config, encoders, hotkeys, icons, paths, sounds, sysinfo, themes
from .overlays import ClickHighlighter, Countdown, Hud, RegionSelector, WebcamBubble
from .recorder import WAYLAND, Recorder, ReplayBuffer, run_quiet, stamp
from .meters import LevelMeter
from .widgets import (Background, RecButton, Section, ThemeCard, Toast, VolumeSlider, app_icon, button, hms,
                      human_size, icon_label, label, panel, pynput_to_qt, qt_to_pynput, row, toggle, tool_button)

PAGES = ["Home", "Video", "Audio", "Overlays", "Extras", "Recordings", "Themes", "About"]
ICONS = {"Home": "home", "Video": "video", "Audio": "audio", "Overlays": "overlays", "Extras": "extras",
         "Recordings": "recordings", "Themes": "themes", "About": "about"}


class _Sig(QObject):
    thumb = Signal(str, str)
    info = Signal(str)
    encoder_done = Signal(str)
    window_picked = Signal(str, str)


class MainWindow(QMainWindow):
    request_setup = Signal()

    def __init__(self, s: config.Settings, bridge: hotkeys.Bridge):
        super().__init__()
        self.s = s
        self.bridge = bridge
        self.sig = _Sig()
        self.quitting = False
        self.session = sysinfo.session_type()
        self.theme = themes.get(s.theme)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(980, 640)

        self.recorder = Recorder(s)
        self.replay = ReplayBuffer(s)
        self.hud = Hud()
        self.clicks = ClickHighlighter(bridge)
        self.webcam = WebcamBubble()
        self.hotkeys = hotkeys.GlobalHotkeys(bridge)
        self._replay_resume = False
        self._last_saved = ""

        self._save_timer = QTimer(self, singleShot=True, interval=400, timeout=lambda: config.save(self.s))
        self._ui_timer = QTimer(self, interval=500, timeout=self._tick)  # only runs while recording
        self._rotate_timer = QTimer(self, timeout=self._rotate)

        self._build()
        self.toast = Toast(self.bg)
        self.countdown = Countdown()
        self.countdown.tick.connect(self._on_count)
        self.countdown.done.connect(self._count_done)
        self._build_tray()
        self.apply_theme(self.theme)
        self._apply_rotation()

        self.recorder.state_changed.connect(self._on_state)
        self.recorder.error.connect(self._show_error)
        self.recorder.saved.connect(self._on_saved)
        self.replay.error.connect(self._show_error)
        self.replay.saved.connect(self._on_saved)
        self.replay.state_changed.connect(self._on_replay_state)
        self.bridge.command.connect(self.handle_command)
        self.sig.thumb.connect(self._set_thumb)
        self.sig.info.connect(self._notify)
        self.sig.encoder_done.connect(self._on_encoder_detected)
        self.sig.window_picked.connect(self._on_window_picked)

        self._apply_hotkeys()
        self.recorder.recover_leftovers()
        if self.s.click_highlight:
            self.clicks.start()
        if self.s.replay_enabled:
            QTimer.singleShot(1500, self.replay.start)
        if self.s.compact:  # open in the size used last time
            self.set_compact(True)

    # ======================================================== layout
    def _build(self):
        self.bg = Background()
        self.setCentralWidget(self.bg)
        root = QVBoxLayout(self.bg)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(12)

        root.addWidget(self._build_header())

        # Everything below the toolbar lives in one container so "collapse" can hide it.
        self.body_w = QWidget()
        body = QHBoxLayout(self.body_w)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        nav_l = QVBoxLayout()
        nav_l.setSpacing(4)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for i, name in enumerate(PAGES):
            b = button(name.upper(), "nav", checkable=True, icon=ICONS[name], role="toggle")
            b.setMinimumWidth(150)
            self.nav_group.addButton(b, i)
            nav_l.addWidget(b)
        nav_l.addStretch(1)
        self.nav_group.idClicked.connect(self._go)
        body.addWidget(panel(nav_l, (12, 18, 12, 14), tape=("tl",), seed=2))

        self.stack = QStackedWidget()
        for name in PAGES:
            page = getattr(self, f"_page_{name.lower()}")()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
        body.addWidget(panel(self._wrap(self.stack), (20, 18, 14, 16), tape=("tr",), seed=3), 1)
        root.addWidget(self.body_w, 1)
        self.status_panel = self._build_status()
        root.addWidget(self.status_panel)
        self.nav_group.button(0).setChecked(True)

    @staticmethod
    def _wrap(w):
        v = QVBoxLayout()
        v.addWidget(w)
        return v

    def _build_header(self):
        outer = QVBoxLayout()
        outer.setSpacing(6)
        h = QHBoxLayout()
        h.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(46, 46))
        h.addWidget(logo)
        self.name_box = QWidget()
        name = QVBoxLayout(self.name_box)
        name.setContentsMargins(0, 0, 0, 0)
        name.setSpacing(0)
        name.addWidget(label(APP_NAME, "title"))
        self.sub_lbl = label("SCREEN RECORDER", "tag")
        name.addWidget(self.sub_lbl)
        h.addWidget(self.name_box)
        h.addSpacing(10)

        # Mode picker: one button with a dropdown, like a recorder's mode menu.
        self.mode_btn = button("", "mode", "Choose what to record", icon="monitor", role="plain")
        self.mode_btn.setMinimumWidth(190)
        self.mode_menu = QMenu(self)
        self.mode_menu.aboutToShow.connect(self._show_mode_menu)
        self.mode_btn.setMenu(self.mode_menu)
        h.addWidget(self.mode_btn)
        self.game_btn = button("Game", "mode", "One click: full screen, 60 fps, GPU encoder, desktop sound on, "
                               "cursor hidden, replay buffer ready", icon="gamepad")
        self.game_btn.clicked.connect(self.apply_game_preset)
        h.addWidget(self.game_btn)
        h.addSpacing(10)

        # Quick sound toggles with live level meters.
        self.spk_btn = tool_button("speaker", "Desktop sound on / off", 44)
        self.spk_btn.setCheckable(True)
        self.spk_meter = LevelMeter("desktop")
        self.mic_btn = tool_button("mic", "Microphone on / off", 44)
        self.mic_btn.setCheckable(True)
        self.mic_meter = LevelMeter("mic")
        self.spk_btn.toggled.connect(lambda on: self.set_audio("desktop_audio", on))
        self.mic_btn.toggled.connect(lambda on: self.set_audio("mic", on))
        for w in (self.spk_btn, self.spk_meter, self.mic_btn, self.mic_meter):
            h.addWidget(w)
        h.addStretch(1)

        self.shot_btn = tool_button("camera", "Screenshot")
        self.shot_btn.clicked.connect(self.take_screenshot)
        self.pause_btn = tool_button("pause", "Pause / resume")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self.recorder.pause_toggle)
        self.rec_btn = RecButton()
        self.rec_btn.clicked.connect(self.toggle_record)
        h.addWidget(self.shot_btn)
        h.addWidget(self.pause_btn)
        h.addSpacing(4)
        h.addWidget(self.rec_btn)
        self.collapse_btn = tool_button("chevron_up", "Make the window small (toolbar only)", 34)
        self.collapse_btn.clicked.connect(lambda: self.set_compact(not self.s.compact))
        h.addWidget(self.collapse_btn, 0, Qt.AlignTop)
        outer.addLayout(h)

        # Info bar: exactly what will be recorded.
        info = QHBoxLayout()
        info.setSpacing(8)
        self.info_icon = icon_label("monitor", "accent", 16)
        self.info_lbl = label("", "stat")
        info.addWidget(self.info_icon)
        info.addWidget(self.info_lbl, 1)
        self.monitor_box = QComboBox()
        self._fill_monitors()
        self.monitor_box.currentIndexChanged.connect(self._on_monitor)
        info.addWidget(self.monitor_box)
        outer.addLayout(info)
        self._sync_audio_widgets()
        self._update_mode_ui()
        return panel(outer, (18, 10, 14, 10), tape=("tl",), seed=1)

    def _build_status(self):
        h = QHBoxLayout()
        h.setSpacing(18)
        self.st_state = label("READY", "tile_ok")
        self.st_time = label("00:00:00", "stat")
        self.st_fps = label("", "stat")
        self.st_size = label("", "stat")
        self.st_enc = label("", "stat")
        for w in (self.st_state, self.st_time, self.st_fps, self.st_size):
            h.addWidget(w)
        h.addStretch(1)
        self.st_enc_icon = icon_label("bolt", "accent", 18)
        h.addWidget(self.st_enc_icon)
        h.addWidget(self.st_enc)
        folder = button("Open folder", icon="folder")
        folder.clicked.connect(lambda: self._open(str(self.s.video_dir())))
        h.addWidget(folder)
        self._update_status_static()
        return panel(h, (18, 9, 12, 9), seed=4)

    # ======================================================== pages
    def _page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 12, 12)
        v.setSpacing(14)
        return w, v

    def _page_home(self):
        w, v = self._page()
        top = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        self.timer_lbl = label("00:00:00", "timer")
        left.addWidget(self.timer_lbl)
        self.state_lbl = label("Ready to record.", "sub", wrap=True)
        left.addWidget(self.state_lbl)
        top.addLayout(left, 1)
        v.addLayout(top)

        # Shown only if the hotkeys aren't working, with a one-click fix.
        self.hk_banner = QFrame()
        self.hk_banner.setObjectName("banner")
        hb = QHBoxLayout(self.hk_banner)
        hb.setContentsMargins(12, 8, 12, 8)
        hb.addWidget(icon_label("warn", "plain", 20))
        self.hk_banner_lbl = label("", wrap=True)
        hb.addWidget(self.hk_banner_lbl, 1)
        self.hk_banner_btn = button("Fix it", "primary", icon="bolt")
        self.hk_banner_btn.clicked.connect(self._fix_hotkeys)
        hb.addWidget(self.hk_banner_btn)
        self.hk_banner.hide()
        v.addWidget(self.hk_banner)

        # Three steps, so nobody has to guess how it works.
        steps = QHBoxLayout()
        steps.setSpacing(10)
        hk = pynput_to_qt(self.s.hotkeys.get("record", ""))
        self.step_rec_lbl = None
        for n, title, text in (("1", "Pick", "Choose what to record with the mode button at the top."),
                               ("2", "Record", f"Press REC or {hk or 'your hotkey'}. Press again to stop."),
                               ("3", "Watch", "Your videos are in Recordings, ready to share.")):
            tile = QFrame()
            tile.setObjectName("tile")
            tl = QHBoxLayout(tile)
            tl.setContentsMargins(12, 10, 12, 10)
            tl.addWidget(label(n, "tilenum"))
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(label(title, "h2"))
            body = label(text, "sub", wrap=True)
            col.addWidget(body)
            if n == "2":
                self.step_rec_lbl = body
            tl.addLayout(col, 1)
            steps.addWidget(tile, 1)
        v.addLayout(steps)

        quick = Section("Quick switches", "bolt")
        self.q_desk = toggle("Desktop sound", self.s.desktop_audio, "Record what you hear", icon="speaker")
        self.q_mic = toggle("Microphone", self.s.mic, "Record your voice", icon="mic")
        self.q_cam = toggle("Webcam bubble", self.s.webcam, "Show your webcam in a circle", icon="webcam")
        self.q_mouse = toggle("Show cursor", self.s.draw_mouse, "Show the mouse pointer in the video", icon="cursor")
        grid = QGridLayout()
        grid.setHorizontalSpacing(30)
        grid.setVerticalSpacing(10)
        for i, c in enumerate((self.q_desk, self.q_mic, self.q_cam, self.q_mouse)):
            grid.addWidget(c, i // 2, i % 2)
        quick.add(grid)
        self.q_desk.toggled.connect(lambda on: self.set_audio("desktop_audio", on))
        self.q_mic.toggled.connect(lambda on: self.set_audio("mic", on))
        self.q_cam.toggled.connect(self._on_webcam_toggle)
        self.q_mouse.toggled.connect(lambda on: self._set("draw_mouse", on))
        v.addWidget(quick)

        keys = Section("Hotkeys", "keyboard")
        self.hotkey_hint = label("", "sub", wrap=True)
        keys.add(self.hotkey_hint)
        v.addWidget(keys)
        self._update_hotkey_hint()

        self.last_card = Section("Last recording", "play")
        self.last_lbl = label("", "sub", wrap=True)
        self.last_card.add(self.last_lbl)
        self.last_btn = button("Play", icon="play")
        self.last_btn.clicked.connect(lambda: self._open(self._last_saved))
        last_folder = button("Show in folder", icon="folder")
        last_folder.clicked.connect(lambda: self._open(os.path.dirname(self._last_saved)))
        self.last_card.add(row(self.last_btn, last_folder))
        self.last_card.hide()
        v.addWidget(self.last_card)
        v.addStretch(1)
        return w

    def _page_video(self):
        w, v = self._page()
        q = Section("Quality", "video", "Pick a preset, or change the numbers yourself.")
        self.preset_box = QComboBox()
        for key, text in (("gaming", "Gaming - 60 fps, balanced"), ("art", "Art / animation - 30 fps, sharp"),
                          ("small", "Small files - 30 fps"), ("custom", "Custom")):
            self.preset_box.addItem(text, key)
        self.preset_box.setCurrentIndex(max(0, self.preset_box.findData(self.s.preset)))
        self.preset_box.currentIndexChanged.connect(self._on_preset)
        q.form("Preset", self.preset_box)
        self.fps_box = QSpinBox()
        self.fps_box.setRange(10, 240)
        self.fps_box.setValue(self.s.fps)
        self.fps_box.setSuffix(" fps")
        self.fps_box.valueChanged.connect(lambda x: self._custom("fps", x))
        q.form("Frame rate", self.fps_box, hint="60 is smooth for games. 30 is fine for everything else.")
        self.q_box = QComboBox()
        for key, text in (("high", "High (bigger files)"), ("balanced", "Balanced"), ("small", "Small")):
            self.q_box.addItem(text, key)
        self.q_box.setCurrentIndex(self.q_box.findData(self.s.quality))
        self.q_box.currentIndexChanged.connect(lambda _: self._custom("quality", self.q_box.currentData()))
        q.form("Quality", self.q_box)
        v.addWidget(q)

        rec = Section("Recording", "rec")
        cd = QSpinBox()
        cd.setRange(0, 10)
        cd.setSuffix(" s")
        cd.setSpecialValueText("Off")
        cd.setValue(self.s.countdown)
        cd.valueChanged.connect(lambda x: self._set("countdown", x))
        rec.form("Countdown", cd, hint="A 3-2-1 before recording starts. Press REC again to cancel it.")
        snd = toggle("Play a little click when recording starts and stops", self.s.sounds)
        snd.toggled.connect(lambda on: self._set("sounds", on))
        rec.add(snd)
        v.addWidget(rec)

        e = Section("Encoder", "bolt", "Uses your graphics card when it can, so recording barely uses the CPU. "
                                        "'Auto' picks the best one that works on this computer.")
        self.enc_box = QComboBox()
        self._fill_encoders()
        self.enc_box.currentIndexChanged.connect(lambda _: self._set("encoder", self.enc_box.currentData(),
                                                                     after=self._update_status_static))
        retest = button("Test again", icon="search")
        retest.clicked.connect(self.detect_encoder)
        e.form("Encoder", self.enc_box, retest)
        v.addWidget(e)

        f = Section("Saving", "save")
        self.fmt_box = QComboBox()
        self.fmt_box.addItem("MP4 (plays everywhere)", "mp4")
        self.fmt_box.addItem("MKV", "mkv")
        self.fmt_box.setCurrentIndex(self.fmt_box.findData(self.s.final_format))
        self.fmt_box.currentIndexChanged.connect(lambda _: self._set("final_format", self.fmt_box.currentData()))
        f.form("File type", self.fmt_box, hint="KalciCam records safely as MKV first (nothing is lost if the "
                                               "computer crashes), then makes the MP4 in about a second.")
        keep = toggle("Also keep the MKV copy", self.s.keep_mkv)
        keep.toggled.connect(lambda on: self._set("keep_mkv", on))
        f.add(keep)
        self.dir_lbl = label(str(self.s.video_dir()), "sub")
        self.dir_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        change = button("Change", icon="folder")
        change.clicked.connect(self._pick_dir)
        f.form("Save videos in", self.dir_lbl, change)
        v.addWidget(f)

        if self.session == "windows":
            win = Section("Windows test options", "monitor")
            eng = QComboBox()
            eng.addItem("Desktop Duplication (GPU, recommended)", "ddagrab")
            eng.addItem("GDI (compatible, more CPU)", "gdigrab")
            eng.setCurrentIndex(eng.findData(self.s.win_capture_engine))
            eng.currentIndexChanged.connect(lambda _: self._set("win_capture_engine", eng.currentData()))
            win.form("Capture", eng)
            v.addWidget(win)
        v.addStretch(1)
        return w

    def _page_audio(self):
        w, v = self._page()
        desk, mics = sysinfo.audio_devices()
        d = Section("Desktop sound", "speaker", "Game sound, music, videos: whatever comes out of your speakers.")
        refresh = tool_button("refresh", "Look for new devices", 34)
        refresh.clicked.connect(self._refresh_audio)
        d.head_widget(refresh)
        self.a_desk = toggle("Record what I hear", self.s.desktop_audio)
        self.a_desk.toggled.connect(lambda on: self.set_audio("desktop_audio", on))
        d.add(self.a_desk)
        self.desk_box = self._device_box(desk, self.s.desktop_device, "desktop_device")
        d.form("Device", self.desk_box)
        dv = VolumeSlider(self.s.desktop_volume)
        dv.changed.connect(lambda x: self._set("desktop_volume", x))
        d.form("Volume", dv)
        if self.session == "windows":
            d.add(label("Windows test build: desktop sound needs a 'Stereo Mix' device (Sound settings > "
                        "Recording). On Linux it just works.", "hint", wrap=True))
        v.addWidget(d)

        m = Section("Microphone", "mic")
        self.a_mic = toggle("Record my microphone", self.s.mic)
        self.a_mic.toggled.connect(lambda on: self.set_audio("mic", on))
        m.add(self.a_mic)
        self.mic_box = self._device_box(mics, self.s.mic_device, "mic_device")
        m.form("Device", self.mic_box)
        mv = VolumeSlider(self.s.mic_volume)
        mv.changed.connect(lambda x: self._set("mic_volume", x))
        m.form("Volume", mv)
        v.addWidget(m)

        a = Section("Audio only mode", "audio", "Pick 'Audio only' in the mode menu at the top to record just "
                                                "sound, no video.")
        fmt = QComboBox()
        fmt.addItem("M4A (good quality, small)", "m4a")
        fmt.addItem("MP3 (plays everywhere)", "mp3")
        fmt.setCurrentIndex(max(0, fmt.findData(self.s.audio_format)))
        fmt.currentIndexChanged.connect(lambda _: (self._set("audio_format", fmt.currentData()),
                                                   self._update_mode_ui()))
        a.form("Save sound as", fmt)
        v.addWidget(a)
        v.addStretch(1)
        return w

    def _device_box(self, devs, current, key):
        b = QComboBox()
        b.setMinimumWidth(320)
        b.setMaximumWidth(460)
        for d in devs:
            b.addItem(d.name, d.id)
        i = b.findData(current)
        b.setCurrentIndex(max(0, i))
        b.currentIndexChanged.connect(lambda _: (self._set(key, b.currentData()), self._restart_meters()))
        return b

    def _refresh_audio(self):
        desk, mics = sysinfo.audio_devices()
        for box, devs, cur in ((self.desk_box, desk, self.s.desktop_device), (self.mic_box, mics, self.s.mic_device)):
            box.blockSignals(True)
            box.clear()
            for d in devs:
                box.addItem(d.name, d.id)
            box.setCurrentIndex(max(0, box.findData(cur)))
            box.blockSignals(False)
        self._notify(f"Found {len(desk) - 1} output(s) and {len(mics) - 1} microphone(s).")

    def _page_overlays(self):
        w, v = self._page()
        c = Section("Webcam bubble", "webcam", "Your webcam in a circle on top of the screen, so it's in the "
                                              "video. Drag it anywhere. Right-click it to hide it.")
        self.o_cam = toggle("Show my webcam", self.s.webcam)
        self.o_cam.toggled.connect(self._on_webcam_toggle)
        c.add(self.o_cam)
        self.cam_box = QComboBox()
        self.cam_box.setMinimumWidth(280)
        self.cam_box.addItem("Default camera", "")
        cam_refresh = tool_button("refresh", "Look for webcams", 34)
        cam_refresh.clicked.connect(self._fill_webcams)
        self.cam_box.currentIndexChanged.connect(lambda _: self._set("webcam_device", self.cam_box.currentData(),
                                                                     after=self._restart_webcam))
        c.form("Camera", self.cam_box, cam_refresh)
        size = QSpinBox()
        size.setRange(120, 480)
        size.setSingleStep(20)
        size.setSuffix(" px")
        size.setValue(self.s.webcam_size)
        size.valueChanged.connect(lambda x: self._set("webcam_size", x, after=self._restart_webcam))
        c.form("Size", size)
        v.addWidget(c)

        if paths.IS_WINDOWS:
            h = Section("Recording badge", "rec", "A small REC timer in the corner. You see it, but it's kept "
                                                  "out of the video.")
            hud = toggle("Show the REC badge while recording", self.s.hud)
        else:
            h = Section("Recording badge", "rec", "While recording, the KalciCam icon in your panel turns into a "
                                                  "red dot (yellow when paused). Point at it to see the time. "
                                                  "Linux can't hide windows from a recording, so the floating "
                                                  "badge would show up in your video.")
            hud = toggle("Also show a floating badge (it WILL be in the video)", self.s.hud)
        hud.toggled.connect(lambda on: self._set("hud", on, after=self._sync_hud))
        h.add(hud)
        v.addWidget(h)

        k = Section("Click highlights", "cursor", "A ripple where you click, handy for tutorials.")
        ch = toggle("Show a ripple where I click", self.s.click_highlight)
        ch.setEnabled(ClickHighlighter.supported())
        ch.toggled.connect(self._on_click_toggle)
        k.add(ch)
        if not ClickHighlighter.supported():
            k.add(label("Only works on X11 desktops like Linux Mint.", "hint", wrap=True))
        v.addWidget(k)
        v.addStretch(1)
        QTimer.singleShot(0, self._fill_webcams)
        return w

    def _fill_webcams(self):
        cur = self.s.webcam_device
        self.cam_box.blockSignals(True)
        self.cam_box.clear()
        self.cam_box.addItem("Default camera", "")
        for d in sysinfo.webcams():
            self.cam_box.addItem(d.name, d.id)
        self.cam_box.setCurrentIndex(max(0, self.cam_box.findData(cur)))
        self.cam_box.blockSignals(False)

    def _page_extras(self):
        w, v = self._page()
        hk = Section("Hotkeys", "keyboard", "Click a box, then press the key you want.")
        st = QHBoxLayout()
        self.hk_status_icon = icon_label("check", "accent", 18)
        self.hk_status = label("", "hk_ok", wrap=True)
        st.addWidget(self.hk_status_icon)
        st.addWidget(self.hk_status, 1)
        hk.add(st)
        self.hk_edits = {}
        for key, text in (("record", "Start / stop"), ("pause", "Pause / resume"),
                          ("screenshot", "Screenshot"), ("replay", "Save replay")):
            e = QKeySequenceEdit(QKeySequence(pynput_to_qt(self.s.hotkeys.get(key, ""))))
            e.setMaximumSequenceLength(1)
            e.setMaximumWidth(220)
            e.editingFinished.connect(lambda k=key, e=e: self._on_hotkey(k, e))
            hk.form(text, e)
            self.hk_edits[key] = e
        reset = button("Reset to F12 / Shift+F12 / F11 / F10", icon="refresh")
        reset.clicked.connect(self._reset_hotkeys)
        hk.add(row(reset))
        if hotkeys.system_shortcuts_available():
            self.hk_sys = toggle(f"Use {hotkeys.desktop_name()}'s keyboard shortcuts instead", self.s.hotkeys_system)
            self.hk_sys.setToolTip("Lets your desktop handle the keys. Use this if the hotkeys don't react.")
            self.hk_sys.toggled.connect(self._on_hotkeys_system)
            hk.add(self.hk_sys)
        if not hotkeys.GlobalHotkeys.supported():
            exe = hotkeys.launch_command()
            hk.add(label("If your desktop isn't listed above, add these as keyboard shortcuts in your "
                         "system settings:", "hint", wrap=True))
            code = label(f"{exe} --toggle\n{exe} --pause\n{exe} --screenshot\n{exe} --replay", "code")
            code.setTextInteractionFlags(Qt.TextSelectableByMouse)
            hk.add(code)
        v.addWidget(hk)

        rb = Section("Replay buffer", "save", "Keeps the last few seconds ready, so you can save a clip after "
                                              "something cool happens.")
        self.rb_toggle = toggle("Keep a replay buffer running", self.s.replay_enabled)
        self.rb_toggle.toggled.connect(self._on_replay_toggle)
        rb.add(self.rb_toggle)
        secs = QSpinBox()
        secs.setRange(10, 300)
        secs.setSingleStep(10)
        secs.setSuffix(" seconds")
        secs.setValue(self.s.replay_seconds)
        secs.valueChanged.connect(lambda x: self._set("replay_seconds", x))
        save_now = button("Save replay now", icon="save")
        save_now.clicked.connect(self.replay.save)
        rb.form("Length", secs, save_now)
        self.rb_state = label("Buffer off", "sub")
        rb.form("Status", self.rb_state)
        v.addWidget(rb)

        win = Section("Window", "window")
        tray = toggle("Closing the window keeps KalciCam running in the panel", self.s.minimize_to_tray)
        tray.toggled.connect(lambda on: self._set("minimize_to_tray", on))
        win.add(tray)
        v.addWidget(win)
        v.addStretch(1)
        return w

    def _page_recordings(self):
        w, v = self._page()
        head = QHBoxLayout()
        head.addWidget(label("Your recordings", "h2"))
        self.rec_count = label("", "sub")
        head.addWidget(self.rec_count)
        head.addStretch(1)
        refresh = button("Refresh", icon="refresh")
        refresh.clicked.connect(self.refresh_recordings)
        head.addWidget(refresh)
        v.addLayout(head)
        self.rec_empty = label("No recordings yet.\nPress REC (or your hotkey) to make your first one.", "empty")
        self.rec_empty.setAlignment(Qt.AlignCenter)
        self.rec_empty.hide()
        v.addWidget(self.rec_empty)
        self.rec_list = QListWidget()
        self.rec_list.setViewMode(QListWidget.IconMode)
        self.rec_list.setIconSize(QSize(208, 117))
        self.rec_list.setGridSize(QSize(236, 182))
        self.rec_list.setResizeMode(QListWidget.Adjust)
        self.rec_list.setMovement(QListWidget.Static)
        self.rec_list.setWordWrap(True)
        self.rec_list.setMinimumHeight(340)
        self.rec_list.setToolTip("Double-click to play. Right-click for more.")
        self.rec_list.itemDoubleClicked.connect(lambda it: self._open(it.data(Qt.UserRole)))
        self.rec_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.rec_list.customContextMenuRequested.connect(self._rec_menu)
        v.addWidget(self.rec_list, 1)
        play = button("Play", icon="play")
        play.clicked.connect(lambda: self._rec_action("play"))
        show = button("Show in folder", icon="folder")
        show.clicked.connect(lambda: self._rec_action("folder"))
        delete = button("Move to trash", icon="trash")
        delete.clicked.connect(lambda: self._rec_action("trash"))
        self.rec_buttons = (play, show, delete)
        v.addLayout(row(play, show, delete))
        return w

    def _page_themes(self):
        w, v = self._page()
        t = Section("Background", "themes", "Each picture comes with its own colours for the whole app.")
        grid = QGridLayout()
        grid.setSpacing(12)
        self.theme_group = QButtonGroup(self)
        for i, th in enumerate(themes.THEMES):
            c = ThemeCard(th, 200, 120)
            c.setChecked(th.key == self.theme.key)
            self.theme_group.addButton(c, i)
            grid.addWidget(c, i // 3, i % 3)
        self.theme_group.idClicked.connect(lambda i: self.set_theme(themes.THEMES[i].key))
        t.add(grid)
        rot = toggle("Change the picture automatically", self.s.rotate_backgrounds)
        mins = QSpinBox()
        mins.setRange(1, 240)
        mins.setSuffix(" min")
        mins.setValue(self.s.rotate_minutes)
        rot.toggled.connect(lambda on: self._set("rotate_backgrounds", on, after=self._apply_rotation))
        mins.valueChanged.connect(lambda x: self._set("rotate_minutes", x, after=self._apply_rotation))
        t.add(row(rot, label("every"), mins))
        v.addWidget(t)
        v.addStretch(1)
        return w

    def _page_about(self):
        w, v = self._page()
        a = Section(f"{APP_NAME} {__version__}", "about")
        st = {"windows": "Windows (test build)", "x11": "Linux (X11)", "wayland": "Linux (Wayland)"}[self.session]
        be = {"ffmpeg_win": "FFmpeg + Desktop Duplication", "ffmpeg_linux": "FFmpeg + x11grab",
              "gst": "GStreamer + PipeWire"}[encoders.backend()]
        self.about_info = label("", "sub", wrap=True)
        self.about_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._about_text = f"System: {st}\nCapture: {be}\nSettings: {paths.config_dir()}"
        self.about_info.setText(self._about_text)
        a.add(self.about_info)
        again = button("Run setup again", "primary", icon="wand")
        again.clicked.connect(self.request_setup.emit)
        report = button("Copy info for help", icon="copy")
        report.setToolTip("Copies a short report (versions, encoder, hotkeys) to send to whoever helps you.")
        report.clicked.connect(self._copy_report)
        log = button("Open log", icon="folder")
        log.clicked.connect(lambda: self._open(str(paths.cache_dir() / "kalcicam.log")))
        a.add(row(again, report, log))
        v.addWidget(a)
        why = Section("Why it's light on your computer", "bolt")
        why.add(label(
            "- Video is made by the graphics card whenever possible.\n"
            "- There's no live preview, and the window barely redraws while recording.\n"
            "- Pausing and making the MP4 copy the video instead of re-making it.\n"
            "- The background picture is drawn once and reused.", "sub", wrap=True))
        v.addWidget(why)
        v.addStretch(1)
        return w

    # ======================================================== tray
    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        m = QMenu()
        self.t_rec = QAction("Start recording", self, triggered=self.toggle_record)
        self.t_pause = QAction("Pause", self, triggered=self.recorder.pause_toggle, enabled=False)
        shot = QAction("Screenshot", self, triggered=self.take_screenshot)
        rp = QAction("Save replay", self, triggered=self.replay.save)
        show = QAction("Show KalciCam", self, triggered=self.bring_up)
        quit_ = QAction("Quit", self, triggered=self.quit)
        for a, ic in ((self.t_rec, "rec"), (self.t_pause, "pause"), (shot, "camera"), (rp, "save"),
                      (show, "monitor"), (quit_, "power")):
            icons.bind(a, ic, "plain", 16)
        for a in (self.t_rec, self.t_pause, shot, rp):
            m.addAction(a)
        m.addSeparator()
        m.addAction(show)
        m.addAction(quit_)
        self.tray.setContextMenu(m)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(lambda r: self.bring_up() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _tray_icon(self, state: str) -> QIcon:
        """Tray icon per state. While recording it's a bold red dot (yellow pause bars
        when paused) so it reads clearly at panel size. The tray lives outside the
        captured picture in fullscreen apps, unlike an on-screen badge."""
        if state not in ("recording", "paused"):
            return app_icon()
        ic = QIcon()
        for size in (16, 22, 24, 32, 48, 64):
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            m = size * 0.06
            ring = max(1.0, size / 16)
            if state == "recording":
                p.setBrush(QColor(self.theme.rec))
                p.setPen(QPen(QColor("white"), ring))
                p.drawEllipse(QRectF(m, m, size - 2 * m, size - 2 * m))
            else:
                p.setBrush(QColor("#ffcc33"))
                p.setPen(QPen(QColor("black"), ring))
                p.drawEllipse(QRectF(m, m, size - 2 * m, size - 2 * m))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("black"))
                bw, bh = size * 0.13, size * 0.42
                p.drawRect(QRectF(size * 0.33, (size - bh) / 2, bw, bh))
                p.drawRect(QRectF(size * 0.54, (size - bh) / 2, bw, bh))
            p.end()
            ic.addPixmap(pm)
        return ic

    def _update_tray_tooltip(self, elapsed: float = 0.0):
        if not self.tray:
            return
        st = self.recorder.state
        if st == "recording":
            self.tray.setToolTip(f"{APP_NAME} - REC {hms(elapsed)}")
        elif st == "paused":
            self.tray.setToolTip(f"{APP_NAME} - PAUSED {hms(elapsed)}")
        else:
            self.tray.setToolTip(APP_NAME)

    # ======================================================== theme
    def apply_theme(self, t: themes.Theme):
        self.theme = t
        themes.set_current(t)
        QApplication.instance().setStyleSheet(themes.stylesheet(t))
        icons.refresh()
        self.bg.set_theme(t)
        self.hud.theme = t
        self.clicks.theme = t
        self.webcam.theme = t
        self.webcam.update()
        self.rec_btn.update()

    def set_theme(self, key: str):
        self._set("theme", key)
        self.apply_theme(themes.get(key))
        b = self.theme_group.button([x.key for x in themes.THEMES].index(key))
        if b:
            b.setChecked(True)

    def _apply_rotation(self):
        if self.s.rotate_backgrounds:
            self._rotate_timer.start(max(1, self.s.rotate_minutes) * 60_000)
        else:
            self._rotate_timer.stop()

    def _rotate(self):
        self.set_theme(themes.next_theme(self.s.theme).key)

    # ======================================================== settings helpers
    def _set(self, key, value, sync=None, after=None):
        setattr(self.s, key, value)
        self._save_timer.start()
        if sync is not None and sync.isChecked() != value:
            sync.blockSignals(True)
            sync.setChecked(value)
            sync.blockSignals(False)
        if after:
            after()

    def _custom(self, key, value):
        self._set(key, value)
        if config.PRESETS.get(self.s.preset) != (self.s.fps, self.s.quality):
            self.s.preset = "custom"
            self.preset_box.blockSignals(True)
            self.preset_box.setCurrentIndex(self.preset_box.findData("custom"))
            self.preset_box.blockSignals(False)

    def _on_preset(self, _):
        key = self.preset_box.currentData()
        if key == "custom":
            self.s.preset = "custom"
        else:
            self.s.apply_preset(key)
            for wdg, val in ((self.fps_box, self.s.fps), (self.q_box, self.q_box.findData(self.s.quality))):
                wdg.blockSignals(True)
                (wdg.setValue if wdg is self.fps_box else wdg.setCurrentIndex)(val)
                wdg.blockSignals(False)
        self._save_timer.start()

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Where should recordings go?", str(self.s.video_dir()))
        if d:
            self._set("save_dir", d)
            self.dir_lbl.setText(d)

    def _fill_monitors(self):
        self.monitor_box.blockSignals(True)
        self.monitor_box.clear()
        for i, scr in enumerate(QGuiApplication.screens()):
            g = scr.geometry()
            dpr = scr.devicePixelRatio()
            self.monitor_box.addItem(f"Monitor {i + 1} ({round(g.width() * dpr)}×{round(g.height() * dpr)})", i)
        self.monitor_box.setCurrentIndex(min(self.s.monitor, self.monitor_box.count() - 1))
        self.monitor_box.blockSignals(False)
        if self.session == "wayland":
            self.monitor_box.setToolTip("On Wayland you pick the screen in the system's share dialog.")

    def _fill_encoders(self):
        self.enc_box.blockSignals(True)
        self.enc_box.clear()
        det = encoders.label(self.s.detected_encoder) if self.s.detected_encoder else "not tested yet"
        self.enc_box.addItem(f"Auto: {det}", "auto")
        for e in encoders.candidates():
            self.enc_box.addItem(e.label, e.key)
        self.enc_box.setCurrentIndex(max(0, self.enc_box.findData(self.s.encoder)))
        self.enc_box.blockSignals(False)

    def detect_encoder(self, advise=False):
        """Test encoders in the background. With `advise`, explain how to get GPU
        recording working if only the CPU encoder works."""
        self.st_enc.setText("Testing encoders...")
        self._advise_after_detect = advise

        def work():
            self.sig.encoder_done.emit(encoders.detect())
        threading.Thread(target=work, daemon=True).start()

    def _on_encoder_detected(self, key):
        self.s.vaapi_device = encoders.VAAPI_DEVICE
        self.s.detected_version = __version__
        self._set("detected_encoder", key)
        self._fill_encoders()
        self._update_status_static()
        self._notify(f"Best encoder: {encoders.label(key)}")
        gpu = any(e.key == key and e.gpu for e in encoders.candidates())
        if getattr(self, "_advise_after_detect", False) and not gpu:
            self._advise_after_detect = False
            advice = sysinfo.gpu_advice()
            if advice:
                box = QMessageBox(self)
                box.setWindowTitle(APP_NAME)
                box.setText("KalciCam is recording with the CPU, which uses a lot of power.\n\n"
                            + "\n\n".join(a.text for a in advice)
                            + "\n\nThe setup wizard can fix this for you.")
                setup = box.addButton("Open setup", QMessageBox.AcceptRole)
                box.addButton("Later", QMessageBox.RejectRole)
                box.exec()
                if box.clickedButton() is setup:
                    self.request_setup.emit()

    def _update_status_static(self):
        key = self.s.encoder if self.s.encoder != "auto" else self.s.detected_encoder
        self.st_enc.setText(encoders.label(key) if key else "")

    def _update_hotkey_hint(self):
        hk = self.s.hotkeys
        parts = [f"{pynput_to_qt(hk.get('record'))}: start/stop", f"{pynput_to_qt(hk.get('pause'))}: pause",
                 f"{pynput_to_qt(hk.get('screenshot'))}: screenshot", f"{pynput_to_qt(hk.get('replay'))}: save replay"]
        txt = "   ·   ".join(parts)
        self.hotkey_hint.setText(txt)
        if getattr(self, "step_rec_lbl", None) is not None:
            rec = pynput_to_qt(hk.get("record", ""))
            self.step_rec_lbl.setText(f"Press REC or {rec or 'your hotkey'}. Press again to stop.")

    def _on_hotkey(self, key, edit):
        seq = edit.keySequence().toString()
        self.s.hotkeys[key] = qt_to_pynput(seq)
        self._save_timer.start()
        self._apply_hotkeys()
        self._notify(f"Hotkey set: {seq or 'none'}")

    def _reset_hotkeys(self):
        self.s.hotkeys = dict(config.DEFAULT_HOTKEYS)
        for k, e in self.hk_edits.items():
            e.setKeySequence(QKeySequence(pynput_to_qt(self.s.hotkeys[k])))
        self._save_timer.start()
        self._apply_hotkeys()
        self._notify("Hotkeys reset to F12 / Shift+F12 / F11 / F10.")

    # ======================================================== mode / target
    MODE_LABELS = {
        "region": ("Select area", "region"),
        "rect": ("Rectangle", "rect"),
        "fullscreen": ("Fullscreen", "monitor"),
        "window": ("Window", "window"),
        "mouse": ("Around mouse", "mouse_area"),
        "audio": ("Audio only", "audio"),
    }
    SIZES = [(1920, 1080), (1280, 720), (854, 480), (640, 360), (1080, 1080), (720, 720),
             (1080, 1920), (720, 1280)]

    @staticmethod
    def _size_name(w, h):
        tag = " (square)" if w == h else (" (vertical)" if h > w else "")
        return f"{w} x {h}{tag}"

    def _show_mode_menu(self):
        """Rebuilt each time it opens, so monitors and sizes are always current."""
        tc = self.theme.text
        m = self.mode_menu
        m.clear()
        a = m.addAction(icons.icon("region", tc), "Select a recording area",
                        lambda: self._set_mode("region"))
        rect = m.addMenu(icons.icon("rect", tc), "Rectangle on a screen")
        for w, h in self.SIZES:
            rect.addAction(self._size_name(w, h), lambda w=w, h=h: self._set_mode("rect", size=(w, h)))
        full = m.addMenu(icons.icon("monitor", tc), "Fullscreen")
        for i in range(self.monitor_box.count()):
            full.addAction(self.monitor_box.itemText(i), lambda i=i: self._set_mode("fullscreen", monitor=i))
        m.addAction(icons.icon("window", tc), "Specific window", lambda: self._set_mode("window"))
        mouse = m.addMenu(icons.icon("mouse_area", tc), "Around mouse")
        for w, h in self.SIZES[:6]:
            mouse.addAction(self._size_name(w, h), lambda w=w, h=h: self._set_mode("mouse", size=(w, h)))
        if self.session != "x11":
            mouse.setEnabled(False)
            mouse.setToolTip("Only works in a Linux X11 session (like Linux Mint).")
            m.setToolTipsVisible(True)
        m.addSeparator()
        m.addAction(icons.icon("audio", tc), "Audio only", lambda: self._set_mode("audio"))
        del a

    def _set_mode(self, mode, size=None, monitor=None):
        if mode == "region":
            self.pick_region()  # sets the mode once an area is chosen
            return
        if mode == "rect":
            self.pick_region(fixed=size)
            return
        if mode == "window":
            self._set("mode", "window")
            self.pick_window()
        elif mode == "fullscreen":
            if monitor is not None:
                self.monitor_box.setCurrentIndex(monitor)
            self._set("mode", "fullscreen")
        elif mode == "mouse":
            self._set("mouse_size", list(size))
            self._set("mode", "mouse")
        elif mode == "audio":
            self._set("mode", "audio")
            if not (self.s.desktop_audio or self.s.mic):
                self.set_audio("desktop_audio", True)
            self._notify("Audio only: records desktop sound and/or mic, no video.")
        self._update_mode_ui()

    def _update_mode_ui(self):
        """Mode button text/icon + the info bar describing exactly what gets recorded."""
        mode = self.s.mode if self.s.mode in self.MODE_LABELS else "fullscreen"
        text, ic = self.MODE_LABELS[mode]
        self.mode_btn.setText(text)
        icons.bind(self.mode_btn, ic, "plain", 18)
        icons.bind(self.info_icon, ic, "accent", 16)
        screens = QGuiApplication.screens()

        def screen_desc(i):
            i = max(0, min(i, len(screens) - 1))
            g = screens[i].geometry()
            dpr = screens[i].devicePixelRatio()
            return i, round(g.width() * dpr), round(g.height() * dpr)

        if mode in ("region", "rect") and self.s.region:
            r = self.s.region
            i, _, _ = screen_desc(r.get("screen", 0))
            txt = (f"{r['w']}x{r['h']} - ({r['x']}, {r['y']}), ({r['x'] + r['w']}, {r['y'] + r['h']})"
                   f" - Display {i + 1}")
        elif mode == "window":
            txt = f"Window: {self.s.window_title}" if self.s.window_title else "Window: pick one when you press REC"
        elif mode == "mouse":
            w, h = self.s.mouse_size
            txt = f"{w}x{h} box that follows the mouse"
        elif mode == "audio":
            src = [n for n, on in (("desktop sound", self.s.desktop_audio), ("microphone", self.s.mic)) if on]
            txt = f"Audio only - {' + '.join(src) if src else 'turn on sound or mic'} - .{self.s.audio_format}"
        else:
            i, w, h = screen_desc(self.s.monitor)
            txt = f"{w}x{h} - (0, 0), ({w}, {h}) - Display {i + 1}"
            if self.session == "wayland":
                txt = "Fullscreen - you pick the screen in the system's share dialog"
        self.info_lbl.setText(txt)
        self.monitor_box.setVisible(mode == "fullscreen")

    def apply_game_preset(self):
        """One click to the best settings for recording games."""
        self._set("mode", "fullscreen")
        self.preset_box.setCurrentIndex(self.preset_box.findData("gaming"))  # 60 fps, balanced
        self.enc_box.setCurrentIndex(0)  # auto = best GPU encoder found
        self.set_audio("desktop_audio", True)
        self.q_mouse.setChecked(False)
        if not self.rb_toggle.isChecked():
            self.rb_toggle.setChecked(True)  # replay buffer: F10 saves the last moments
        self._update_mode_ui()
        hk = pynput_to_qt(self.s.hotkeys.get("replay", ""))
        self._notify(f"Game preset on: fullscreen, 60 fps, GPU encoder, game sound, no cursor. "
                     f"Replay buffer running ({hk} saves the last {self.s.replay_seconds} s).")

    # ---- sound toggles (toolbar, Home page and Audio page stay in sync) ----
    def set_audio(self, key, on):
        self._set(key, on)
        self._sync_audio_widgets()
        self._restart_meters()
        if self.s.mode == "audio":
            self._update_mode_ui()

    def _sync_audio_widgets(self):
        pairs = (("desktop_audio", ("spk_btn", "q_desk", "a_desk"), "speaker"),
                 ("mic", ("mic_btn", "q_mic", "a_mic"), "mic"))
        for key, names, ic in pairs:
            on = getattr(self.s, key)
            for n in names:
                w = getattr(self, n, None)
                if w is not None and w.isChecked() != on:
                    w.blockSignals(True)
                    w.setChecked(on)
                    w.blockSignals(False)
            btn = getattr(self, names[0], None)
            if btn is not None:
                icons.bind(btn, ic if on else ic + "_off", "plain", 22)

    def _restart_meters(self):
        if not hasattr(self, "spk_meter"):
            return
        visible = self.isVisible() and not self.isMinimized()
        for meter, on, dev in ((self.spk_meter, self.s.desktop_audio, self.s.desktop_device),
                               (self.mic_meter, self.s.mic, self.s.mic_device)):
            if visible and on:
                meter.start(dev)  # hides itself if the device can't be opened
            else:
                meter.stop()
                meter.setVisible(False)  # muted: no empty bar next to the button

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(300, self._restart_meters)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._restart_meters()  # window hidden: meters stop, nothing keeps the mic open

    def _on_monitor(self, i):
        self._set("monitor", max(0, i))
        self._update_mode_ui()

    # ---- mini mode: just the toolbar strip ----
    FULL_MIN = (980, 640)

    def set_compact(self, on: bool):
        self._set("compact", on)
        if on:
            if not self.isMaximized() and self.height() > 300:
                self._full_size = self.size()
            self.showNormal() if self.isMaximized() else None
            for w in (self.body_w, self.status_panel, self.name_box):
                w.hide()
            self.setMinimumSize(0, 0)
            icons.bind(self.collapse_btn, "chevron_down", "plain", 20)
            self.collapse_btn.setToolTip("Show the full window")
            QTimer.singleShot(0, self._shrink_to_strip)
        else:
            for w in (self.body_w, self.status_panel, self.name_box):
                w.show()
            self.setMinimumSize(*self.FULL_MIN)
            icons.bind(self.collapse_btn, "chevron_up", "plain", 20)
            self.collapse_btn.setToolTip("Make the window small (toolbar only)")
            size = getattr(self, "_full_size", None)
            self.resize(size if size else self.sizeHint().expandedTo(self.minimumSize()))

    def _shrink_to_strip(self):
        hint = self.centralWidget().layout().minimumSize()
        self.resize(max(hint.width(), 760), hint.height())

    def pick_region(self, fixed=None):
        self.hide()
        idx = self.s.region.get("screen", self.s.monitor) if self.s.region else self.s.monitor
        sel = RegionSelector(idx, self.theme.accent, fixed=fixed)
        sel.chosen.connect(lambda r: self._on_region(r, "rect" if fixed else "region", fixed))
        sel.cancelled.connect(self.bring_up)
        QTimer.singleShot(200, sel.go)  # let our window disappear first
        self._selector = sel

    def _on_region(self, r, mode="region", fixed=None):
        self._set("region", r)
        if fixed:
            self._set("rect_size", list(fixed))
        self._set("mode", mode)
        self._update_mode_ui()
        self._notify(f"Area set: {r['w']} x {r['h']}")
        self.bring_up()

    def pick_window(self):
        if self.session == "wayland":
            self._notify("When you press REC, pick the window in the system's share dialog.")
        elif self.session == "x11":
            if not shutil.which("xwininfo"):
                self._show_error("Window picking needs xwininfo (package x11-utils). Run setup again to install it.")
                return
            self._notify("Click the window you want to record...")

            def work():
                res = sysinfo.pick_window_x11()
                if res:
                    self.sig.window_picked.emit(*res)
            threading.Thread(target=work, daemon=True).start()
        else:
            m = QMenu(self)
            for title in sysinfo.list_windows_win32():
                if title == self.windowTitle():
                    continue
                m.addAction(title[:80], lambda t=title: self._on_window_picked(t, t))
            m.exec(self.mode_btn.mapToGlobal(self.mode_btn.rect().bottomLeft()))

    def _on_window_picked(self, wid, title):
        self._set("window_id", wid)
        self._set("window_title", title)
        self._update_mode_ui()
        self._notify(f"Recording window: {title}")

    # ======================================================== recording
    def toggle_record(self):
        if self.countdown.running():  # pressing again during 3-2-1 cancels
            self.countdown.cancel()
            self.rec_btn.set_countdown(0)
            self._notify("Recording cancelled.")
            return
        if self.recorder.state == "idle" and self.s.countdown > 0:
            self.countdown.theme = self.theme
            screen = (self.s.region.get("screen", 0) if self.s.mode in ("region", "rect") and self.s.region
                      else self.s.monitor)
            self.countdown.start(int(self.s.countdown), screen)
            return
        self._start_or_stop()

    def _on_count(self, n):
        self.rec_btn.set_countdown(n)
        self.state_lbl.setText(f"Recording in {n}... (press REC again to cancel)")
        if self.s.sounds:
            sounds.play("tick")

    def _count_done(self):
        self.rec_btn.set_countdown(0)
        self._start_or_stop()

    def _start_or_stop(self):
        if self.recorder.state == "idle" and self.s.sounds:
            sounds.play("start")  # plays before capture opens, so it isn't recorded
        if self.recorder.state == "idle" and self.replay.running():
            # One capture at a time keeps CPU/GPU use low: pause the buffer while recording.
            self._replay_resume = True
            self.replay.stop()
        self.recorder.toggle()

    def _on_state(self, st):
        rec = st in ("recording", "paused")
        if st == "idle" and getattr(self, "_last_state", "idle") == "saving" and self.s.sounds:
            sounds.play("stop")  # capture has ended, so it isn't recorded
        self._last_state = st
        self.rec_btn.set_recording(rec)
        self.pause_btn.setEnabled(rec)
        icons.bind(self.pause_btn, "play" if st == "paused" else "pause", "plain", 22)
        for b in (self.mode_btn, self.game_btn, self.monitor_box, self.spk_btn, self.mic_btn):
            b.setEnabled(st == "idle")
        texts = {
            "idle": ("READY", "tile_ok", "Ready to record."),
            "starting": ("STARTING", "tile_busy",
                         "Pick what to share in the system dialog..." if self.session == "wayland" else "Starting..."),
            "recording": ("REC", "tile_rec", "Recording. Press the hotkey again to stop."),
            "paused": ("PAUSED", "tile_busy", "Paused. Press pause again to continue."),
            "saving": ("SAVING", "tile_busy", "Finishing the file..."),
        }
        t, obj, long = texts[st]
        self.st_state.setText(t)
        self.st_state.setObjectName(obj)
        self.st_state.style().unpolish(self.st_state)
        self.st_state.style().polish(self.st_state)
        self.state_lbl.setText(long)
        if self.tray:
            self.tray.setIcon(self._tray_icon(st))
            self._update_tray_tooltip(self.recorder.elapsed())
            self.t_rec.setText("Stop recording" if rec else "Start recording")
            icons.bind(self.t_rec, "stop" if rec else "rec", "plain", 16)
            self.t_pause.setEnabled(rec)
            self.t_pause.setText("Resume" if st == "paused" else "Pause")
        if rec:
            if not self._ui_timer.isActive():
                self._ui_timer.start()
            self._sync_hud()
            self._tick()
        else:
            self._ui_timer.stop()
            self.hud.hide()
            if st == "idle":
                self.timer_lbl.setText("00:00:00")
                self.st_time.setText("00:00:00")
                self.st_fps.setText("")
                self.st_size.setText("")
                if self._replay_resume:
                    self._replay_resume = False
                    QTimer.singleShot(800, self.replay.start)

    def _sync_hud(self):
        if self.s.hud and self.recorder.state in ("recording", "paused"):
            self.hud.place(self.s.region.get("screen", 0) if self.s.mode in ("region", "rect") and self.s.region
                           else self.s.monitor)
            self.hud.show()
        else:
            self.hud.hide()

    def _tick(self):
        el = self.recorder.elapsed()
        fps = self.recorder.fps()
        t = hms(el)
        self.timer_lbl.setText(t)
        self.st_time.setText(t)
        self.st_fps.setText(f"{fps:.0f} fps" if fps else "")
        self.st_size.setText(human_size(self.recorder.size_bytes()))
        if self.hud.isVisible():
            self.hud.update_info(el, fps, self.recorder.state == "paused")
        self._update_tray_tooltip(el)

    def _on_saved(self, path):
        self._last_saved = path
        try:
            size = human_size(os.path.getsize(path))
        except OSError:
            size = ""
        self.last_lbl.setText(f"{os.path.basename(path)}   {size}\n{os.path.dirname(path)}")
        self.last_card.show()
        self._notify(f"Saved {os.path.basename(path)}")
        if self.stack.currentIndex() == PAGES.index("Recordings"):
            self.refresh_recordings()

    def _on_replay_toggle(self, on):
        self._set("replay_enabled", on)
        if on:
            self.replay.start()
        else:
            self.replay.stop()

    def _on_replay_state(self, running):
        if hasattr(self, "rb_state"):
            self.rb_state.setText("Buffer running" if running else "Buffer off")

    def _on_webcam_toggle(self, on):
        self._set("webcam", on)
        for c in (self.q_cam, getattr(self, "o_cam", None)):
            if c is not None and c.isChecked() != on:
                c.blockSignals(True)
                c.setChecked(on)
                c.blockSignals(False)
        self._restart_webcam()

    def _restart_webcam(self):
        if self.s.webcam:
            if not self.webcam.start(self.s.webcam_device, self.s.webcam_size):
                self._show_error("No webcam found.")
        else:
            self.webcam.stop()

    def _on_click_toggle(self, on):
        self._set("click_highlight", on)
        (self.clicks.start if on else self.clicks.stop)()

    # ======================================================== screenshot
    def take_screenshot(self):
        out_dir = self.s.video_dir() / "Screenshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"KalciCam_{stamp()}.png"
        if self.session == "wayland":
            def work():
                try:
                    from . import wayland_portal
                    src = wayland_portal.screenshot()
                    shutil.move(src, dest)
                    self.sig.info.emit(f"Screenshot saved: {dest.name}")
                except Exception as e:
                    self.sig.info.emit(f"Screenshot failed: {e}")
            threading.Thread(target=work, daemon=True).start()
            return
        screens = QGuiApplication.screens()
        region = self.s.region if self.s.mode in ("region", "rect") and self.s.region else None
        idx = region.get("screen", 0) if region else self.s.monitor
        scr = screens[max(0, min(idx, len(screens) - 1))]
        pm = scr.grabWindow(0)
        if region:
            pm = pm.copy(region["x"], region["y"], region["w"], region["h"])
        if pm.save(str(dest), "PNG"):
            if self.s.sounds:
                sounds.play("shot")
            self._notify(f"Screenshot saved: {dest.name}")
        else:
            self._show_error("Couldn't save the screenshot.")

    # ======================================================== recordings page
    def refresh_recordings(self):
        self.rec_list.clear()
        d = self.s.video_dir()
        files = sorted([p for p in d.glob("*") if p.suffix.lower() in (".mp4", ".mkv", ".m4a", ".mp3")],
                       key=lambda p: p.stat().st_mtime, reverse=True)
        self.rec_empty.setVisible(not files)
        self.rec_list.setVisible(bool(files))
        for b in self.rec_buttons:
            b.setEnabled(bool(files))
        self.rec_count.setText(f"{len(files)} file{'s' if len(files) != 1 else ''}" if files else "")
        thumbs = paths.cache_dir() / "thumbs"
        thumbs.mkdir(exist_ok=True)
        todo = []
        placeholder = QPixmap(208, 117)
        placeholder.fill(QColor(0, 0, 0, 120))
        for f in files:
            st = f.stat()
            when = datetime.fromtimestamp(st.st_mtime).strftime("%b %d, %H:%M")
            it = QListWidgetItem(QIcon(placeholder), f"{f.stem}\n{human_size(st.st_size)} · {when}")
            it.setData(Qt.UserRole, str(f))
            it.setToolTip(str(f))
            self.rec_list.addItem(it)
            if f.suffix.lower() in (".m4a", ".mp3"):  # audio: a speaker card instead of a frame
                card = QPixmap(208, 117)
                card.fill(QColor(self.theme.accent).darker(260))
                pa = QPainter(card)
                pa.drawPixmap(80, 34, icons.pixmap("audio", self.theme.accent, 48, 1.0))
                pa.end()
                it.setIcon(QIcon(card))
                continue
            png = thumbs / f"{f.stem}_{int(st.st_mtime)}.png"
            if png.exists():
                it.setIcon(QIcon(str(png)))
            else:
                todo.append((str(f), str(png)))
        if todo:
            def work():
                for vid, png in todo:
                    if run_quiet(capture.thumbnail_cmd(vid, png), timeout=20):
                        self.sig.thumb.emit(vid, png)
            threading.Thread(target=work, daemon=True).start()

    def _set_thumb(self, vid, png):
        for i in range(self.rec_list.count()):
            it = self.rec_list.item(i)
            if it.data(Qt.UserRole) == vid:
                it.setIcon(QIcon(png))

    def _rec_menu(self, pos):
        it = self.rec_list.itemAt(pos)
        if not it:
            return
        self.rec_list.setCurrentItem(it)
        m = QMenu(self)
        tc = self.theme.text
        m.addAction(icons.icon("play", tc), "Play", lambda: self._rec_action("play"))
        m.addAction(icons.icon("folder", tc), "Show in folder", lambda: self._rec_action("folder"))
        m.addAction(icons.icon("trash", tc), "Move to trash", lambda: self._rec_action("trash"))
        m.exec(self.rec_list.mapToGlobal(pos))

    def _rec_action(self, what):
        it = self.rec_list.currentItem()
        if not it:
            return
        path = it.data(Qt.UserRole)
        if what == "play":
            self._open(path)
        elif what == "folder":
            self._open(os.path.dirname(path))
        elif what == "trash":
            from PySide6.QtCore import QFile
            if QMessageBox.question(self, "Move to trash?", f"Move {os.path.basename(path)} to the trash?") \
                    == QMessageBox.Yes:
                if QFile.moveToTrash(path):
                    self.rec_list.takeItem(self.rec_list.row(it))
                else:
                    self._show_error("Couldn't move it to the trash.")

    # ======================================================== misc
    def _go(self, i):
        self.stack.setCurrentIndex(i)
        if PAGES[i] == "Recordings":
            self.refresh_recordings()

    def _open(self, path):
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _notify(self, msg, kind="info"):
        """Slide-in message in the window, or a desktop notification if KalciCam is hidden."""
        if self.recorder.state == "idle" and kind == "info":
            self.state_lbl.setText(msg)
        if self.isVisible() and not self.isMinimized():
            self.toast.show_message(msg, kind)
        elif self.tray:
            self.tray.showMessage(APP_NAME, msg, app_icon() if kind == "info" else QSystemTrayIcon.Warning, 3500)

    def _show_error(self, msg):
        if self.isVisible() and len(msg) < 90 and "\n" not in msg:
            self._notify(msg, "error")
        elif self.isVisible():
            QMessageBox.warning(self, APP_NAME, msg)
        elif self.tray:
            self.tray.showMessage(APP_NAME, msg, QSystemTrayIcon.Warning, 6000)

    # ---- hotkeys: start them, show whether they work, offer a fix ----
    def _apply_hotkeys(self):
        live = self.hotkeys.apply(self.s.hotkeys, enabled=not self.s.hotkeys_system)
        if self.s.hotkeys_system:
            hotkeys.add_system_shortcuts(self.s.hotkeys)  # keep desktop shortcuts in step with edits
        self._update_hotkey_hint()
        self._update_hk_status(live)

    def _update_hk_status(self, live):
        wayland = not hotkeys.GlobalHotkeys.supported()
        if self.s.hotkeys_system:
            ok, text = True, f"Handled by {hotkeys.desktop_name()}'s keyboard shortcuts."
        elif live:
            ok, text = True, "Hotkeys are working, even when KalciCam is in the background."
        elif wayland:
            ok, text = False, ("Your desktop (Wayland) doesn't let apps listen for keys. "
                               + ("Turn on the option below to use your desktop's shortcuts."
                                  if hotkeys.system_shortcuts_available() else
                                  "Add the commands below as keyboard shortcuts in your system settings."))
        else:
            ok, text = False, f"Hotkeys aren't working. {self.hotkeys.error}"
        if hasattr(self, "hk_status"):
            self.hk_status.setText(text)
            self.hk_status.setObjectName("hk_ok" if ok else "hk_bad")
            self.hk_status.style().unpolish(self.hk_status)
            self.hk_status.style().polish(self.hk_status)
            icons.bind(self.hk_status_icon, "check" if ok else "warn", "accent" if ok else "plain", 18)
        if hasattr(self, "hk_banner"):
            fixable = hotkeys.system_shortcuts_available()
            self.hk_banner_lbl.setText(text + (f" Click 'Fix it' to use {hotkeys.desktop_name()}'s keyboard "
                                               "shortcuts instead." if fixable else " See Extras > Hotkeys."))
            self.hk_banner_btn.setVisible(fixable)
            self.hk_banner.setVisible(not ok)

    def _fix_hotkeys(self):
        if hasattr(self, "hk_sys"):
            self.hk_sys.setChecked(True)  # runs _on_hotkeys_system
        else:
            self._on_hotkeys_system(True)

    def _on_hotkeys_system(self, on):
        self._set("hotkeys_system", on)
        if on:
            ok, msg = hotkeys.add_system_shortcuts(self.s.hotkeys)
            if not ok:
                self._set("hotkeys_system", False)
                if hasattr(self, "hk_sys"):
                    self.hk_sys.blockSignals(True)
                    self.hk_sys.setChecked(False)
                    self.hk_sys.blockSignals(False)
                self._notify(msg, "error")
            else:
                self._notify(msg)
        else:
            hotkeys.remove_system_shortcuts()  # so keys don't fire twice
            self._notify("KalciCam listens for the hotkeys itself again.")
        self._apply_hotkeys()

    def _copy_report(self):
        key = self.s.encoder if self.s.encoder != "auto" else self.s.detected_encoder
        lines = [
            f"KalciCam {__version__}",
            self._about_text,
            f"Desktop: {os.environ.get('XDG_CURRENT_DESKTOP', '-')}",
            f"Encoder: {encoders.label(key)}",
            f"Hotkeys: {'desktop shortcuts' if self.s.hotkeys_system else ('working' if self.hotkeys.ok else 'NOT working: ' + self.hotkeys.error)}",
            f"Mode: {self.s.mode}  fps: {self.s.fps}  quality: {self.s.quality}",
            f"Log: {paths.cache_dir() / 'kalcicam.log'}",
        ]
        QGuiApplication.clipboard().setText("\n".join(lines))
        self._notify("Copied. Paste it in a message to whoever helps you.")

    def handle_command(self, cmd):
        if cmd == "toggle":
            self.toggle_record()
        elif cmd == "pause":
            self.recorder.pause_toggle()
        elif cmd == "screenshot":
            self.take_screenshot()
        elif cmd == "replay":
            self.replay.save()
        elif cmd == "show":
            self.bring_up()

    def bring_up(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        if not self.quitting and self.tray and self.s.minimize_to_tray:
            e.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, "Still here in the tray. Hotkeys keep working.", app_icon(), 2500)
            return
        e.accept()
        self.quit()

    def quit(self):
        self.quitting = True
        config.save(self.s)
        self.hotkeys.stop()
        self.clicks.stop()
        self.webcam.stop()
        self.recorder.shutdown()
        self.replay.stop()
        WAYLAND.close()
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()
