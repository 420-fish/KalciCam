"""Video encoders, best (GPU, near-zero CPU) first.

Each backend has its own list:
  * ffmpeg_linux  - X11 capture (x11grab gives raw BGRx frames in RAM)
  * ffmpeg_win    - Windows test build (ddagrab gives frames that stay on the GPU)
  * gst           - Wayland (PipeWire frames through GStreamer)

`detect()` actually runs a tiny test encode with each one and keeps the first
that works, so "auto" never picks an encoder the machine can't use.
"""

import os
import subprocess
from dataclasses import dataclass

from . import paths, sysinfo


@dataclass
class Encoder:
    key: str
    label: str
    gpu: bool


FFMPEG_LINUX = [
    Encoder("nvenc", "NVIDIA NVENC (GPU)", True),
    Encoder("vaapi", "VA-API zero-copy (AMD/Intel GPU)", True),
    Encoder("vaapi_sw", "VA-API (AMD/Intel GPU)", True),
    Encoder("x264", "x264 ultrafast (CPU)", False),
]

FFMPEG_WIN = [
    Encoder("amf", "AMD AMF (GPU)", True),
    Encoder("nvenc", "NVIDIA NVENC (GPU)", True),
    Encoder("qsv", "Intel Quick Sync (GPU)", True),
    Encoder("x264", "x264 ultrafast (CPU)", False),
]

GST = [
    Encoder("gst_nvh264", "NVIDIA NVENC (GPU)", True),
    Encoder("gst_va", "VA (AMD/Intel GPU)", True),
    Encoder("gst_vaapi", "VA-API legacy (AMD/Intel GPU)", True),
    Encoder("gst_x264", "x264 ultrafast (CPU)", False),
    Encoder("gst_openh264", "OpenH264 (CPU)", False),
]


def backend() -> str:
    st = sysinfo.session_type()
    return {"windows": "ffmpeg_win", "x11": "ffmpeg_linux", "wayland": "gst"}[st]


def candidates(be: str | None = None) -> list[Encoder]:
    be = be or backend()
    return {"ffmpeg_win": FFMPEG_WIN, "ffmpeg_linux": FFMPEG_LINUX, "gst": GST}[be]


def label(key: str) -> str:
    for lst in (FFMPEG_WIN, FFMPEG_LINUX, GST):
        for e in lst:
            if e.key == key:
                return e.label
    return key


def render_nodes() -> list[str]:
    """Every GPU's render device. Hybrid laptops (Intel + NVIDIA) have two or more."""
    return [f"/dev/dri/renderD{n}" for n in range(128, 136) if os.path.exists(f"/dev/dri/renderD{n}")]


def render_node() -> str:
    nodes = render_nodes()
    return nodes[0] if nodes else "/dev/dri/renderD128"


# The render node VA-API worked on (set by detect(), or loaded from settings).
VAAPI_DEVICE = ""
# label -> last error line, for every encoder that failed in the last detect().
last_failures: dict[str, str] = {}


# ---------------- ffmpeg argument builders ----------------

def ffmpeg_pre_input(key: str, device: str = "") -> list[str]:
    """Global options that must come before the inputs."""
    if key in ("vaapi", "vaapi_sw"):
        return ["-vaapi_device", device or VAAPI_DEVICE or render_node()]
    return []


def ffmpeg_video_filter(key: str, be: str, hw_frames: bool) -> str:
    """Filter chain between capture and encoder ('' = none)."""
    if be == "ffmpeg_linux":
        if key == "vaapi":
            return "hwupload,scale_vaapi=format=nv12"  # colour conversion on the GPU
        if key == "vaapi_sw":
            return "format=nv12,hwupload"
        return ""
    # Windows: ddagrab frames live on the GPU (D3D11).
    if not hw_frames:
        return ""
    if key == "nvenc":
        return ""  # NVENC reads D3D11 frames directly: zero copy
    # AMF rejects ddagrab's BGRA D3D11 surfaces (SubmitInput error 18 on RDNA3),
    # so copy the frame to RAM first; encoding itself still runs on the GPU.
    if key == "qsv":
        return "hwmap=derive_device=qsv,format=qsv"
    return "hwdownload,format=bgra"  # x264 needs the frame in RAM


def bitrate_kbps(quality: str, fps: int, pixels: int = 1920 * 1080) -> int:
    """Target bitrate, Bandicam-style: scaled from a 1080p60 baseline."""
    base = {"high": 30000, "balanced": 14000, "small": 6000}.get(quality, 14000)
    scale = (max(pixels, 320 * 240) / (1920 * 1080)) ** 0.75 * (0.55 + 0.45 * min(fps, 120) / 60)
    return max(1500, int(base * scale))


def ffmpeg_encoder_args(key: str, qp: int, fps: int, kbps: int) -> list[str]:
    """Variable bitrate with a peak cap: small files on calm screens, room for fast action."""
    gop = max(1, fps * 2)
    peak = int(kbps * 1.6)
    rate = ["-b:v", f"{kbps}k", "-maxrate", f"{peak}k", "-bufsize", f"{peak * 2}k"]
    common = ["-g", str(gop)]
    if key == "nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p2", "-tune", "ll", "-rc", "vbr", "-cq", str(qp),
                "-bf", "0"] + rate + common
    if key in ("vaapi", "vaapi_sw"):
        return ["-c:v", "h264_vaapi", "-rc_mode", "VBR", "-bf", "0"] + rate + common
    if key == "amf":
        return ["-c:v", "h264_amf", "-usage", "lowlatency", "-quality", "speed", "-rc", "vbr_peak"] + rate + common
    if key == "qsv":
        return ["-c:v", "h264_qsv", "-preset", "veryfast"] + rate + common
    # CPU fallback, tuned to be as light as x264 gets.
    return ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-crf", str(qp),
            "-maxrate", f"{peak}k", "-bufsize", f"{peak * 2}k", "-pix_fmt", "yuv420p"] + common

# ---------------- GStreamer chain builders ----------------

def gst_encoder_chain(key: str, quality: str, fps: int, pixels: int = 1920 * 1080) -> str:
    kbps = bitrate_kbps(quality, fps, pixels)
    gop = fps * 2
    if key == "gst_nvh264":
        return f"videoconvert ! video/x-raw,format=NV12 ! nvh264enc bitrate={kbps} gop-size={gop} ! h264parse"
    if key == "gst_va":
        return (f"vapostproc ! video/x-raw(memory:VAMemory),format=NV12 ! "
                f"vah264enc bitrate={kbps} key-int-max={gop} ! h264parse")
    if key == "gst_vaapi":
        return f"vaapipostproc ! vaapih264enc bitrate={kbps} keyframe-period={gop} ! h264parse"
    if key == "gst_openh264":
        return f"videoconvert ! video/x-raw,format=I420 ! openh264enc bitrate={kbps * 1000} ! h264parse"
    return (f"videoconvert ! video/x-raw,format=I420 ! x264enc speed-preset=ultrafast tune=zerolatency "
            f"bitrate={kbps} key-int-max={gop} ! h264parse")


# ---------------- detection ----------------

def _test_cmd(key: str, be: str, device: str = "") -> list[str] | None:
    if be == "gst":
        gst = "gst-launch-1.0"
        chain = gst_encoder_chain(key, "balanced", 30)
        return [gst, "-q", "videotestsrc", "num-buffers=15", "!",
                "video/x-raw,width=640,height=360,format=BGRx,framerate=30/1", "!"] + \
            _split_gst(chain) + ["!", "fakesink"]
    ff = paths.ffmpeg()
    if not ff:
        return None
    if be == "ffmpeg_linux":
        src = ["-f", "lavfi", "-i", "color=black:s=640x360:r=30,format=bgr0"]
        hw = False
    else:
        # Test with the real Windows capture so we know zero-copy works here.
        src = ["-f", "lavfi", "-i", "ddagrab=output_idx=0:framerate=30"]
        hw = True
    vf = ffmpeg_video_filter(key, be, hw)
    cmd = [ff, "-hide_banner", "-loglevel", "error"] + ffmpeg_pre_input(key, device) + src
    if vf:
        cmd += ["-vf", vf]
    cmd += ffmpeg_encoder_args(key, 23, 30, 4000) + ["-frames:v", "15", "-f", "null", "-"]
    return cmd


def _split_gst(chain: str) -> list[str]:
    out = []
    for part in chain.split(" ! "):
        if out:
            out.append("!")
        out.extend(part.split(" "))
    return out


split_gst = _split_gst


def test(key: str, be: str | None = None, device: str = "") -> tuple[bool, str]:
    be = be or backend()
    cmd = _test_cmd(key, be, device)
    if not cmd:
        return False, "ffmpeg not found"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                           creationflags=paths.no_window_flags())
    except FileNotFoundError:
        return False, "program not found"
    except subprocess.TimeoutExpired:
        return False, "timed out"
    err = (r.stderr or "").strip().splitlines()
    return r.returncode == 0, (err[-1] if err else "")


def detect(progress=None) -> str:
    """Return the key of the best working encoder. `progress(label, ok)` is called per try."""
    global VAAPI_DEVICE
    be = backend()
    last_failures.clear()
    for enc in candidates(be):
        if be == "ffmpeg_linux" and enc.key in ("vaapi", "vaapi_sw"):
            # Try each GPU: on hybrid laptops the first node is often the wrong one.
            tries = [(n, f"{enc.label} on {os.path.basename(n)}") for n in render_nodes()] or [("", enc.label)]
        else:
            tries = [("", enc.label)]
        for device, lbl in tries:
            ok, err = test(enc.key, be, device)
            if progress:
                progress(lbl, ok, err)
            if ok:
                if device:
                    VAAPI_DEVICE = device
                return enc.key
            last_failures[lbl] = err
    return candidates(be)[-1].key
