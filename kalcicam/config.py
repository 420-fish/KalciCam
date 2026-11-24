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
    mode: str = "fullscreen"  # fullscreen | region | window
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
    hud: bool = True
    click_highlight: bool = False
    # Extras
    hotkeys: dict = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    replay_enabled: bool = False
    replay_seconds: int = 30
    minimize_to_tray: bool = True
    wayland_restore_token: str = ""

    def video_dir(self):
        from pathlib import Path

        d = Path(self.save_dir) if self.save_dir else paths.default_videos_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def apply_preset(self, name: str):
        if name in PRESETS:
            self.preset = name
            self.fps, self.quality = PRESETS[name]


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
