#!/usr/bin/env python3
"""生成 demo.mp4 的背景音乐（原创、可重跑）。

没有外部素材：全部是加性合成的铺底 + 稀疏的「翻页」音，混一点卷积混响。
音色取向是书房/纸张：慢起音、长释放、低亮度、不做节拍。

    uv run --with numpy python tools/make_music.py            # → tools/.build/music.wav
    ffmpeg -i demo.mp4 -i tools/.build/music.wav -map 0:v -map 1:a \
           -c:v copy -c:a aac -b:a 192k \
           -af "loudnorm=I=-24:TP=-2:LRA=7" -shortest demo.out.mp4

时长、调性、力度曲线都由下面的常量决定，改这里就能换气质。
"""

from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

SR = 48_000
DUR = 89.2  # 与 demo.mp4 等长，后面统一裁/补
OUT = Path(__file__).resolve().parent / ".build" / "music.wav"

# D 大调，四小节一循环；不追求进行感，只求不抢画面
CHORDS = [
    [38, 45, 54, 61, 64],  # D  · D2 A2 F#3 C#4 E4
    [47, 54, 59, 62, 66],  # Bm · B2 F#3 B3 D4 F#4
    [43, 50, 57, 59, 64],  # G  · G2 D3 A3 B3 E4
    [45, 52, 55, 62, 64],  # A  · A2 E3 G3 D4 E4
]
BELLS = [74, 78, 81, 83, 88]  # D5 F#5 A5 B5 E6，五声音阶里的几个落点


def midi_hz(n: float) -> float:
    return 440.0 * 2.0 ** ((n - 69.0) / 12.0)


def env_pad(n: int, attack: float, release: float, sustain: float) -> np.ndarray:
    a = max(1, int(attack * SR))
    r = max(1, int(release * SR))
    e = np.ones(n, dtype=np.float32) * sustain
    if a < n:
        e[:a] = sustain * np.linspace(0.0, 1.0, a, dtype=np.float32) ** 1.6
    if r < n:
        e[-r:] *= np.linspace(1.0, 0.0, r, dtype=np.float32) ** 1.4
    return e


def add_pad(buf: np.ndarray, midi: float, t0: float, dur: float, amp: float, pan: float, spread: float = 0.0) -> None:
    start = int(t0 * SR)
    n = min(int(dur * SR), buf.shape[1] - start)
    if n <= 0:
        return
    t = np.arange(n, dtype=np.float32) / SR
    # 加性合成：基频 + 三个泛音，轻微失谐避免太“电子”
    partials = ((1.0, 1.00, 0.0), (2.0, 0.26, 0.4), (3.0, 0.11, -0.3), (4.0, 0.05, 0.6))
    sig = np.zeros(n, dtype=np.float32)
    for mult, weight, detune_cents in partials:
        f = midi_hz(midi) * mult * 2.0 ** ((detune_cents + spread) / 1200.0)
        if f > SR / 2.2:
            continue
        sig += weight * np.sin(2 * np.pi * f * t + mult * 0.7, dtype=np.float32)
    # 极慢的呼吸，让铺底不是一条直线
    sig *= 1.0 + 0.06 * np.sin(2 * np.pi * 0.23 * t + midi * 0.11, dtype=np.float32)
    sig *= env_pad(n, attack=2.2, release=3.4, sustain=0.58) * amp
    gain_l = math.cos((pan + 1.0) * math.pi / 4.0)
    gain_r = math.sin((pan + 1.0) * math.pi / 4.0)
    buf[0, start : start + n] += sig * gain_l
    buf[1, start : start + n] += sig * gain_r


def add_bell(buf: np.ndarray, midi: float, t0: float, amp: float, pan: float) -> None:
    start = int(t0 * SR)
    n = min(int(4.2 * SR), buf.shape[1] - start)
    if n <= 0:
        return
    t = np.arange(n, dtype=np.float32) / SR
    f = midi_hz(midi)
    # 敲击音：两个近频率相加产生很慢的拍，指数衰减
    sig = (
        np.sin(2 * np.pi * f * t, dtype=np.float32)
        + 0.45 * np.sin(2 * np.pi * f * 2.004 * t, dtype=np.float32)
        + 0.18 * np.sin(2 * np.pi * f * 3.01 * t, dtype=np.float32)
    )
    sig *= np.exp(-t / 0.85, dtype=np.float32) * (1.0 - np.exp(-t / 0.012))
    sig *= amp
    gain_l = math.cos((pan + 1.0) * math.pi / 4.0)
    gain_r = math.sin((pan + 1.0) * math.pi / 4.0)
    buf[0, start : start + n] += sig * gain_l
    buf[1, start : start + n] += sig * gain_r


def make_ir(seconds: float = 2.4, decay: float = 0.05) -> np.ndarray:
    n = int(seconds * SR)
    rng = np.random.default_rng(7)  # 固定种子：每次生成同一份文件
    noise = rng.standard_normal(n).astype(np.float32)
    t = np.arange(n, dtype=np.float32) / SR
    ir = noise * np.exp(-t / decay, dtype=np.float32)
    # 一阶低通，削掉高频的“嘶”
    k = math.exp(-2 * math.pi * 5200 / SR)
    out = np.empty_like(ir)
    acc = 0.0
    for i in range(n):
        acc = k * acc + (1 - k) * ir[i]
        out[i] = acc
    out[: int(0.02 * SR)] = 0.0  # 预延迟
    return out / np.max(np.abs(out))


def convolve(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    n = 1 << math.ceil(math.log2(len(x) + len(h) + 1))
    out = np.fft.irfft(np.fft.rfft(x, n) * np.fft.rfft(h, n), n)[: len(x)]
    return out.astype(np.float32)


def main() -> None:
    total = int(DUR * SR)
    dry = np.zeros((2, total), dtype=np.float32)

    # 铺底：每 4 秒一个和弦，循环铺满
    t = 0.0
    cycle = 0
    while t < DUR - 2.0:
        chord = CHORDS[cycle % len(CHORDS)]
        # 力度：开头收着，中段最满，结尾再收回来
        level = 0.55 if t < 16 else 0.72 if t < 64 else 0.6
        for i, midi in enumerate(chord):
            pan = (-0.32, 0.28, -0.16, 0.34, 0.06)[i % 5]
            amp = (0.105 if i == 0 else 0.072) * level
            # spread 让同一和弦在每一轮里微微走音，避免八十八秒听成一条直线
            spread = (-2.4, 1.8, -1.2, 2.6, 0.0)[cycle % 5]
            add_pad(dry, midi, t, 9.2, amp, pan, spread)
        # 第三、四轮在高处加一层极轻的八度，让中段有“打开”的感觉
        if 3 <= cycle % 8 <= 4:
            add_pad(dry, chord[0] + 24, t + 0.6, 6.0, 0.028 * level, 0.42)
        t += 4.0
        cycle += 1

    # 翻页音：每 8 秒一下，走五声音阶
    for i, t0 in enumerate(np.arange(12.0, DUR - 6.0, 8.0)):
        add_bell(dry, BELLS[i % len(BELLS)], float(t0), 0.052, 0.55 if i % 2 else -0.5)

    # 混响：一点湿声就够，别糊住画面上的字
    ir = make_ir()
    wet = np.stack([convolve(dry[0], ir), convolve(dry[1], ir)])
    mix = dry * 0.78 + wet * 0.30

    # 母带：一抹低通 + 首尾淡入淡出
    k = math.exp(-2 * math.pi * 7200 / SR)
    for ch in range(2):
        acc = 0.0
        for i in range(mix.shape[1]):
            acc = k * acc + (1 - k) * mix[ch, i]
            mix[ch, i] = acc
    mix -= mix.mean(axis=1, keepdims=True)
    fade_in = int(2.6 * SR)
    fade_out = int(5.0 * SR)
    mix[:, :fade_in] *= np.linspace(0, 1, fade_in, dtype=np.float32) ** 1.5
    mix[:, -fade_out:] *= np.linspace(1, 0, fade_out, dtype=np.float32) ** 1.2

    peak = float(np.max(np.abs(mix)))
    mix = np.tanh(mix / peak * 1.15) * 0.72  # 软限幅，留 3 dB 余量给编码
    pcm = (np.clip(mix, -1.0, 1.0).T * 32767).astype("<i2")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(f"{OUT}  {DUR:.1f}s  {OUT.stat().st_size / 1e6:.1f} MB  peak {peak:.3f}")


if __name__ == "__main__":
    main()
