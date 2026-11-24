"""Where things live: bundled assets, config, recordings, and helper programs."""

import os
import shutil
import sys
from pathlib import Path

from . import APP_ID

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")


def resource_dir() -> Path:
    """Folder holding bundled assets (works from source, PyInstaller and the AppImage)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "kalcicam" / "assets"
    return Path(__file__).resolve().parent / "assets"


def asset(name: str) -> str:
    return str(resource_dir() / name)


def config_dir() -> Path:
    if IS_WINDOWS:
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = root / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    if IS_WINDOWS:
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    d = root / APP_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_videos_dir() -> Path:
    videos = None
    if IS_LINUX:
        # Respect the XDG "Videos" folder if the desktop set one.
        try:
            out = _run_quiet(["xdg-user-dir", "VIDEOS"])
            if out and out.strip() and out.strip() != str(Path.home()):
                videos = Path(out.strip())
        except Exception:
            pass
    if videos is None:
        videos = Path.home() / "Videos"
    return videos / "KalciCam"


def _run_quiet(cmd):
    import subprocess

    return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout


def find_program(name: str) -> str | None:
    """Look next to the app first (portable builds), then on PATH."""
    exe = name + (".exe" if IS_WINDOWS else "")
    here = Path(sys.executable).resolve().parent
    for cand in (here / exe, here / "bin" / exe):
        if cand.is_file():
            return str(cand)
    return shutil.which(name)


def ffmpeg() -> str | None:
    return find_program("ffmpeg")


def no_window_flags() -> int:
    """On Windows, keep ffmpeg from flashing a console window."""
    if IS_WINDOWS:
        import subprocess

        return subprocess.CREATE_NO_WINDOW
    return 0
