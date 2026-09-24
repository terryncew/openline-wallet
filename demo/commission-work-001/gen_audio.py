#!/usr/bin/env python3
"""Original synthesized sound for the commission-work demo: quiet pulse bed,
soft arrival ticks at scene changes, confirmation chime at ACCEPTED/SETTLE,
dull thud at refusals. numpy only. Mono, peaks at -3 dBFS."""
import numpy as np

SR = 44100
DUR = 44.0
n = int(SR * DUR)
t = np.arange(n) / SR
mix = np.zeros(n)

def add(sig, at):
    i = int(at * SR)
    j = min(n, i + len(sig))
    mix[i:j] += sig[:j - i]

# quiet pulse bed: 55 Hz sine, slow amplitude LFO, very low
bed = 0.05 * np.sin(2 * np.pi * 55 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.12 * t))
mix += bed

def tick(at, freq=880.0, dur=0.25, vol=0.18):
    m = int(dur * SR)
    env = np.exp(-np.arange(m) / (SR * 0.06))
    add(vol * np.sin(2 * np.pi * freq * np.arange(m) / SR) * env, at)

def chime(at):
    for f, v in [(660.0, 0.16), (990.0, 0.12), (1320.0, 0.08)]:
        m = int(0.6 * SR)
        env = np.exp(-np.arange(m) / (SR * 0.18))
        add(v * np.sin(2 * np.pi * f * np.arange(m) / SR) * env, at)

def thud(at):
    m = int(0.35 * SR)
    env = np.exp(-np.arange(m) / (SR * 0.07))
    add(0.22 * np.sin(2 * np.pi * 110 * np.arange(m) / SR) * env, at)

# scene boundaries: 0, 4, 8, 13.5, 17.5, 23, 28, 32, 36, 39.5
for b in [4.0, 8.0, 13.5, 17.5, 28.0, 32.0, 36.0, 39.5]:
    tick(b)
chime(17.8)    # VERIFY accepted
chime(23.2)    # SETTLED
thud(25.5)     # ALREADY_SETTLED refusal
thud(28.5)     # REJECTED
thud(33.0)     # boundary refusals
thud(37.0)     # MANDATE_REVOKED
tick(39.5, freq=1320.0, dur=0.4, vol=0.12)  # end card resolution

peak = np.max(np.abs(mix))
mix = mix / peak * 0.707  # -3 dBFS
import wave
w = wave.open("/tmp/cw-audio.wav", "wb")
w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
w.writeframes((mix * 32767).astype(np.int16).tobytes())
w.close()
print("wrote /tmp/cw-audio.wav")
