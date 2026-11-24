"""Runs capture processes and turns their pieces into finished videos.

* Recording is written as MKV pieces (crash-safe). Pause = stop the current
  piece, resume = start a new one. On stop, pieces are joined and remuxed to
  MP4 with `-c copy`, which takes almost no CPU.
* The replay buffer keeps rolling 2-second MKV chunks; "save replay" joins the
  last N seconds.
* All blocking work happens in worker threads; Qt signals carry results back.
"""

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication

from . import capture, encoders, paths, sysinfo
from .config import Settings


# ---------------- process wrapper ----------------

_log_lock = threading.Lock()


def _die_with_parent():
    """Linux: if KalciCam crashes, its capture process stops too (MKV stays playable)."""
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:
        pass


_job_handle = None


def _bind_to_app(proc):
    """Windows: put capture processes in a job object that closes with KalciCam."""
    global _job_handle
    if not paths.IS_WINDOWS:
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        if _job_handle is None:
            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in
                            ("r_ops", "w_ops", "o_ops", "r_bytes", "w_bytes", "o_bytes")]

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("Basic", BASIC), ("Io", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32.CreateJobObjectW.restype = wintypes.HANDLE
            job = k32.CreateJobObjectW(None, None)
            info = EXTENDED()
            info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
            _job_handle = job
        k32.AssignProcessToJobObject(wintypes.HANDLE(_job_handle), wintypes.HANDLE(int(proc._handle)))
    except Exception as e:
        log(f"couldn't bind process to app: {e}")


def log(msg: str):
    """Append to kalcicam.log in the cache folder (handy when something goes wrong)."""
    try:
        p = paths.cache_dir() / "kalcicam.log"
        with _log_lock:
            if p.exists() and p.stat().st_size > 2_000_000:
                p.replace(p.with_suffix(".old.log"))
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


class Runner:
    """One capture process. Reads ffmpeg's -progress output for live stats."""

    def __init__(self, job: capture.Job, on_exit=None):
        self.job = job
        self.on_exit = on_exit
        self.fps = 0.0
        self.started = time.monotonic()
        self.stopping = False
        self.err_tail: list[str] = []
        kw = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                  creationflags=paths.no_window_flags())
        if job.pass_fds:
            kw["pass_fds"] = job.pass_fds
        env = dict(os.environ, **job.env) if job.env else None
        if paths.IS_LINUX:
            kw["preexec_fn"] = _die_with_parent
        log("start: " + subprocess.list2cmdline(job.cmd))
        self.proc = subprocess.Popen(job.cmd, env=env, **kw)
        _bind_to_app(self.proc)
        for fd in job.pass_fds:  # the child has its own copy now
            try:
                os.close(fd)
            except OSError:
                pass
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        threading.Thread(target=self._wait, daemon=True).start()

    def _read_out(self):
        for raw in self.proc.stdout:
            line = raw.decode(errors="ignore").strip()
            if line.startswith("fps="):
                try:
                    self.fps = float(line[4:])
                except ValueError:
                    pass

    def _read_err(self):
        for raw in self.proc.stderr:
            line = raw.decode(errors="ignore").strip()
            if line:
                self.err_tail = (self.err_tail + [line])[-12:]

    def _wait(self):
        code = self.proc.wait()
        time.sleep(0.1)  # let the stderr reader catch the last lines
        log(f"exit {code} after {time.monotonic() - self.started:.1f}s"
            + ("" if self.stopping else " (unexpected)") + "\n    " + "\n    ".join(self.err_tail))
        if self.on_exit:
            self.on_exit(self, code)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self, timeout=15) -> int:
        """Ask nicely so the file gets finished properly, then insist."""
        self.stopping = True
        if not self.alive():
            return self.proc.returncode
        try:
            if self.job.stop == "sigint":
                self.proc.send_signal(signal.SIGINT)
            else:
                self.proc.stdin.write(b"q")
                self.proc.stdin.flush()
        except Exception:
            pass
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                return self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                return -9


def run_quiet(cmd, timeout=600) -> bool:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=paths.no_window_flags())
        return r.returncode == 0
    except Exception:
        return False


# ---------------- where to capture ----------------

def _win_monitor_rects() -> dict:
    """Windows: device name -> physical (x, y) of each monitor."""
    import ctypes
    from ctypes import wintypes

    class MONITORINFOEX(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

    out = {}
    user32 = ctypes.windll.user32
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                              wintypes.LPARAM)

    def cb(hmon, _hdc, _rect, _):
        mi = MONITORINFOEX()
        mi.cbSize = ctypes.sizeof(MONITORINFOEX)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        out[mi.szDevice] = (mi.rcMonitor.left, mi.rcMonitor.top)
        return True

    user32.EnumDisplayMonitors(0, 0, proc(cb), 0)
    return out


def screen_target(s: Settings) -> dict:
    """Work out the capture area from settings + Qt screens. Call on the UI thread.

    Wayland returns {} plus hints; the portal fills in the rest later.
    """
    screens = QGuiApplication.screens()
    st = sysinfo.session_type()
    if not screens:
        return {}
    region = s.region if (s.mode == "region" and s.region) else None
    idx = region.get("screen", 0) if region else s.monitor
    idx = max(0, min(idx, len(screens) - 1))
    scr = screens[idx]
    dpr = scr.devicePixelRatio()
    g = scr.geometry()
    phys_w, phys_h = round(g.width() * dpr), round(g.height() * dpr)

    pixels = phys_w * phys_h
    if st == "wayland":
        t = {"pixels": pixels, "source": "window" if s.mode == "window" else "monitor", "screen_size": (phys_w, phys_h)}
        if region:
            t["region"] = region
        return t

    if st == "x11":
        if s.mode == "window" and s.window_id:
            return {"window_id": s.window_id, "pixels": pixels}
        ox, oy = round(g.x() * dpr), round(g.y() * dpr)
        if region:
            return {"x": ox + region["x"], "y": oy + region["y"], "w": region["w"], "h": region["h"]}
        return {"x": ox, "y": oy, "w": phys_w, "h": phys_h}

    # Windows
    if s.mode == "window" and s.window_title:
        return {"title": s.window_title, "pixels": pixels}
    t = {"output_idx": idx, "pixels": pixels}
    origin = _win_monitor_rects().get(scr.name(), (round(g.x() * dpr), round(g.y() * dpr)))
    if region:
        t.update(rx=region["x"], ry=region["y"], w=region["w"], h=region["h"],
                 ax=origin[0] + region["x"], ay=origin[1] + region["y"])
    else:
        t.update(ax=origin[0], ay=origin[1])
        if s.win_capture_engine == "gdigrab":
            t.update(w=phys_w, h=phys_h)
    return t


class WaylandSource:
    """Keeps one portal session for the app's lifetime so we only ask once."""

    def __init__(self):
        self.cast = None
        self.kind = None

    def ensure(self, s: Settings, target: dict) -> dict:
        from . import wayland_portal as wp

        kind = target.get("source", "monitor")
        if self.cast is None or self.kind != kind:
            self.close()
            cast = wp.ScreenCast()
            try:
                cast.start(wp.WINDOW if kind == "window" else wp.MONITOR, s.draw_mouse,
                           s.wayland_restore_token if kind == "monitor" else "")
            except Exception:
                cast.close()
                raise
            self.cast, self.kind = cast, kind
            if cast.restore_token and kind == "monitor":
                s.wayland_restore_token = cast.restore_token
        t = dict(target, node=self.cast.node, fd=self.cast.open_fd())
        region = target.get("region")
        if region:
            sw, sh = self.cast.size or target["screen_size"]
            t["crop"] = {"l": region["x"], "t": region["y"],
                         "r": max(0, sw - region["x"] - region["w"]),
                         "b": max(0, sh - region["y"] - region["h"])}
        return t

    def close(self):
        if self.cast:
            self.cast.close()
        self.cast = None


WAYLAND = WaylandSource()


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


# ---------------- recorder ----------------

class Recorder(QObject):
    state_changed = Signal(str)  # idle | starting | recording | paused | saving
    error = Signal(str)
    saved = Signal(str)  # final file path

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self.state = "idle"
        self.runner: Runner | None = None
        self.parts: list[str] = []
        self.work: Path | None = None
        self.name = ""
        self._target = {}
        self._t0 = 0.0
        self._paused_total = 0.0
        self._paused_at = 0.0
        self._lock = threading.Lock()
        self._fallback_tried = False

    # ---- info for the UI ----
    def elapsed(self) -> float:
        if self.state in ("idle", "starting"):
            return 0.0
        now = self._paused_at if self.state == "paused" else time.monotonic()
        return max(0.0, now - self._t0 - self._paused_total)

    def fps(self) -> float:
        return self.runner.fps if self.runner and self.state == "recording" else 0.0

    def size_bytes(self) -> int:
        total = 0
        for p in self.parts:
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        return total

    def _set(self, st):
        self.state = st
        self.state_changed.emit(st)

    # ---- controls ----
    def start(self):
        if self.state != "idle":
            return
        if not paths.ffmpeg() and encoders.backend() != "gst":
            self.error.emit("FFmpeg isn't installed. Open Settings > Run setup again.")
            return
        self.name = "KalciCam_" + stamp()
        self.work = self.s.video_dir() / f".{self.name}_parts"
        self.work.mkdir(parents=True, exist_ok=True)
        self.parts = []
        self._paused_total = 0.0
        self._fallback_tried = False
        self._target = screen_target(self.s)
        self._gdi_target = None
        if encoders.backend() == "ffmpeg_win":
            # Worked out now on the UI thread, in case GPU capture fails and we fall back.
            old = self.s.win_capture_engine
            self.s.win_capture_engine = "gdigrab"
            self._gdi_target = dict(screen_target(self.s), _gdi=True)
            self.s.win_capture_engine = old
        self._set("starting")
        threading.Thread(target=self._start_piece, args=(True,), daemon=True).start()

    def _start_piece(self, first: bool):
        try:
            target = self._target
            if encoders.backend() == "gst":
                target = WAYLAND.ensure(self.s, target)
            part = str(self.work / f"part{len(self.parts):03d}.mkv")
            job = capture.build_job(self.s, target, part)
            with self._lock:
                self.runner = Runner(job, on_exit=self._on_exit)
                self.parts.append(part)
            if first:
                self._t0 = time.monotonic()
            else:
                self._paused_total += time.monotonic() - self._paused_at
            self._set("recording")
        except Exception as e:
            msg = "Screen sharing was cancelled." if "cancelled" in str(e) else f"Couldn't start recording: {e}"
            self.error.emit(msg)
            self._cleanup_empty()
            self._set("idle")

    def _on_exit(self, runner: Runner, code: int):
        if runner is not self.runner or runner.stopping:
            return
        # The capture died on its own.
        quick = time.monotonic() - runner.started < 4
        if (quick and not self._fallback_tried and self._gdi_target and not self._target.get("_gdi")
                and self.s.win_capture_engine == "ddagrab" and "title" not in self._target):
            # Desktop Duplication can fail on some setups; retry with GDI capture.
            log("GPU capture failed, falling back to GDI")
            self._fallback_tried = True
            self._target = self._gdi_target
            with self._lock:
                if self.parts:
                    self.parts.pop()
            threading.Thread(target=self._start_piece_gdi, daemon=True).start()
            return
        detail = runner.err_tail[-1] if runner.err_tail else f"exit code {code}"
        self.error.emit(f"Recording stopped unexpectedly: {detail}")
        threading.Thread(target=self._finish, daemon=True).start()

    def _start_piece_gdi(self, first=True):
        old = self.s.win_capture_engine
        self.s.win_capture_engine = "gdigrab"
        try:
            self._start_piece(first)
        finally:
            self.s.win_capture_engine = old

    def toggle(self):
        if self.state == "idle":
            self.start()
        elif self.state in ("recording", "paused"):
            self.stop()

    def pause_toggle(self):
        if self.state == "recording":
            self._paused_at = time.monotonic()
            r = self.runner
            self._set("paused")
            threading.Thread(target=r.stop, daemon=True).start()
        elif self.state == "paused":
            self._set("starting")
            fn = self._start_piece_gdi if self._target.get("_gdi") else self._start_piece
            threading.Thread(target=fn, args=(False,), daemon=True).start()

    def stop(self):
        if self.state not in ("recording", "paused"):
            return
        if self.state == "paused":
            self._paused_total += time.monotonic() - self._paused_at
        self._set("saving")
        threading.Thread(target=self._finish, daemon=True).start()

    def _finish(self):
        if self.runner:
            self.runner.stop()
        parts = [p for p in self.parts if os.path.exists(p) and os.path.getsize(p) > 1024]
        out_dir = self.s.video_dir()
        ext = ".mp4" if self.s.final_format == "mp4" else ".mkv"
        final = str(out_dir / (self.name + ext))
        ok = False
        if len(parts) == 1:
            if ext == ".mkv":
                shutil.move(parts[0], final)
                ok = True
            else:
                ok = run_quiet(capture.remux_cmd(parts[0], final))
        elif len(parts) > 1:
            lst = self.work / "list.txt"
            lst.write_text("".join(f"file '{Path(p).as_posix()}'\n" for p in parts), encoding="utf-8")
            ok = run_quiet(capture.concat_cmd(str(lst), final))
        if ok and self.s.keep_mkv and ext == ".mp4" and parts:
            # Keep the raw MKV next to the MP4 too.
            if len(parts) == 1:
                shutil.copy2(parts[0], str(out_dir / (self.name + ".mkv")))
        if ok:
            shutil.rmtree(self.work, ignore_errors=True)
            self.saved.emit(final)
        elif parts:
            # Never lose a recording: leave the pieces where the user can find them.
            keep = out_dir / (self.name + "_pieces")
            shutil.move(str(self.work), str(keep))
            self.error.emit(f"Couldn't convert the video, but the raw MKV pieces are saved in:\n{keep}")
        else:
            self._cleanup_empty()
            self.error.emit("Nothing was recorded.")
        self.runner = None
        self._set("idle")

    def _cleanup_empty(self):
        if self.work and self.work.exists():
            shutil.rmtree(self.work, ignore_errors=True)

    def recover_leftovers(self):
        """After a crash or power cut, turn leftover MKV pieces into a normal video."""
        def work():
            d = self.s.video_dir()
            for parts_dir in d.glob(".KalciCam_*_parts"):
                if self.work and parts_dir == self.work:
                    continue
                name = parts_dir.name[1:-len("_parts")]
                parts = sorted(p for p in parts_dir.glob("part*.mkv") if p.stat().st_size > 1024)
                if not parts:
                    shutil.rmtree(parts_dir, ignore_errors=True)
                    continue
                lst = parts_dir / "list.txt"
                lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
                final = str(d / f"{name}_recovered.mp4")
                if run_quiet(capture.concat_cmd(str(lst), final)):
                    shutil.rmtree(parts_dir, ignore_errors=True)
                    log(f"recovered {final}")
                    self.saved.emit(final)
                else:
                    shutil.move(str(parts_dir), str(d / f"{name}_pieces"))
        threading.Thread(target=work, daemon=True).start()

    def shutdown(self):
        if self.state in ("recording", "paused"):
            self._set("saving")
            self._finish()


# ---------------- replay buffer ----------------

class ReplayBuffer(QObject):
    state_changed = Signal(bool)
    error = Signal(str)
    saved = Signal(str)

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self.runner: Runner | None = None
        self.dir = paths.cache_dir() / "replay"

    def running(self) -> bool:
        return self.runner is not None and self.runner.alive()

    def start(self):
        if self.running():
            return
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        target = screen_target(self.s)
        threading.Thread(target=self._start, args=(target,), daemon=True).start()

    def _start(self, target):
        try:
            if encoders.backend() == "gst":
                target = WAYLAND.ensure(self.s, target)
            job = capture.build_job(self.s, target, "", replay_dir=str(self.dir))
            self.runner = Runner(job, on_exit=self._on_exit)
            self.state_changed.emit(True)
        except Exception as e:
            self.error.emit(f"Replay buffer couldn't start: {e}")
            self.state_changed.emit(False)

    def _on_exit(self, runner, code):
        if not runner.stopping:
            detail = runner.err_tail[-1] if runner.err_tail else f"exit code {code}"
            self.error.emit(f"Replay buffer stopped: {detail}")
        self.state_changed.emit(False)

    def stop(self):
        r, self.runner = self.runner, None
        if r:
            r.stop()
        self.state_changed.emit(False)

    def save(self):
        if not self.running():
            self.error.emit("Replay buffer isn't running. Turn it on in the Extras tab.")
            return
        threading.Thread(target=self._save, daemon=True).start()

    def _save(self):
        segs = sorted(self.dir.glob("seg*.mkv"), key=lambda p: p.stat().st_mtime)
        if len(segs) > 1:
            segs = segs[:-1]  # the newest chunk is still being written
        need = capture.replay_segments(self.s.replay_seconds) - 2
        segs = [p for p in segs[-need:] if p.stat().st_size > 1024]
        if not segs:
            self.error.emit("Nothing in the replay buffer yet, give it a few seconds.")
            return
        with tempfile.TemporaryDirectory() as td:
            lst = Path(td) / "list.txt"
            # Copy first so ffmpeg's segment_wrap can't overwrite them mid-join.
            local = []
            for i, p in enumerate(segs):
                dst = Path(td) / f"{i:03d}.mkv"
                shutil.copy2(p, dst)
                local.append(dst)
            lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in local), encoding="utf-8")
            ext = ".mp4" if self.s.final_format == "mp4" else ".mkv"
            final = str(self.s.video_dir() / f"KalciCam_Replay_{stamp()}{ext}")
            if run_quiet(capture.concat_cmd(str(lst), final)):
                self.saved.emit(final)
            else:
                self.error.emit("Couldn't save the replay.")
