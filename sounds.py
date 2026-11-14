## PROCEDURAL SOUND EFFECTS
##
## Every sound is synthesised with numpy at startup and cached, so the game
## ships without audio files. If numpy or the audio device is unavailable
## the game runs silently instead of crashing.

import math
import random

import pygame

try:
    import numpy as np
except ImportError:
    np = None

SAMPLE_RATE = 44100


def _tone(freq, ms, volume=0.5, decay="exp", shape="sine"):
    n = int(SAMPLE_RATE * ms / 1000)
    t = np.arange(n) / SAMPLE_RATE
    wave = np.zeros(n)
    for f, amp in freq:
        if shape == "sine":
            wave += amp * np.sin(2 * math.pi * f * t)
        elif shape == "tri":
            wave += amp * (2 / math.pi) * np.arcsin(np.sin(2 * math.pi * f * t))
        else:  # square-ish
            wave += amp * np.sign(np.sin(2 * math.pi * f * t))
    if decay == "exp":
        wave *= np.exp(-5.0 * t / (ms / 1000))
    elif decay == "linear":
        wave *= np.linspace(1, 0, n)
    return (wave * volume * 32767).astype(np.int16)


def _mix(*layers):
    n = max(len(x) for x in layers)
    out = np.zeros(n)
    for x in layers:
        out[: len(x)] += x
    peak = np.max(np.abs(out)) or 1
    if peak > 32767:
        out = out * (32767 / peak)
    return out.astype(np.int16)


def _noise_burst(ms, volume=0.4, lowpass=0.3):
    n = int(SAMPLE_RATE * ms / 1000)
    noise = np.random.uniform(-1, 1, n)
    ## Cheap one-pole low-pass for a thuddy attack
    out = np.empty(n)
    acc = 0.0
    for i in range(n):
        acc += lowpass * (noise[i] - acc)
        out[i] = acc
    out *= np.exp(-9.0 * np.arange(n) / SAMPLE_RATE / (ms / 1000))
    return (out * volume * 32767).astype(np.int16)


def build_sounds():
    """Returns {name: pygame.mixer.Sound} or {} when audio is unavailable."""
    if np is None:
        return {}
    try:
        pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=1, buffer=512)
    except pygame.error:
        return {}

    ## The driver may override the channel count (e.g. stereo dummy device),
    ## so shape arrays to whatever the mixer actually opened.
    channels = (pygame.mixer.get_init() or (0, 0, 1))[2] or 1

    def make(arr):
        if channels == 2:
            arr = np.column_stack([arr, arr])
        return pygame.sndarray.make_sound(arr)

    sounds = {}
    rng = random.Random(7)

    ## Wooden piece placement: short noise thump + soft body tone
    thock = _noise_burst(50, volume=0.55, lowpass=0.18)
    body = _tone([(180 + rng.randint(-15, 15), 1.0)], 60, volume=0.35, decay="exp")
    sounds["move"] = make(_mix(thock, body))

    ## Capture: harder crack plus a lower impact
    crack = _noise_burst(80, volume=0.8, lowpass=0.45)
    thud = _tone([(110, 1.2), (85, 0.6)], 90, volume=0.45, decay="exp")
    sounds["capture"] = make(_mix(crack, thud))

    ## Check: quick two-note alert
    sounds["check"] = make(
        _mix(_tone([(880, 1.0)], 70, volume=0.30), _tone([(660, 1.0)], 110, volume=0.25))
    )

    ## Castling: two quick placements
    sounds["castle"] = make(
        _mix(_noise_burst(40, 0.5, 0.18),
             _tone([(200, 1.0)], 50, 0.3),
             np.concatenate([np.zeros(int(SAMPLE_RATE * 0.07)), _noise_burst(40, 0.4, 0.18)]))
    )

    ## Game end: little arpeggio up (win) / down (loss)
    def arpeggio(freqs, step_ms, ms, volume=0.32):
        layers = []
        for i, f in enumerate(freqs):
            offset = np.zeros(int(SAMPLE_RATE * step_ms / 1000 * i))
            layers.append(np.concatenate([offset, _tone([(f, 1.0)], ms, volume)]))
        return _mix(*layers)

    sounds["win"] = make(arpeggio((523, 659, 784, 1047), 90, 350))
    sounds["lose"] = make(arpeggio((784, 622, 523, 415), 110, 380, 0.28))
    sounds["draw"] = make(arpeggio((440, 440, 392), 130, 300, 0.25))

    ## UI clicks
    sounds["click"] = make(_tone([(700, 0.8), (1400, 0.3)], 35, 0.22))
    sounds["select"] = make(_tone([(950, 0.8)], 30, 0.16))
    sounds["illegal"] = make(_tone([(160, 1.0)], 90, 0.30, shape="square"))

    return sounds
