"""Build the capture command for the current system.

The UI computes a `target` dict from Qt (which screen / region / window),
and these pure functions turn settings + target into a command line. Keeping
them pure makes them easy to test on any OS.

target keys:
  X11:      x, y, w, h (absolute physical px), follow  or  window_id
  Windows:  output_idx, and optionally rx, ry, w, h  or  title
  Wayland:  node, fd, and optionally crop {l, t, r, b}
"""

import math
import os
from dataclasses import dataclass, field

from . import encoders, paths, sysinfo
from .config import QUALITY_QP, Settings

SEGMENT_SECONDS = 2  # replay buffer chunk length


@dataclass
class Job:
    cmd: list
    output: str
    stop: str = "q"  # "q" = send q on stdin (ffmpeg), "sigint" = Ctrl+C (gst-launch -e)
    pass_fds: tuple = ()
    env: dict = field(default_factory=dict)


def _even(n: int) -> int:
    """H.264 needs even width/height."""
    n = int(n)
    return n - (n % 2)


def _encoder(s: Settings) -> str:
    if s.encoder and s.encoder != "auto":
        return s.encoder
    return s.detected_encoder or encoders.candidates()[-1].key


def _audio_sources(s: Settings) -> list[tuple[str, int]]:
    """[(device, volume%)], desktop first."""
    out = []
    be = encoders.backend()
    if s.desktop_audio:
        if be == "ffmpeg_win":
            if s.desktop_device:  # needs "Stereo Mix" or similar on Windows
                out.append((s.desktop_device, s.desktop_volume))
        else:
            out.append((s.desktop_device or sysinfo.default_desktop_source(), s.desktop_volume))
    if s.mic:
        if be == "ffmpeg_win":
            mic = s.mic_device
            if not mic:
                devs = sysinfo.audio_devices()[1]
                mic = devs[1].id if len(devs) > 1 else ""
            if mic:
                out.append((mic, s.mic_volume))
        else:
            out.append((s.mic_device or sysinfo.default_mic_source(), s.mic_volume))
    return out


def _pixels(target: dict) -> int:
    """How many pixels we're capturing (for picking a bitrate)."""
    if target.get("w") and target.get("h"):
        return int(target["w"]) * int(target["h"])
    if target.get("region"):
        return int(target["region"]["w"]) * int(target["region"]["h"])
    return int(target.get("pixels") or 1920 * 1080)


def replay_segments(seconds: int) -> int:
    return math.ceil(seconds / SEGMENT_SECONDS) + 2


# ---------------- ffmpeg (X11 + Windows) ----------------

def ffmpeg_job(s: Settings, target: dict, output: str, replay_dir: str | None = None) -> Job:
    be = encoders.backend()
    enc = _encoder(s)
    ff = paths.ffmpeg() or "ffmpeg"
    fps = int(s.fps)
    qp = QUALITY_QP.get(s.quality, 23)
    mouse = "1" if s.draw_mouse else "0"

    cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
           "-stats_period", "0.5", "-y"]
    cmd += encoders.ffmpeg_pre_input(enc, s.vaapi_device)

    # Audio inputs go first: opening a sound device can take a moment, and if the
    # screen input is already running it falls behind and drops most frames.
    audio = _audio_sources(s)
    for dev, _vol in audio:
        if be == "ffmpeg_win":
            cmd += ["-thread_queue_size", "1024", "-f", "dshow", "-i", f"audio={dev}"]
        else:
            cmd += ["-thread_queue_size", "1024", "-f", "pulse", "-i", dev]
    v_in = len(audio)

    hw_frames = False
    if be == "ffmpeg_linux":
        display = os.environ.get("DISPLAY", ":0")
        cmd += ["-thread_queue_size", "64", "-f", "x11grab", "-framerate", str(fps), "-draw_mouse", mouse]
        if target.get("window_id"):
            cmd += ["-window_id", target["window_id"], "-i", display]
        else:
            if target.get("follow"):
                # "Around mouse": x11grab moves the capture box with the cursor itself.
                cmd += ["-follow_mouse", "centered"]
            cmd += ["-video_size", f"{_even(target['w'])}x{_even(target['h'])}",
                    "-i", f"{display}+{int(target['x'])},{int(target['y'])}"]
    else:
        if target.get("title") or s.win_capture_engine == "gdigrab":
            # gdigrab: CPU capture, but can grab a single window by title.
            cmd += ["-thread_queue_size", "64", "-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", mouse]
            if target.get("title"):
                cmd += ["-i", f"title={target['title']}"]
            else:
                if target.get("w"):
                    cmd += ["-offset_x", str(int(target.get("ax", 0))), "-offset_y", str(int(target.get("ay", 0))),
                            "-video_size", f"{_even(target['w'])}x{_even(target['h'])}"]
                cmd += ["-i", "desktop"]
        else:
            # ddagrab: Desktop Duplication, frames stay on the GPU.
            opts = [f"output_idx={int(target.get('output_idx', 0))}", f"framerate={fps}", f"draw_mouse={mouse}"]
            if target.get("w"):
                opts += [f"video_size={_even(target['w'])}x{_even(target['h'])}",
                         f"offset_x={int(target.get('rx', 0))}", f"offset_y={int(target.get('ry', 0))}"]
            cmd += ["-f", "lavfi", "-i", "ddagrab=" + ":".join(opts)]
            hw_frames = True

    cmd += ["-map", f"{v_in}:v"]
    vf = encoders.ffmpeg_video_filter(enc, be, hw_frames)
    if vf:
        cmd += ["-vf", vf]

    if len(audio) == 1:
        vol = audio[0][1] / 100
        if vol != 1:
            cmd += ["-filter_complex", f"[0:a]volume={vol:.2f}[aout]", "-map", "[aout]"]
        else:
            cmd += ["-map", "0:a"]
    elif len(audio) >= 2:
        parts, labels = [], ""
        for i, (_dev, v) in enumerate(audio):
            parts.append(f"[{i}:a]volume={v / 100:.2f}[a{i}]")
            labels += f"[a{i}]"
        parts.append(f"{labels}amix=inputs={len(audio)}:duration=longest:normalize=0[aout]")
        cmd += ["-filter_complex", ";".join(parts), "-map", "[aout]"]

    cmd += encoders.ffmpeg_encoder_args(enc, qp, fps, encoders.bitrate_kbps(s.quality, fps, _pixels(target)))
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "160k"]

    if replay_dir:
        n = replay_segments(s.replay_seconds)
        cmd += ["-f", "segment", "-segment_time", str(SEGMENT_SECONDS), "-segment_wrap", str(n),
                "-reset_timestamps", "1", "-segment_format", "matroska",
                os.path.join(replay_dir, "seg%03d.mkv")]
        return Job(cmd, replay_dir)
    cmd += [output]
    return Job(cmd, output)


# ---------------- GStreamer (Wayland) ----------------

def gst_job(s: Settings, target: dict, output: str, replay_dir: str | None = None) -> Job:
    enc = _encoder(s)
    fps = int(s.fps)
    chain = encoders.gst_encoder_chain(enc, s.quality, fps, _pixels(target))
    src = (f"pipewiresrc fd={int(target['fd'])} path={int(target['node'])} do-timestamp=true "
           f"keepalive-time=1000 always-copy=false ! videorate ! video/x-raw,framerate={fps}/1")
    crop = target.get("crop")
    if crop:
        src += (f" ! videocrop left={int(crop['l'])} top={int(crop['t'])} "
                f"right={int(crop['r'])} bottom={int(crop['b'])}")

    if replay_dir:
        n = replay_segments(s.replay_seconds)
        loc = os.path.join(replay_dir, "seg%05d.mkv")
        sink = (f"splitmuxsink name=mux location=@LOC@ max-size-time={SEGMENT_SECONDS * 1_000_000_000} "
                f"max-files={n} muxer-factory=matroskamux")
        vpad, apad = "mux.video", "mux.audio_0"
    else:
        sink = "matroskamux name=mux ! filesink location=@LOC@"; loc = output
        vpad, apad = "mux.video_0", "mux.audio_0"

    pipeline = (f"{src} ! queue max-size-buffers=4 leaky=downstream ! {chain} ! queue ! {vpad}  {sink}")

    audio = _audio_sources(s)
    if audio:
        if len(audio) == 1:
            dev, v = audio[0]
            pipeline += (f"  pulsesrc device={dev} ! audioconvert ! audioresample ! volume volume={v / 100:.2f} ! "
                         f"audioconvert ! opusenc bitrate=160000 ! queue ! {apad}")
        else:
            pipeline += "  audiomixer name=amix ! audioconvert ! opusenc bitrate=160000 ! queue ! " + apad
            for dev, v in audio:
                pipeline += (f"  pulsesrc device={dev} ! audioconvert ! audioresample ! "
                             f"volume volume={v / 100:.2f} ! queue ! amix.")

    # gst-launch escapes spaces inside each argv item, so the file path goes in as
    # one item (device names from pactl never contain spaces).
    tokens = [f"location={loc}" if t == "location=@LOC@" else t for t in pipeline.split()]
    cmd = ["gst-launch-1.0", "-e", "-q"] + tokens
    return Job(cmd, replay_dir or output, stop="sigint", pass_fds=(int(target["fd"]),))


# ---------------- audio only ----------------

AUDIO_FORMATS = {
    # key: (final extension, codec args)
    "m4a": (".m4a", ["-c:a", "aac", "-b:a", "192k"]),
    "mp3": (".mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]),
}


def audio_job(s: Settings, output: str) -> Job:
    """Sound only (desktop and/or mic) into a crash-safe .mka piece. Uses ffmpeg with
    PulseAudio/PipeWire on Linux (X11 and Wayland alike) and DirectShow on Windows."""
    be = encoders.backend()
    ff = paths.ffmpeg() or "ffmpeg"
    audio = _audio_sources(s)
    if not audio:
        raise ValueError("Turn on desktop sound or the microphone to record audio only.")
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
           "-stats_period", "0.5", "-y"]
    for dev, _vol in audio:
        if be == "ffmpeg_win":
            cmd += ["-thread_queue_size", "1024", "-f", "dshow", "-i", f"audio={dev}"]
        else:
            cmd += ["-thread_queue_size", "1024", "-f", "pulse", "-i", dev]
    parts, labels = [], ""
    for i, (_dev, v) in enumerate(audio):
        parts.append(f"[{i}:a]volume={v / 100:.2f}[a{i}]")
        labels += f"[a{i}]"
    if len(audio) > 1:
        parts.append(f"{labels}amix=inputs={len(audio)}:duration=longest:normalize=0[aout]")
    else:
        parts[0] = parts[0].replace("[a0]", "[aout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[aout]", "-vn"]
    cmd += AUDIO_FORMATS.get(s.audio_format, AUDIO_FORMATS["m4a"])[1]
    cmd += ["-f", "matroska", output]
    return Job(cmd, output)


def build_job(s: Settings, target: dict, output: str, replay_dir: str | None = None) -> Job:
    if s.mode == "audio" and not replay_dir:
        return audio_job(s, output)
    if encoders.backend() == "gst":
        return gst_job(s, target, output, replay_dir)
    return ffmpeg_job(s, target, output, replay_dir)


# ---------------- post-processing ----------------

def concat_cmd(list_file: str, output: str) -> list:
    """Join pieces (pause/resume, replay segments) without re-encoding."""
    ff = paths.ffmpeg() or "ffmpeg"
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
           "-c", "copy"]
    return cmd + _faststart(output) + [output]


def _faststart(output: str) -> list:
    """MP4/M4A: put the index at the front so players start instantly."""
    return ["-movflags", "+faststart"] if output.lower().endswith((".mp4", ".m4a", ".mov")) else []


def remux_cmd(src: str, output: str) -> list:
    ff = paths.ffmpeg() or "ffmpeg"
    return [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-map", "0", "-c", "copy"] + \
        _faststart(output) + [output]


def thumbnail_cmd(video: str, png: str) -> list:
    ff = paths.ffmpeg() or "ffmpeg"
    return [ff, "-hide_banner", "-loglevel", "error", "-y", "-ss", "1", "-i", video, "-frames:v", "1",
            "-vf", "scale=240:-2", png]
