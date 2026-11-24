"""Checks the capture commands for every backend (runs on any OS).

python -m pytest tests  (or just: python tests/test_capture.py)
"""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kalcicam import capture, config, encoders, sysinfo  # noqa: E402


def _settings(**kw):
    s = config.Settings()
    s.detected_encoder = kw.pop("enc", "nvenc")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _with_backend(be):
    return mock.patch.object(encoders, "backend", return_value=be)


def test_x11_fullscreen_nvenc_with_two_audio():
    s = _settings(mic=True, mic_volume=150)
    with _with_backend("ffmpeg_linux"), \
            mock.patch.object(sysinfo, "default_desktop_source", return_value="sink.monitor"), \
            mock.patch.object(sysinfo, "default_mic_source", return_value="mic0"):
        job = capture.ffmpeg_job(s, {"x": 1920, "y": 0, "w": 2560, "h": 1440}, "/tmp/a b/out.mkv")
    c = job.cmd
    assert c[c.index("x11grab") - 1] == "-f"
    assert c.index("pulse") < c.index("x11grab")  # audio opens first
    assert c[c.index("-map") + 1] == "2:v"
    assert ":0+1920,0" in c[c.index("-video_size") + 2] or c[c.index("-video_size") + 3].endswith("+1920,0")
    assert "2560x1440" in c
    assert "h264_nvenc" in c
    fc = c[c.index("-filter_complex") + 1]
    assert "[1:a]volume=1.50" in fc and "amix=inputs=2" in fc
    assert c[-1] == "/tmp/a b/out.mkv"
    print("X11:", " ".join(c))


def test_x11_vaapi_zero_copy():
    s = _settings(enc="vaapi", desktop_audio=False)
    with _with_backend("ffmpeg_linux"):
        c = capture.ffmpeg_job(s, {"x": 0, "y": 0, "w": 1920, "h": 1080}, "o.mkv").cmd
    assert c.index("-vaapi_device") < c.index("-i")
    assert c[c.index("-vf") + 1] == "hwupload,scale_vaapi=format=nv12"
    assert "-an" not in c and "aac" not in c


def test_odd_region_is_made_even():
    s = _settings(desktop_audio=False)
    with _with_backend("ffmpeg_linux"):
        c = capture.ffmpeg_job(s, {"x": 5, "y": 7, "w": 801, "h": 601}, "o.mkv").cmd
    assert "800x600" in c


def test_x11_window_and_replay_segments():
    s = _settings(desktop_audio=False, replay_seconds=30)
    with _with_backend("ffmpeg_linux"):
        job = capture.ffmpeg_job(s, {"window_id": "0x3a00007"}, "", replay_dir="/tmp/rb")
    c = job.cmd
    assert c[c.index("-window_id") + 1] == "0x3a00007"
    assert c[c.index("-segment_wrap") + 1] == str(capture.replay_segments(30))
    assert c[-1].endswith("seg%03d.mkv")


def test_windows_ddagrab_region():
    s = _settings(enc="amf", desktop_audio=False)
    with _with_backend("ffmpeg_win"):
        c = capture.ffmpeg_job(s, {"output_idx": 1, "rx": 10, "ry": 20, "w": 640, "h": 480}, "o.mkv").cmd
    src = c[c.index("lavfi") + 2]
    assert src.startswith("ddagrab=output_idx=1") and "offset_x=10" in src and "video_size=640x480" in src
    assert c[c.index("-vf") + 1] == "hwdownload,format=bgra"
    assert "h264_amf" in c


def test_wayland_gst_pipeline():
    s = _settings(enc="gst_va", mic=True)
    with _with_backend("gst"), \
            mock.patch.object(sysinfo, "default_desktop_source", return_value="sink.monitor"), \
            mock.patch.object(sysinfo, "default_mic_source", return_value="mic0"):
        job = capture.gst_job(s, {"node": 42, "fd": 9, "crop": {"l": 10, "t": 20, "r": 30, "b": 40}},
                              "/home/me/My Videos/x.mkv")
    c = job.cmd
    assert c[:3] == ["gst-launch-1.0", "-e", "-q"]
    assert "fd=9" in c and "path=42" in c
    assert "location=/home/me/My Videos/x.mkv" in c  # one argv item, spaces kept
    assert "vah264enc" in c and "audiomixer" in c and "left=10" in c
    assert job.stop == "sigint" and job.pass_fds == (9,)
    print("Wayland:", " ".join(c))


def test_wayland_replay():
    s = _settings(enc="gst_x264", desktop_audio=True)
    with _with_backend("gst"), mock.patch.object(sysinfo, "default_desktop_source", return_value="m"):
        c = capture.gst_job(s, {"node": 1, "fd": 3}, "", replay_dir="/tmp/rb").cmd
    assert "splitmuxsink" in c and "mux.video" in c and "mux.audio_0" in c


def test_hotkey_conversion():
    from kalcicam.widgets import pynput_to_qt, qt_to_pynput
    from kalcicam.hotkeys import _gnome_accel
    assert qt_to_pynput("Shift+F12") == "<shift>+<f12>"
    assert pynput_to_qt("<shift>+<f12>") == "Shift+F12"
    assert qt_to_pynput("Ctrl+Alt+R") == "<ctrl>+<alt>+r"
    assert _gnome_accel("<shift>+<f12>") == "<Shift>F12"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
