#!/usr/bin/env python3
"""Generate Redunator's distinct UI + alert sounds as 22050 Hz mono
16-bit WAVs (same format as the existing tap.wav / alert.wav).

Every sound is a short sequence of sine notes with an exponential
decay envelope — no samples, no downloads, fully reproducible. The
recipes live here so a sound can be re-tuned later by editing one
line and re-running: python3 tools/generate_sounds.py

Alert sounds (also copied to android/app/src/main/res/raw/ for the
notification channels — one channel per alert kind):
  proximity_alert  urgent alternating high/low, foreground + background
  spike_alert      rising blips, when the morning digest contains a spike
  area_alert       low two-note chime, a HIGH camera inside a watched area
  digest_morning   gentle arpeggio, the daily 07:00 digest
  digest_weekly    soft two-note, the Sunday weekly digest
UI sounds (assets/ only, played through SoundService):
  pin    bright tick, tapping a map marker
  save   confirm ding, saving / unsaving / reporting a spike
  query  soft tick, running a risk query
  route  rising two-note, computing or picking a route
  watch  soft chime, long-press to watch an area
tap.wav (existing) stays the generic button sound.
"""
import math
import struct
import wave
from pathlib import Path

SR = 22050
OUT = Path(__file__).resolve().parent.parent / "assets"


def tone(freq: float, seconds: float, volume: float = 0.5,
         harmonic: float = 0.0) -> bytes:
    """One sine note with a fast attack and exponential decay."""
    n = int(SR * seconds)
    frames = bytearray()
    for i in range(n):
        t = i / SR
        env = min(1.0, t / 0.008) * math.exp(-3.0 * t / seconds)
        s = math.sin(2 * math.pi * freq * t)
        if harmonic:
            s += harmonic * math.sin(2 * math.pi * freq * 2 * t)
        v = int(max(-1.0, min(1.0, volume * env * s)) * 32767)
        frames += struct.pack("<h", v)
    return bytes(frames)


def seq(notes, gap: float = 0.02) -> bytes:
    """Concatenate notes with a short silence between them."""
    out = bytearray()
    silence = bytes(int(SR * gap) * 2)
    for i, (freq, seconds, volume, harmonic) in enumerate(notes):
        if i:
            out += silence
        out += tone(freq, seconds, volume, harmonic)
    return bytes(out)


# (freq, seconds, volume, harmonic)
RECIPES = {
    # Urgent: high/low alternating, three pairs.
    "proximity_alert": [
        (880, 0.12, 0.55, 0.0), (659, 0.12, 0.55, 0.0),
        (880, 0.12, 0.55, 0.0), (659, 0.12, 0.55, 0.0),
        (880, 0.20, 0.55, 0.0),
    ],
    # Rising blips, ends on a held high note.
    "spike_alert": [
        (523, 0.09, 0.5, 0.0), (659, 0.09, 0.5, 0.0),
        (784, 0.09, 0.5, 0.0), (1047, 0.22, 0.5, 0.0),
    ],
    # Low two-note chime with a soft octave harmonic.
    "area_alert": [
        (392, 0.28, 0.5, 0.25), (330, 0.32, 0.5, 0.25),
    ],
    # Gentle morning arpeggio, softer volumes.
    "digest_morning": [
        (523, 0.18, 0.38, 0.0), (659, 0.18, 0.38, 0.0),
        (784, 0.18, 0.38, 0.0), (1047, 0.30, 0.38, 0.0),
    ],
    # Soft two-note, lower and slower than the daily digest.
    "digest_weekly": [
        (659, 0.30, 0.38, 0.0), (523, 0.38, 0.38, 0.0),
    ],
    # Bright tick.
    "pin": [(1250, 0.08, 0.5, 0.0)],
    # Confirm ding: two rising notes.
    "save": [(880, 0.12, 0.5, 0.0), (1318, 0.20, 0.5, 0.0)],
    # Soft tick, lower than the pin.
    "query": [(700, 0.12, 0.45, 0.0)],
    # Rising two-note.
    "route": [(660, 0.15, 0.5, 0.0), (990, 0.20, 0.5, 0.0)],
    # Soft chime with an octave harmonic.
    "watch": [(587, 0.18, 0.45, 0.25), (880, 0.25, 0.45, 0.25)],
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, notes in RECIPES.items():
        data = seq(notes)
        path = OUT / f"{name}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(data)
        print(f"{name}.wav: {len(data) // 2} samples, "
              f"{len(data) / 2 / SR:.2f}s")


if __name__ == "__main__":
    main()
