"""The main KalciCam window (Bandicam-style layout, themed with the photos)."""

import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QKeySequence, QPainter, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFileDialog, QGridLayout, QHBoxLayout,
                               QKeySequenceEdit, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QScrollArea, QSpinBox, QStackedWidget, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from . import APP_NAME, __version__, capture, config, encoders, hotkeys, icons, paths, sysinfo, themes
from .overlays import ClickHighlighter, Hud, RegionSelector, WebcamBubble
from .recorder import WAYLAND, Recorder, ReplayBuffer, run_quiet, stamp
from .meters import LevelMeter
from .widgets import (Background, RecButton, ThemeCard, VolumeSlider, app_icon, button, hms, human_size,
                      icon_label, label, panel, pynput_to_qt, qt_to_pynput, row, toggle, tool_button)

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

        self.hotkeys.apply(self.s.hotkeys)
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
        v.setContentsMargins(6, 4, 10, 10)
        v.setSpacing(12)
        return w, v

    def _page_home(self):
        w, v = self._page()
        self.timer_lbl = label("00:00:00", "timer")
        v.addWidget(self.timer_lbl)
        self.state_lbl = label("Ready to record. Press F12 or the big REC button.", "sub", wrap=True)
        v.addWidget(self.state_lbl)

        v.addWidget(label("Quick switches", "h2"))
        self.q_desk = toggle("Desktop audio", self.s.desktop_audio, icon="speaker")
        self.q_mic = toggle("Microphone", self.s.mic, icon="mic")
        self.q_cam = toggle("Webcam bubble", self.s.webcam, icon="webcam")
        self.q_mouse = toggle("Show cursor", self.s.draw_mouse, icon="cursor")
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        for i, c in enumerate((self.q_desk, self.q_mic, self.q_cam, self.q_mouse)):
            grid.addWidget(c, i // 2, i % 2)
        v.addLayout(grid)
        self.q_desk.toggled.connect(lambda on: self.set_audio("desktop_audio", on))
        self.q_mic.toggled.connect(lambda on: self.set_audio("mic", on))
        self.q_cam.toggled.connect(self._on_webcam_toggle)
        self.q_mouse.toggled.connect(lambda on: self._set("draw_mouse", on))

        v.addWidget(label("Hotkeys", "h2"))
        self.hotkey_hint = label("", "sub", wrap=True)
        v.addWidget(self.hotkey_hint)
        self._update_hotkey_hint()

        self.last_lbl = label("", "sub", wrap=True)
        self.last_btn = button("Play last recording", icon="play")
        self.last_btn.hide()
        self.last_btn.clicked.connect(lambda: self._open(self._last_saved))
        v.addWidget(self.last_lbl)
        v.addLayout(row(self.last_btn))
        v.addStretch(1)
        return w

    def _page_video(self):
        w, v = self._page()
        v.addWidget(label("Video", "h2"))

        self.preset_box = QComboBox()
        for key, text in (("gaming", "Gaming - 60 fps, balanced"), ("art", "Art / animation - 30 fps, sharp"),
                          ("small", "Small files - 30 fps"), ("custom", "Custom")):
            self.preset_box.addItem(text, key)
        self.preset_box.setCurrentIndex(max(0, self.preset_box.findData(self.s.preset)))
        self.preset_box.currentIndexChanged.connect(self._on_preset)
        v.addLayout(row(label("Preset"), self.preset_box))

        self.fps_box = QSpinBox()
        self.fps_box.setRange(10, 240)
        self.fps_box.setValue(self.s.fps)
        self.fps_box.setSuffix(" fps")
        self.fps_box.valueChanged.connect(lambda x: self._custom("fps", x))
        self.q_box = QComboBox()
        for key, text in (("high", "High (bigger files)"), ("balanced", "Balanced"), ("small", "Small")):
            self.q_box.addItem(text, key)
        self.q_box.setCurrentIndex(self.q_box.findData(self.s.quality))
        self.q_box.currentIndexChanged.connect(lambda _: self._custom("quality", self.q_box.currentData()))
        v.addLayout(row(label("Frame rate"), self.fps_box, 20, label("Quality"), self.q_box))

        v.addWidget(label("Encoder", "h2"))
        self.enc_box = QComboBox()
        self._fill_encoders()
        self.enc_box.currentIndexChanged.connect(lambda _: self._set("encoder", self.enc_box.currentData(),
                                                                     after=self._update_status_static))
        retest = button("Test my GPU again", icon="search")
        retest.clicked.connect(self.detect_encoder)
        v.addLayout(row(self.enc_box, retest))
        v.addWidget(label("GPU encoders put the work on the graphics card, so recording barely uses the CPU. "
                          "'Auto' picks the best one KalciCam found on this computer.", "hint", wrap=True))

        v.addWidget(label("File", "h2"))
        self.fmt_box = QComboBox()
        self.fmt_box.addItem("MP4 (plays everywhere)", "mp4")
        self.fmt_box.addItem("MKV (raw)", "mkv")
        self.fmt_box.setCurrentIndex(self.fmt_box.findData(self.s.final_format))
        self.fmt_box.currentIndexChanged.connect(lambda _: self._set("final_format", self.fmt_box.currentData()))
        v.addLayout(row(label("Save as"), self.fmt_box))
        v.addWidget(label("KalciCam always records to crash-safe MKV first, then converts to MP4 when you stop. "
                          "The conversion copies the video without re-encoding, so it takes about a second.",
                          "hint", wrap=True))
        keep = toggle("Also keep the original MKV", self.s.keep_mkv)
        keep.toggled.connect(lambda on: self._set("keep_mkv", on))
        v.addWidget(keep)

        self.dir_lbl = label(str(self.s.video_dir()), "sub")
        change = button("Change", icon="folder")
        change.clicked.connect(self._pick_dir)
        v.addLayout(row(label("Folder"), self.dir_lbl, change))

        if self.session == "windows":
            v.addWidget(label("Windows test options", "h2"))
            eng = QComboBox()
            eng.addItem("Desktop Duplication (GPU, recommended)", "ddagrab")
            eng.addItem("GDI (compatible, more CPU)", "gdigrab")
            eng.setCurrentIndex(eng.findData(self.s.win_capture_engine))
            eng.currentIndexChanged.connect(lambda _: self._set("win_capture_engine", eng.currentData()))
            v.addLayout(row(label("Capture"), eng))
        v.addStretch(1)
        return w

    def _page_audio(self):
        w, v = self._page()
        desk, mics = sysinfo.audio_devices()
        v.addWidget(label("Desktop / game sound", "h2"))
        self.a_desk = toggle("Record what I hear", self.s.desktop_audio)
        self.a_desk.toggled.connect(lambda on: self.set_audio("desktop_audio", on))
        self.desk_box = self._device_box(desk, self.s.desktop_device, "desktop_device")
        dv = VolumeSlider(self.s.desktop_volume)
        dv.changed.connect(lambda x: self._set("desktop_volume", x))
        v.addWidget(self.a_desk)
        v.addLayout(row(label("Device"), self.desk_box))
        v.addLayout(row(label("Volume"), dv))
        if self.session == "windows":
            v.addWidget(label("Windows test build: desktop sound needs a loopback device such as 'Stereo Mix' "
                              "(enable it in Sound settings > Recording). On Linux it just works.", "hint",
                              wrap=True))

        v.addWidget(label("Microphone", "h2"))
        self.a_mic = toggle("Record my microphone", self.s.mic)
        self.a_mic.toggled.connect(lambda on: self.set_audio("mic", on))
        self.mic_box = self._device_box(mics, self.s.mic_device, "mic_device")
        mv = VolumeSlider(self.s.mic_volume)
        mv.changed.connect(lambda x: self._set("mic_volume", x))
        v.addWidget(self.a_mic)
        v.addLayout(row(label("Device"), self.mic_box))
        v.addLayout(row(label("Volume"), mv))
        refresh = button("Refresh devices", icon="refresh")
        refresh.clicked.connect(self._refresh_audio)
        v.addLayout(row(refresh))

        v.addWidget(label("Audio only mode", "h2"))
        fmt = QComboBox()
        fmt.addItem("M4A (good quality, small)", "m4a")
        fmt.addItem("MP3 (plays everywhere)", "mp3")
        fmt.setCurrentIndex(max(0, fmt.findData(self.s.audio_format)))
        fmt.currentIndexChanged.connect(lambda _: (self._set("audio_format", fmt.currentData()),
                                                   self._update_mode_ui()))
        v.addLayout(row(label("Save sound as"), fmt))
        v.addWidget(label("Pick 'Audio only' from the mode menu at the top to record just sound, no video.",
                          "hint", wrap=True))
        v.addStretch(1)
        return w

    def _device_box(self, devs, current, key):
        b = QComboBox()
        b.setMinimumWidth(340)
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

    def _page_overlays(self):
        w, v = self._page()
        v.addWidget(label("Webcam bubble", "h2"))
        self.o_cam = toggle("Show my webcam in a round bubble", self.s.webcam)
        self.o_cam.toggled.connect(self._on_webcam_toggle)
        v.addWidget(self.o_cam)
        self.cam_box = QComboBox()
        self.cam_box.setMinimumWidth(300)
        self.cam_box.addItem("Default camera", "")
        cam_refresh = tool_button("refresh", "Look for webcams", 40)
        cam_refresh.clicked.connect(self._fill_webcams)
        self.cam_box.currentIndexChanged.connect(lambda _: self._set("webcam_device", self.cam_box.currentData(),
                                                                     after=self._restart_webcam))
        size = QSpinBox()
        size.setRange(120, 480)
        size.setSingleStep(20)
        size.setSuffix(" px")
        size.setValue(self.s.webcam_size)
        size.valueChanged.connect(lambda x: self._set("webcam_size", x, after=self._restart_webcam))
        v.addLayout(row(label("Camera"), self.cam_box, cam_refresh, 12, label("Size"), size))
        v.addWidget(label("The bubble floats on top of your screen, so it's captured in the recording. "
                          "Drag it anywhere, right-click to hide.", "hint", wrap=True))

        v.addWidget(label("Recording HUD", "h2"))
        if paths.IS_WINDOWS:
            hud = toggle("Show the REC timer + FPS badge while recording", self.s.hud)
            hint = "You see the badge, but it's kept out of the video."
        else:
            hud = toggle("Also show a floating REC badge on screen (it WILL appear in the video)", self.s.hud)
            hint = ("While recording, the KalciCam tray icon turns into a red dot (yellow when paused). "
                    "Hover over it to see the timer. Linux doesn't let apps hide a window from a screen "
                    "recording, so the floating badge is off unless you want it in your videos.")
        hud.toggled.connect(lambda on: self._set("hud", on, after=self._sync_hud))
        v.addWidget(hud)
        v.addWidget(label(hint, "hint", wrap=True))

        v.addWidget(label("Click highlights", "h2"))
        ch = toggle("Show a ripple where I click", self.s.click_highlight)
        ch.setEnabled(ClickHighlighter.supported())
        ch.toggled.connect(self._on_click_toggle)
        v.addWidget(ch)
        if not ClickHighlighter.supported():
            v.addWidget(label("Wayland doesn't let apps see clicks outside their own window, so this only works "
                              "in an X11 session.", "hint", wrap=True))
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
        v.addWidget(label("Hotkeys", "h2"))
        self.hk_edits = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        for i, (key, text) in enumerate((("record", "Start / stop recording"), ("pause", "Pause / resume"),
                                         ("screenshot", "Screenshot"), ("replay", "Save replay"))):
            e = QKeySequenceEdit(QKeySequence(pynput_to_qt(self.s.hotkeys.get(key, ""))))
            e.setMaximumSequenceLength(1)
            e.editingFinished.connect(lambda k=key, e=e: self._on_hotkey(k, e))
            grid.addWidget(label(text), i, 0)
            grid.addWidget(e, i, 1)
            self.hk_edits[key] = e
        v.addLayout(grid)
        reset = button("Reset to F12 / Shift+F12 / F11 / F10")
        reset.clicked.connect(self._reset_hotkeys)
        v.addLayout(row(reset))
        if not hotkeys.GlobalHotkeys.supported():
            exe = hotkeys.launch_command()
            v.addWidget(label("Wayland doesn't let apps listen for keys in the background, so the hotkeys work "
                              "through your desktop's keyboard shortcuts instead. Bind your keys to:", "hint",
                              wrap=True))
            code = label(f"{exe} --toggle\n{exe} --pause\n{exe} --screenshot\n{exe} --replay", "code")
            code.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.addWidget(code)
            if hotkeys.is_gnome():
                g = button("Add these shortcuts to GNOME for me", "primary", icon="bolt")
                g.clicked.connect(lambda: self._notify(hotkeys.add_gnome_shortcuts(self.s.hotkeys)[1]))
                v.addLayout(row(g))

        v.addWidget(label("Replay buffer", "h2"))
        v.addWidget(label("Keeps the last few seconds in memory-light chunks so you can save a clip after "
                          "something cool happens, like an instant replay.", "hint", wrap=True))
        self.rb_toggle = toggle("Keep a replay buffer running", self.s.replay_enabled)
        self.rb_toggle.toggled.connect(self._on_replay_toggle)
        secs = QSpinBox()
        secs.setRange(10, 300)
        secs.setSingleStep(10)
        secs.setSuffix(" seconds")
        secs.setValue(self.s.replay_seconds)
        secs.valueChanged.connect(lambda x: self._set("replay_seconds", x))
        save_now = button("Save replay now", icon="save")
        save_now.clicked.connect(self.replay.save)
        v.addWidget(self.rb_toggle)
        v.addLayout(row(label("Length"), secs, save_now))
        self.rb_state = label("", "sub")
        v.addWidget(self.rb_state)

        v.addWidget(label("Window", "h2"))
        tray = toggle("Closing the window keeps KalciCam running in the tray", self.s.minimize_to_tray)
        tray.toggled.connect(lambda on: self._set("minimize_to_tray", on))
        v.addWidget(tray)
        v.addStretch(1)
        return w

    def _page_recordings(self):
        w, v = self._page()
        v.addWidget(label("Your recordings", "h2"))
        self.rec_list = QListWidget()
        self.rec_list.setViewMode(QListWidget.IconMode)
        self.rec_list.setIconSize(QSize(208, 117))
        self.rec_list.setGridSize(QSize(236, 182))
        self.rec_list.setResizeMode(QListWidget.Adjust)
        self.rec_list.setMovement(QListWidget.Static)
        self.rec_list.setWordWrap(True)
        self.rec_list.setMinimumHeight(360)
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
        refresh = button("Refresh", icon="refresh")
        refresh.clicked.connect(self.refresh_recordings)
        v.addLayout(row(play, show, delete, None, refresh, stretch=False))
        return w

    def _page_themes(self):
        w, v = self._page()
        v.addWidget(label("Pick a background", "h2"))
        v.addWidget(label("Each picture comes with its own colours for the whole app.", "hint"))
        grid = QGridLayout()
        grid.setSpacing(12)
        self.theme_group = QButtonGroup(self)
        for i, t in enumerate(themes.THEMES):
            c = ThemeCard(t, 200, 120)
            c.setChecked(t.key == self.theme.key)
            self.theme_group.addButton(c, i)
            grid.addWidget(c, i // 3, i % 3)
        self.theme_group.idClicked.connect(lambda i: self.set_theme(themes.THEMES[i].key))
        v.addLayout(grid)
        rot = toggle("Rotate backgrounds automatically", self.s.rotate_backgrounds)
        mins = QSpinBox()
        mins.setRange(1, 240)
        mins.setSuffix(" min")
        mins.setValue(self.s.rotate_minutes)
        rot.toggled.connect(lambda on: self._set("rotate_backgrounds", on, after=self._apply_rotation))
        mins.valueChanged.connect(lambda x: self._set("rotate_minutes", x, after=self._apply_rotation))
        v.addLayout(row(rot, label("every"), mins))
        v.addStretch(1)
        return w

    def _page_about(self):
        w, v = self._page()
        v.addWidget(label(f"{APP_NAME} {__version__}", "h2"))
        st = {"windows": "Windows (test build)", "x11": "Linux · X11", "wayland": "Linux · Wayland"}[self.session]
        be = {"ffmpeg_win": "FFmpeg + Desktop Duplication", "ffmpeg_linux": "FFmpeg + x11grab",
              "gst": "GStreamer + PipeWire portal"}[encoders.backend()]
        self.about_info = label("", "sub", wrap=True)
        v.addWidget(self.about_info)
        self._about_text = f"System: {st}\nCapture: {be}\nSettings: {paths.config_dir()}"
        self.about_info.setText(self._about_text)
        again = button("Run setup again", "primary", icon="wand")
        again.clicked.connect(self.request_setup.emit)
        v.addLayout(row(again))
        v.addWidget(label("Why it's light on CPU", "h2"))
        v.addWidget(label(
            "• Video is encoded on the graphics card (NVENC / VA-API / AMF) whenever possible.\n"
            "• On Linux X11 + VA-API, colour conversion also happens on the GPU.\n"
            "• The window doesn't show a live preview, and it only refreshes twice a second while recording.\n"
            "• Pause and MP4 conversion copy the video without re-encoding it.\n"
            "• The photo background is scaled once and cached.", "sub", wrap=True))
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
        if not hotkeys.GlobalHotkeys.supported():
            txt += "\n(Wayland: set these up once in the Extras tab.)"
        self.hotkey_hint.setText(txt)

    def _on_hotkey(self, key, edit):
        seq = edit.keySequence().toString()
        self.s.hotkeys[key] = qt_to_pynput(seq)
        self._save_timer.start()
        self.hotkeys.apply(self.s.hotkeys)
        self._update_hotkey_hint()

    def _reset_hotkeys(self):
        self.s.hotkeys = dict(config.DEFAULT_HOTKEYS)
        for k, e in self.hk_edits.items():
            e.setKeySequence(QKeySequence(pynput_to_qt(self.s.hotkeys[k])))
        self._save_timer.start()
        self.hotkeys.apply(self.s.hotkeys)
        self._update_hotkey_hint()

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
                meter.start(dev)
            else:
                meter.stop()
                meter.setVisible(True)

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
        if self.recorder.state == "idle" and self.replay.running():
            # One capture at a time keeps CPU/GPU use low: pause the buffer while recording.
            self._replay_resume = True
            self.replay.stop()
        self.recorder.toggle()

    def _on_state(self, st):
        rec = st in ("recording", "paused")
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
        self.last_lbl.setText(f"Saved: {path}")
        self.last_btn.show()
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
            self._notify(f"Screenshot saved: {dest.name}")
        else:
            self._show_error("Couldn't save the screenshot.")

    # ======================================================== recordings page
    def refresh_recordings(self):
        self.rec_list.clear()
        d = self.s.video_dir()
        files = sorted([p for p in d.glob("*") if p.suffix.lower() in (".mp4", ".mkv", ".m4a", ".mp3")],
                       key=lambda p: p.stat().st_mtime, reverse=True)
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

    def _notify(self, msg):
        if self.recorder.state == "idle":
            self.state_lbl.setText(msg)
        if self.tray and not self.isVisible():
            self.tray.showMessage(APP_NAME, msg, app_icon(), 3000)

    def _show_error(self, msg):
        if self.isVisible():
            QMessageBox.warning(self, APP_NAME, msg)
        elif self.tray:
            self.tray.showMessage(APP_NAME, msg, QSystemTrayIcon.Warning, 6000)

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
