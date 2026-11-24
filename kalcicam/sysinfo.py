"""Find out what kind of system we're on and which devices it has."""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from . import paths


def session_type() -> str:
    """'windows', 'x11' or 'wayland'."""
    if paths.IS_WINDOWS:
        return "windows"
    st = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if st == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    return "x11"


def distro() -> dict:
    """Parse /etc/os-release (empty dict on Windows)."""
    info = {}
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip().split("=", 1)
                    info[k] = v.strip('"')
    except OSError:
        pass
    return info


def package_manager() -> str:
    for pm in ("apt", "dnf", "pacman", "zypper"):
        if shutil.which(pm):
            return pm
    return ""


# Package names for what KalciCam needs, per package manager.
PACKAGES = {
    "apt": {
        "ffmpeg": "ffmpeg",
        "pactl": "pulseaudio-utils",
        "xwininfo": "x11-utils",
        "gst": "gstreamer1.0-tools gstreamer1.0-pipewire gstreamer1.0-plugins-good "
        "gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-pulseaudio",
    },
    "dnf": {
        "ffmpeg": "ffmpeg",
        "pactl": "pulseaudio-utils",
        "xwininfo": "xwininfo",
        "gst": "gstreamer1-plugins-good gstreamer1-plugins-bad-free gstreamer1-plugins-ugly "
        "pipewire-gstreamer gstreamer1-plugin-openh264",
    },
    "pacman": {
        "ffmpeg": "ffmpeg",
        "pactl": "libpulse",
        "xwininfo": "xorg-xwininfo",
        "gst": "gstreamer gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-plugin-pipewire",
    },
    "zypper": {
        "ffmpeg": "ffmpeg",
        "pactl": "pulseaudio-utils",
        "xwininfo": "xwininfo",
        "gst": "gstreamer-plugins-good gstreamer-plugins-bad gstreamer-plugins-ugly gstreamer-plugin-pipewire",
    },
}

INSTALL_CMD = {
    "apt": "sudo apt install -y",
    "dnf": "sudo dnf install -y",
    "pacman": "sudo pacman -S --needed",
    "zypper": "sudo zypper install -y",
}


@dataclass
class Check:
    key: str
    label: str
    ok: bool
    why: str
    required: bool = True


def check_dependencies() -> list[Check]:
    st = session_type()
    out = [
        Check("ffmpeg", "FFmpeg (does the recording)", paths.ffmpeg() is not None,
              "Encodes and saves your videos."),
    ]
    if st == "windows":
        return out
    out.append(Check("pactl", "PulseAudio/PipeWire tools", shutil.which("pactl") is not None,
                     "Finds your speakers and microphone.", required=False))
    if st == "x11":
        out.append(Check("xwininfo", "xwininfo (window picker)", shutil.which("xwininfo") is not None,
                         "Lets you click a window to record just that window.", required=False))
    if st == "wayland":
        gst = shutil.which("gst-launch-1.0") is not None
        pw = gst and _gst_has("pipewiresrc")
        out.append(Check("gst", "GStreamer + PipeWire plugin", pw,
                         "Wayland only lets apps see the screen through PipeWire."))
    return out


def install_command(missing: list[str]) -> str:
    pm = package_manager()
    if not pm or not missing:
        return ""
    pk = " ".join(PACKAGES[pm][m] for m in missing if m in PACKAGES[pm])
    return f"{INSTALL_CMD[pm]} {pk}"


def _gst_has(element: str) -> bool:
    try:
        r = subprocess.run(["gst-inspect-1.0", "--exists", element], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


gst_has = _gst_has


# ---------- graphics cards (for GPU-encoding advice) ----------

@dataclass
class GpuAdvice:
    text: str
    packages: list  # apt/dnf/... packages that would fix it (installable with pkexec)
    driver_manager: bool = False  # suggest the distro's driver tool (NVIDIA)


def gpus() -> list[str]:
    """'nvidia' / 'amd' / 'intel' for each graphics card lspci reports."""
    out = []
    try:
        txt = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return out
    for line in txt.splitlines():
        if not re.search(r"VGA compatible|3D controller|Display controller", line):
            continue
        low = line.lower()
        if "nvidia" in low:
            out.append("nvidia")
        elif "amd" in low or "ati " in low or "radeon" in low:
            out.append("amd")
        elif "intel" in low:
            out.append("intel")
    return out


def nvidia_driver_loaded() -> bool:
    return os.path.exists("/proc/driver/nvidia/version")


VA_PACKAGES = {
    "apt": {"intel": ["intel-media-va-driver-non-free"], "amd": ["mesa-va-drivers"]},
    "dnf": {"intel": ["intel-media-driver"], "amd": ["mesa-va-drivers"]},
    "pacman": {"intel": ["intel-media-driver"], "amd": ["libva-mesa-driver"]},
    "zypper": {"intel": ["intel-media-driver"], "amd": ["libva-mesa-driver"]},
}


def gpu_advice() -> list[GpuAdvice]:
    """Why GPU encoding might not work here, and what would fix it."""
    if paths.IS_WINDOWS:
        return []
    found = gpus()
    pm = package_manager()
    advice = []
    if "nvidia" in found:
        if not nvidia_driver_loaded():
            advice.append(GpuAdvice(
                "Your NVIDIA card can record with almost no CPU, but it needs NVIDIA's own driver "
                "(right now it's using the open 'nouveau' driver, which can't encode video). Open "
                "Driver Manager, pick the recommended nvidia-driver, and restart.", [], driver_manager=True))
        else:
            advice.append(GpuAdvice(
                "The NVIDIA driver is installed but its video encoder didn't answer. Updating the driver in "
                "Driver Manager and restarting usually fixes it.", [], driver_manager=True))
    for vendor in ("intel", "amd"):
        if vendor in found:
            pk = VA_PACKAGES.get(pm, {}).get(vendor, [])
            name = "Intel" if vendor == "intel" else "AMD"
            advice.append(GpuAdvice(
                f"Your {name} graphics can also record on the GPU once its video driver is installed"
                + (f" ({', '.join(pk)})." if pk else "."), pk))
    return advice


def driver_manager_command() -> list[str] | None:
    for cmd in (["mintdrivers"], ["software-properties-gtk", "--open-tab=4"], ["nvidia-settings"]):
        if shutil.which(cmd[0]):
            return cmd
    return None


# ---------- audio devices ----------

@dataclass
class Device:
    id: str
    name: str


def audio_devices() -> tuple[list[Device], list[Device]]:
    """(desktop outputs to capture, microphones). First entry is 'Default'."""
    desk = [Device("", "Default (what you hear)")]
    mics = [Device("", "Default microphone")]
    if paths.IS_WINDOWS:
        for name in _dshow_devices("audio"):
            dev = Device(name, name)
            if re.search(r"stereo mix|what u hear|loopback|virtual-audio", name, re.I):
                desk.append(dev)
            else:
                mics.append(dev)
        return desk, mics
    if not shutil.which("pactl"):
        return desk, mics
    try:
        out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True,
                             text=True, timeout=5).stdout
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name = parts[1]
            pretty = _pretty_pulse(name)
            if name.endswith(".monitor"):
                desk.append(Device(name, pretty))
            else:
                mics.append(Device(name, pretty))
    except Exception:
        pass
    return desk, mics


def _pretty_pulse(name: str) -> str:
    n = name.replace("alsa_output.", "").replace("alsa_input.", "").replace(".monitor", " (output)")
    return n.replace("_", " ").replace("-", " ")


def default_desktop_source() -> str:
    """PulseAudio name for 'what you hear' on Linux."""
    try:
        sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True,
                              timeout=3).stdout.strip()
        if sink:
            return sink + ".monitor"
    except Exception:
        pass
    return "@DEFAULT_MONITOR@"


def default_mic_source() -> str:
    try:
        src = subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True,
                             timeout=3).stdout.strip()
        if src:
            return src
    except Exception:
        pass
    return "default"


def _dshow_devices(kind: str) -> list[str]:
    ff = paths.ffmpeg()
    if not ff:
        return []
    try:
        r = subprocess.run([ff, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                           capture_output=True, text=True, timeout=8, creationflags=paths.no_window_flags())
    except Exception:
        return []
    names = []
    for line in r.stderr.splitlines():
        m = re.search(r'"([^"]+)"\s*\((audio|video)\)', line)
        if m and m.group(2) == kind:
            names.append(m.group(1))
    return names


def webcams() -> list[Device]:
    """Webcams Qt can open. Qt Multimedia is imported lazily to keep startup light."""
    try:
        from PySide6.QtMultimedia import QMediaDevices

        return [Device(bytes(d.id()).decode(errors="ignore"), d.description()) for d in QMediaDevices.videoInputs()]
    except Exception:
        return []


# ---------- windows (for "record one window") ----------

def list_windows_win32() -> list[str]:
    """Titles of visible top-level windows (Windows test build)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    titles = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # no owner
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                t = buf.value
                if t and t not in ("Program Manager", "Settings") and t not in titles:
                    titles.append(t)
        return True

    user32.EnumWindows(cb, 0)
    return titles


def pick_window_x11() -> tuple[str, str] | None:
    """Run xwininfo so the user can click a window. Returns (window id, title)."""
    if not shutil.which("xwininfo"):
        return None
    try:
        out = subprocess.run(["xwininfo"], capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return None
    m = re.search(r'Window id: (0x[0-9a-fA-F]+)\s*"?(.*?)"?\s*$', out, re.M)
    if not m:
        return None
    return m.group(1), m.group(2) or m.group(1)
