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
import time

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


NAMES = {"record": "toggle", "pause": "pause", "screenshot": "screenshot", "replay": "replay"}


class GlobalHotkeys:
    """App-level global hotkeys (pynput). `ok` / `error` say whether they really work."""

    def __init__(self, bridge: Bridge):
        self.bridge = bridge
        self.listener = None
        self.error = ""
        self.ok = False

    @staticmethod
    def supported() -> bool:
        return sysinfo.session_type() != "wayland"

    def apply(self, hotkeys: dict, enabled: bool = True) -> bool:
        """(Re)start listening. Returns True if the hotkeys are live."""
        self.stop()
        self.ok = False
        self.error = ""
        if not enabled:
            return False
        if not self.supported():
            self.error = "Wayland doesn't let apps listen for keys in the background."
            return False
        try:
            from pynput import keyboard
        except Exception as e:  # missing backend / X11 library
            self.error = f"The key listener couldn't load ({e})."
            return False
        combos = []
        for k, cmd in NAMES.items():
            hk = hotkeys.get(k)
            if not hk:
                continue
            try:
                combos.append(keyboard.HotKey(keyboard.HotKey.parse(hk),
                                              lambda c=cmd: self.bridge.command.emit(c)))
            except ValueError:
                pass  # an unusual key name; the others still work
        try:
            # A plain Listener + HotKey matching instead of GlobalHotKeys: GlobalHotKeys
            # ignores "injected" key presses, so macro keys, Stream Decks and remote
            # tools couldn't trigger KalciCam.
            holder = {}

            def on_press(key, injected=False):
                k = holder["l"].canonical(key)
                for h in combos:
                    h.press(k)

            def on_release(key, injected=False):
                k = holder["l"].canonical(key)
                for h in combos:
                    h.release(k)

            listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            holder["l"] = listener
            listener.daemon = True
            listener.start()
            # The listener connects to the display in its own thread; failures happen
            # there, so wait until it's ready (or has died) and check.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and listener.is_alive():
                ready = getattr(listener, "_ready", False)  # bool or Event, depending on pynput version
                if ready.is_set() if hasattr(ready, "is_set") else bool(ready):
                    break
                time.sleep(0.05)
            if not listener.is_alive():
                try:
                    listener.join(0.2)  # re-raises the thread's exception
                    self.error = "The key listener stopped right after starting."
                except Exception as e:
                    self.error = f"The key listener couldn't start ({e})."
                return False
            self.listener = listener
            self.ok = True
            return True
        except Exception as e:
            self.error = f"The key listener couldn't start ({e})."
            return False

    def stop(self):
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
            self.listener = None
        self.ok = False


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


def is_cinnamon() -> bool:
    return "cinnamon" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower() and shutil.which("gsettings") is not None


def system_shortcuts_available() -> bool:
    return is_gnome() or is_cinnamon()


def desktop_name() -> str:
    return "Cinnamon" if is_cinnamon() else ("GNOME" if is_gnome() else "your desktop")


def add_system_shortcuts(hotkeys: dict) -> tuple[bool, str]:
    if is_cinnamon():
        return add_cinnamon_shortcuts(hotkeys)
    if is_gnome():
        return add_gnome_shortcuts(hotkeys)
    return False, "Automatic shortcuts only work on Cinnamon (Linux Mint) and GNOME."


def remove_system_shortcuts():
    """Undo add_system_shortcuts (so keys don't fire twice when app hotkeys are back on)."""
    try:
        if is_cinnamon():
            key = "org.cinnamon.desktop.keybindings"
            cur = _gs_list(key, "custom-list")
            keep = [n for n in cur if not n.startswith("kalcicam-")]
            subprocess.run(["gsettings", "set", key, "custom-list", _gs_fmt(keep)], timeout=5)
        elif is_gnome():
            base = "org.gnome.settings-daemon.plugins.media-keys"
            cur = _gs_list(base, "custom-keybindings")
            keep = [p for p in cur if "/kalcicam-" not in p]
            subprocess.run(["gsettings", "set", base, "custom-keybindings", _gs_fmt(keep)], timeout=5)
    except Exception:
        pass


def _gs_list(schema, key) -> list:
    out = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True, timeout=5).stdout.strip()
    if out.startswith("@as") or not out.startswith("["):
        return []
    return [p.strip().strip("'\"") for p in out.strip("[]").split(",") if p.strip()]


def _gs_fmt(items) -> str:
    return "[" + ", ".join(f"'{i}'" for i in items) + "]"


def add_cinnamon_shortcuts(hotkeys: dict) -> tuple[bool, str]:
    """Linux Mint: register Cinnamon custom keyboard shortcuts that call `kalcicam --<cmd>`."""
    key = "org.cinnamon.desktop.keybindings"
    try:
        names = [n for n in _gs_list(key, "custom-list") if not n.startswith("kalcicam-")]
        exe = launch_command()
        for k, cmd in NAMES.items():
            accel = _gnome_accel(hotkeys.get(k, ""))
            if not accel:
                continue
            name = f"kalcicam-{cmd}"
            path = f"/org/cinnamon/desktop/keybindings/custom-keybindings/{name}/"
            schema = f"{key}.custom-keybinding:{path}"
            subprocess.run(["gsettings", "set", schema, "name", f"KalciCam {cmd}"], timeout=5, check=True)
            subprocess.run(["gsettings", "set", schema, "command", f"{exe} --{cmd}"], timeout=5, check=True)
            subprocess.run(["gsettings", "set", schema, "binding", _gs_fmt([accel])], timeout=5, check=True)
            names.append(name)
        subprocess.run(["gsettings", "set", key, "custom-list", _gs_fmt(names)], timeout=5, check=True)
        return True, "Shortcuts added to Cinnamon (System Settings > Keyboard > Shortcuts > Custom Shortcuts)."
    except Exception as e:
        return False, f"Couldn't add Cinnamon shortcuts: {e}"


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
