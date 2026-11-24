"""Global hotkeys + a tiny local socket so `kalcicam --toggle` etc. work.

* Windows and X11: pynput listens for the hotkeys system-wide.
* Wayland doesn't let apps grab keys, so users bind a desktop shortcut to
  `kalcicam --toggle` (the setup wizard can do that for GNOME automatically).
"""

import getpass
import os
import shlex
import shutil
import subprocess
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import sysinfo

COMMANDS = ("toggle", "pause", "screenshot", "replay", "show")


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"kalcicam-{user}"


def send_command(cmd: str, timeout=800) -> bool:
    """Send a command to a running KalciCam. False if none is running."""
    s = QLocalSocket()
    s.connectToServer(server_name())
    if not s.waitForConnected(timeout):
        return False
    s.write((cmd + "\n").encode())
    s.flush()
    s.waitForBytesWritten(timeout)
    s.disconnectFromServer()
    return True


class Bridge(QObject):
    """Signals that background threads (pynput, socket) use to reach the UI."""
    command = Signal(str)
    clicked = Signal(bool)


class CommandServer:
    def __init__(self, bridge: Bridge):
        self.bridge = bridge
        self.server = QLocalServer()
        QLocalServer.removeServer(server_name())  # clear a stale socket from a crash
        self.server.listen(server_name())
        self.server.newConnection.connect(self._on_conn)

    def _on_conn(self):
        while self.server.hasPendingConnections():
            c = self.server.nextPendingConnection()
            c.readyRead.connect(lambda c=c: self._read(c))

    def _read(self, c):
        for line in bytes(c.readAll()).decode(errors="ignore").splitlines():
            if line.strip() in COMMANDS:
                self.bridge.command.emit(line.strip())


class GlobalHotkeys:
    def __init__(self, bridge: Bridge):
        self.bridge = bridge
        self.listener = None
        self.error = ""

    @staticmethod
    def supported() -> bool:
        return sysinfo.session_type() != "wayland"

    def apply(self, hotkeys: dict):
        self.stop()
        if not self.supported():
            return
        try:
            from pynput import keyboard
        except Exception as e:
            self.error = f"pynput unavailable: {e}"
            return
        names = {"record": "toggle", "pause": "pause", "screenshot": "screenshot", "replay": "replay"}
        mapping = {}
        for k, cmd in names.items():
            hk = hotkeys.get(k)
            if hk:
                mapping[hk] = (lambda c=cmd: self.bridge.command.emit(c))
        try:
            self.listener = keyboard.GlobalHotKeys(mapping)
            self.listener.daemon = True
            self.listener.start()
            self.error = ""
        except Exception as e:
            self.error = str(e)
            self.listener = None

    def stop(self):
        if self.listener:
            self.listener.stop()
            self.listener = None


def launch_command() -> str:
    """The command that starts this KalciCam (AppImage, frozen binary, or source)."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return shlex.quote(appimage)
    if getattr(sys, "frozen", False):
        return shlex.quote(sys.executable)
    main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    return f"{shlex.quote(sys.executable)} {shlex.quote(main)}"


def is_gnome() -> bool:
    return "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower() and shutil.which("gsettings") is not None


def add_gnome_shortcuts(hotkeys: dict) -> tuple[bool, str]:
    """Register GNOME custom keyboard shortcuts that call `kalcicam --<cmd>`."""
    base = "org.gnome.settings-daemon.plugins.media-keys"
    path_root = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
    names = {"record": "toggle", "pause": "pause", "screenshot": "screenshot", "replay": "replay"}
    try:
        cur = subprocess.run(["gsettings", "get", base, "custom-keybindings"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        existing = [] if cur.startswith("@as") else [p.strip(" '") for p in cur.strip("[]").split(",") if p.strip()]
        exe = launch_command()
        for key, cmd in names.items():
            accel = _gnome_accel(hotkeys.get(key, ""))
            if not accel:
                continue
            p = f"{path_root}kalcicam-{cmd}/"
            if p not in existing:
                existing.append(p)
            schema = f"{base}.custom-keybinding:{p}"
            subprocess.run(["gsettings", "set", schema, "name", f"KalciCam {cmd}"], timeout=5)
            subprocess.run(["gsettings", "set", schema, "command", f"{exe} --{cmd}"], timeout=5)
            subprocess.run(["gsettings", "set", schema, "binding", accel], timeout=5)
        lst = "[" + ", ".join(f"'{p}'" for p in existing) + "]"
        subprocess.run(["gsettings", "set", base, "custom-keybindings", lst], timeout=5, check=True)
        return True, "Shortcuts added to GNOME Settings > Keyboard > Custom Shortcuts."
    except Exception as e:
        return False, f"Couldn't add shortcuts: {e}"


def _gnome_accel(hk: str) -> str:
    """'<shift>+<f12>' -> '<Shift>F12'"""
    if not hk:
        return ""
    out = ""
    for part in hk.split("+"):
        if part in ("<ctrl>", "<shift>", "<alt>"):
            out += "<" + part.strip("<>").capitalize() + ">"
        elif part == "<cmd>":
            out += "<Super>"
        else:
            k = part.strip("<>")
            out += k.upper() if k.startswith("f") and k[1:].isdigit() else k
    return out
