"""Talk to xdg-desktop-portal so we can capture the screen on Wayland.

Flow: CreateSession -> SelectSources -> Start (the system shows its
"share your screen" picker) -> OpenPipeWireRemote gives a file descriptor
that GStreamer's pipewiresrc reads from.

With persist_mode=2 the portal hands back a restore token, so after the first
time KalciCam can start recording without asking again.

The session lives on its own asyncio thread for as long as we need it.
"""

import asyncio
import os
import secrets
import threading

try:  # dbus-fast is the maintained fork; dbus-next has the same API
    from dbus_fast import BusType, Message, MessageType, Variant
    from dbus_fast.aio import MessageBus
except ImportError:  # pragma: no cover
    from dbus_next import BusType, Message, MessageType, Variant
    from dbus_next.aio import MessageBus

DEST = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
SC = "org.freedesktop.portal.ScreenCast"
SHOT = "org.freedesktop.portal.Screenshot"

MONITOR, WINDOW = 1, 2
CURSOR_EMBEDDED, CURSOR_HIDDEN = 2, 1


class PortalError(RuntimeError):
    pass


class _Loop:
    """A private asyncio loop in a daemon thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True, name="portal")
        self.thread.start()

    def run(self, coro, timeout=None):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


class ScreenCast:
    def __init__(self):
        self._l = _Loop()
        self.bus = None
        self.session = None
        self.node = None
        self.size = None  # (w, h) of the stream, if the portal told us
        self.restore_token = ""
        self._waiters = {}

    # ---- public, blocking API (call from a worker thread, not the UI thread) ----

    def start(self, source=MONITOR, cursor=True, restore_token="", timeout=180):
        return self._l.run(self._start(source, cursor, restore_token), timeout)

    def open_fd(self) -> int:
        return self._l.run(self._open_fd(), 15)

    def close(self):
        try:
            if self.bus and self.session:
                self._l.run(self._call(self.session, "org.freedesktop.portal.Session", "Close", "", []), 5)
        except Exception:
            pass
        try:
            if self.bus:
                self.bus.disconnect()
        except Exception:
            pass
        self._l.stop()

    # ---- internals ----

    async def _connect(self):
        if self.bus:
            return
        self.bus = await MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True).connect()
        self.bus.add_message_handler(self._on_message)
        await self._call("/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s",
                         ["type='signal',interface='org.freedesktop.portal.Request',member='Response'"],
                         dest="org.freedesktop.DBus")

    def _on_message(self, msg):
        if msg.message_type == MessageType.SIGNAL and msg.member == "Response":
            fut = self._waiters.pop(msg.path, None)
            if fut and not fut.done():
                fut.set_result(msg.body)

    async def _call(self, path, iface, member, sig, body, dest=DEST):
        reply = await self.bus.call(Message(destination=dest, path=path, interface=iface, member=member,
                                            signature=sig, body=body))
        if reply.message_type == MessageType.ERROR:
            raise PortalError(f"{member}: {reply.error_name} {reply.body}")
        return reply

    async def _request(self, member, sig, body, options):
        """Call a portal method that answers later through a Request 'Response' signal."""
        token = "kalcicam_" + secrets.token_hex(6)
        sender = self.bus.unique_name.lstrip(":").replace(".", "_")
        req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        fut = asyncio.get_running_loop().create_future()
        self._waiters[req_path] = fut
        options["handle_token"] = Variant("s", token)
        await self._call(PATH, self._iface, member, sig, body + [options])
        code, results = await fut
        if code == 1:
            raise PortalError("cancelled")
        if code != 0:
            raise PortalError(f"{member} failed (code {code})")
        return results

    _iface = SC

    async def _start(self, source, cursor, restore_token):
        await self._connect()
        self._iface = SC
        res = await self._request("CreateSession", "a{sv}", [], {
            "session_handle_token": Variant("s", "kalcicam_s" + secrets.token_hex(4))})
        self.session = res["session_handle"].value

        opts = {
            "types": Variant("u", source),
            "multiple": Variant("b", False),
            "persist_mode": Variant("u", 2),
        }
        cursors = await self._get_prop(SC, "AvailableCursorModes")
        want = CURSOR_EMBEDDED if cursor else CURSOR_HIDDEN
        if cursors is None or cursors & want:
            opts["cursor_mode"] = Variant("u", want)
        if restore_token:
            opts["restore_token"] = Variant("s", restore_token)
        await self._request("SelectSources", "oa{sv}", [self.session], opts)

        res = await self._request("Start", "osa{sv}", [self.session, ""], {})
        streams = res["streams"].value
        if not streams:
            raise PortalError("no screen was shared")
        node, props = streams[0]
        self.node = int(node)
        if "size" in props:
            self.size = tuple(props["size"].value)
        if "restore_token" in res:
            self.restore_token = res["restore_token"].value
        return self.node, self.size, self.restore_token

    async def _open_fd(self):
        reply = await self._call(PATH, SC, "OpenPipeWireRemote", "oa{sv}", [self.session, {}])
        idx = reply.body[0]
        fd = reply.unix_fds[idx] if reply.unix_fds else idx
        # Hand the child process its own copy; we keep ours for later resumes.
        return os.dup(fd)

    async def _get_prop(self, iface, name):
        try:
            r = await self._call(PATH, "org.freedesktop.DBus.Properties", "Get", "ss", [iface, name])
            return r.body[0].value
        except Exception:
            return None


def screenshot(timeout=60) -> str:
    """Ask the portal for a full-screen screenshot. Returns a local file path."""
    sc = ScreenCast()
    try:
        async def go():
            await sc._connect()
            sc._iface = SHOT
            res = await sc._request("Screenshot", "sa{sv}", [""], {"interactive": Variant("b", False)})
            return res["uri"].value
        uri = sc._l.run(go(), timeout)
    finally:
        try:
            sc.bus.disconnect()
        except Exception:
            pass
        sc._l.stop()
    from urllib.parse import unquote, urlparse

    return unquote(urlparse(uri).path)
