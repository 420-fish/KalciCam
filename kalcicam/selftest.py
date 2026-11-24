"""`kalcicam --selftest`: record a few seconds for real and report what happened.

Used by the GitHub build (on a virtual X11 screen) to make sure the packaged app
can actually record on Linux, and handy for users: the printed report shows what
works on their machine.
"""

import os
import sys
import tempfile
import time

from . import __version__, capture, config, encoders, paths, sysinfo


def run(app) -> int:
    from PySide6.QtCore import QTimer

    from .recorder import Recorder, run_quiet

    results = []

    def check(name, ok, detail=""):
        results.append((name, ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""), flush=True)

    print(f"KalciCam {__version__} self-test  ({sysinfo.session_type()}, {sys.platform})", flush=True)

    # 1. Our bundled libraries must not leak into programs we launch.
    ld = os.environ.get("LD_LIBRARY_PATH", "")
    bundle = getattr(sys, "_MEIPASS", None)
    check("clean library path for child programs", not (bundle and bundle in ld), ld or "(empty)")

    # 2. ffmpeg present and working.
    ff = paths.ffmpeg()
    check("ffmpeg found", ff is not None, ff or "not installed")
    if not ff:
        return 1
    check("ffmpeg runs", run_quiet([ff, "-hide_banner", "-version"], timeout=15))

    # 3. Encoder detection.
    key = encoders.detect(lambda lbl, ok, err="": print(f"       {'ok  ' if ok else 'no  '} {lbl}"
                                                      + (f"  ({err})" if err and not ok else ""), flush=True))
    gpu = any(e.key == key and e.gpu for e in encoders.candidates())
    check("encoder detected", bool(key), f"{encoders.label(key)}{'' if gpu else ' (no GPU encoder)'}")
    if not gpu:
        for a in sysinfo.gpu_advice():
            print("       hint:", a.text, flush=True)

    # 4. Record 3 s, pause 1 s, record 2 s, stop: from worker threads, like the real app.
    tmp = tempfile.mkdtemp(prefix="kalcicam-selftest-")
    s = config.Settings()
    s.save_dir = tmp
    s.detected_encoder = key
    s.vaapi_device = encoders.VAAPI_DEVICE
    s.desktop_audio = False
    s.mic = False
    s.fps = 30
    rec = Recorder(s)
    state = {"saved": None, "errors": [], "states": []}
    rec.saved.connect(lambda p: state.__setitem__("saved", p))
    rec.error.connect(lambda e: state["errors"].append(e))
    rec.state_changed.connect(lambda st: state["states"].append(st))

    def step(t, fn):
        QTimer.singleShot(int(t * 1000), fn)

    step(0.1, rec.start)
    step(3.5, rec.pause_toggle)
    step(4.5, rec.pause_toggle)
    step(7.0, rec.stop)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and not (state["saved"] or ("idle" in state["states"][2:])):
        app.processEvents()
        time.sleep(0.05)
    # give a moment for any late error signal
    end = time.monotonic() + 1
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.05)

    check("recording did not crash", not state["errors"], " | ".join(e.splitlines()[0] for e in state["errors"]))
    saved = state["saved"]
    size = os.path.getsize(saved) if saved and os.path.exists(saved) else 0
    check("video file saved", size > 10_000, f"{saved} ({size} bytes)" if saved else "no file")
    if saved and ff:
        probe = paths.find_program("ffprobe")
        if probe:
            import subprocess

            out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  saved], capture_output=True, text=True).stdout.strip()
            try:
                dur = float(out)
            except ValueError:
                dur = 0
            check("video length ~5 s (pause skipped)", 3.5 <= dur <= 7.5, f"{dur:.2f} s")
    print(f"log: {paths.cache_dir() / 'kalcicam.log'}", flush=True)
    failed = [n for n, ok in results if not ok]
    print("RESULT:", "ALL PASSED" if not failed else f"{len(failed)} FAILED", flush=True)
    return 1 if failed else 0
