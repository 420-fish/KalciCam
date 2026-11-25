"""Settings, saved as JSON in the user's config folder."""

import json
from dataclasses import asdict, dataclass, field, fields

from . import paths

PRESETS = {
    # name: (fps, quality)
    "gaming": (60, "balanced"),
    "art": (30, "high"),
    "small": (30, "small"),
}

# Constant-quality value per quality level (lower = better / bigger file).
QUALITY_QP = {"high": 18, "balanced": 23, "small": 28}

# Bump when an update needs to change saved settings (see _migrate).
SETTINGS_REV = 1

DEFAULT_HOTKEYS = {
    "record": "<f12>",
    "pause": "<shift>+<f12>",
    "screenshot": "<f11>",
    "replay": "<f10>",
}


@dataclass
class Settings:
    first_run_done: bool = False
    save_dir: str = ""
    # Look
    theme: str = "neon_space"
    rotate_backgrounds: bool = False
    rotate_minutes: int = 10
    # What to capture
    mode: str = "fullscreen"  # fullscreen | region | rect | mouse | window | audio
    rect_size: list = field(default_factory=lambda: [1280, 720])  # "Rectangle on a screen" size
    mouse_size: list = field(default_factory=lambda: [1280, 720])  # "Around mouse" box size
    audio_format: str = "m4a"  # audio-only file type: m4a | mp3
    compact: bool = False  # window collapsed to the toolbar strip
    monitor: int = 0
    region: dict = field(default_factory=dict)  # {screen, x, y, w, h} in physical px, relative to screen
    window_id: str = ""  # X11 window id or Windows window title
    window_title: str = ""
    draw_mouse: bool = True
    # Video
    preset: str = "gaming"
    fps: int = 60
    quality: str = "balanced"
    encoder: str = "auto"  # auto, or a key from encoders.py
    detected_encoder: str = ""
    vaapi_device: str = ""  # which /dev/dri/renderD* VA-API worked on
    detected_version: str = ""  # KalciCam version that ran the encoder test (re-test after updates)
    final_format: str = "mp4"  # mp4 | mkv
    keep_mkv: bool = False
    win_capture_engine: str = "ddagrab"  # ddagrab (GPU) | gdigrab (fallback)
    # Audio
    desktop_audio: bool = True
    desktop_device: str = ""  # empty = default output's monitor
    desktop_volume: int = 100
    mic: bool = False
    mic_device: str = ""
    mic_volume: int = 100
    # Overlays
    webcam: bool = False
    webcam_device: str = ""
    webcam_size: int = 220
    hud: bool = paths.IS_WINDOWS  # Linux can't keep the badge out of the video, so it's off there
    click_highlight: bool = False
    # Extras
    hotkeys: dict = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    hotkeys_system: bool = False  # keys handled by desktop shortcuts instead of KalciCam's own listener
    countdown: int = 3  # seconds of 3-2-1 before recording (0 = start right away)
    sounds: bool = True  # little click when recording starts / stops
    replay_enabled: bool = False
    replay_seconds: int = 30
    minimize_to_tray: bool = True
    wayland_restore_token: str = ""
    settings_rev: int = SETTINGS_REV

    def video_dir(self):
        from pathlib import Path

        d = Path(self.save_dir) if self.save_dir else paths.default_videos_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def apply_preset(self, name: str):
        if name in PRESETS:
            self.preset = name
            self.fps, self.quality = PRESETS[name]


def _migrate(s: Settings, data: dict):
    rev = data.get("settings_rev", 0)
    if rev < 1 and paths.IS_LINUX:
        # 1.0.3: the floating REC badge is captured on Linux, so turn it off;
        # the tray icon shows recording state instead.
        s.hud = False
    s.settings_rev = SETTINGS_REV


def _path():
    return paths.config_dir() / "settings.json"


def load() -> Settings:
    s = Settings()
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        known = {f.name for f in fields(Settings)}
        for k, v in data.items():
            if k in known:
                setattr(s, k, v)
        # Fill in any hotkeys added in newer versions.
        for k, v in DEFAULT_HOTKEYS.items():
            s.hotkeys.setdefault(k, v)
        _migrate(s, data)
    except FileNotFoundError:
        pass
    except Exception as e:  # corrupt file: keep defaults, but don't crash
        print("KalciCam: could not read settings:", e)
    return s


def save(s: Settings):
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")
    tmp.replace(p)
