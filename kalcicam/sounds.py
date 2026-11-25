"""Tiny start / stop / tick sounds, generated in code (no sound files to ship).

Start plays before capture begins and stop plays after it ends, so neither
ends up in the recording.
"""

import math
import struct
import wave

from . import paths

_effects = {}

TONES = {
    # name: [(frequency Hz, milliseconds), ...]
    "tick": [(880, 45)],
    "start": [(660, 70), (990, 110)],
    "stop": [(990, 70), (560, 120)],
    "shot": [(1400, 30), (900, 40)],
}


def _make_wav(name: str) -> str:
    path = paths.cache_dir() / f"sound_{name}.wav"
    if path.exists():
        return str(path)
    rate = 44100
    frames = bytearray()
    for freq, ms in TONES[name]:
        n = int(rate * ms / 1000)
        for i in range(n):
            # soft attack/decay so it's a click, not a beep
            env = min(1.0, i / (rate * 0.004)) * min(1.0, (n - i) / (rate * 0.02))
            v = 0.32 * env * math.sin(2 * math.pi * freq * i / rate)
            frames += struct.pack("<h", int(v * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return str(path)


def play(name: str):
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect

        fx = _effects.get(name)
        if fx is None:
            fx = QSoundEffect()
            fx.setSource(QUrl.fromLocalFile(_make_wav(name)))
            fx.setVolume(0.6)
            _effects[name] = fx
        fx.play()
    except Exception:
        pass  # sound is a nicety; never let it break recording
