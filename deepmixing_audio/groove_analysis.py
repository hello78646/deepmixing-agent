"""Evidence-limited groove-pocket diagnostics for real drum stems.

This module measures onset envelopes and periodicity. It does not identify a
genre, transcribe kick/snare names, or decide what feels good.
"""
import math
from pathlib import Path

import numpy as np

from .analysis import INPUT_OPTIONS
from .runtime import run_ffmpeg

RATE = 22050
MAX_SECONDS = 24
MIN_RELATIVE_FLUX = .005
SPARSE_FLUX_PERCENTILE = 99
MIN_CENTERED_CORRELATION = .2


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _onset_envelope(samples, *, return_metadata=False):
    def finish(values, sparse=False):
        return (values, sparse) if return_metadata else values

    if len(samples) < RATE:
        return finish(np.array([], dtype=float))
    hop = 256
    frame = 1024
    count = 1 + max(0, (len(samples) - frame) // hop)
    frames = np.lib.stride_tricks.sliding_window_view(samples, frame)[::hop][:count]
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(frame), axis=1))
    flux = np.maximum(np.diff(spectrum, axis=0), 0).sum(axis=1)
    if not len(flux):
        return finish(np.array([], dtype=float))
    # Do not magnify tiny FFT leakage from stationary tones into full-scale
    # attacks. The ratio is independent of a common source gain. Sparse,
    # isolated attacks can leave the 95th percentile at zero, so use a more
    # conservative upper percentile only when the normal evidence floor is
    # not met. This changes sensitivity for sparse attacks without changing
    # the periodicity, BPM, or later evidence checks.
    spectral_level = float(np.mean(spectrum.sum(axis=1)))
    evidence_floor = MIN_RELATIVE_FLUX * max(spectral_level, 1e-12)
    scale_percentile = 95
    sparse = False
    if np.percentile(flux, scale_percentile) < evidence_floor:
        scale_percentile = SPARSE_FLUX_PERCENTILE
        sparse = True
        if np.percentile(flux, scale_percentile) < evidence_floor:
            return finish(np.zeros_like(flux), sparse)
    flux = np.maximum(flux - np.percentile(flux, 45), 0)
    scale = np.percentile(flux, scale_percentile)
    return finish(flux / scale if scale > 1e-10 else np.zeros_like(flux), sparse)


def _periodicity(onset, frame_rate, bpm_hint=None):
    if len(onset) < 16 or np.max(onset) < .05:
        return None
    low_bpm, high_bpm = (max(40, bpm_hint*.65), min(220, bpm_hint*1.35)) if _finite(bpm_hint) and bpm_hint > 0 else (60, 180)
    low = max(1, round(frame_rate * 60 / high_bpm))
    high = min(len(onset)//2, round(frame_rate * 60 / low_bpm))
    centered = onset - np.mean(onset)
    if high <= low or float(np.dot(centered, centered)) <= 1e-12:
        return None
    # Positive-only flux has a DC component even for random noise. Compare
    # centered, overlapping vectors at each lag instead of their raw energy.
    correlations = []
    for lag in range(low, high+1):
        a, b = centered[:-lag], centered[lag:]
        correlations.append(float(np.dot(a, b)/max(np.linalg.norm(a)*np.linalg.norm(b), 1e-12)))
    # Keep the existing pulse-layer candidate. Maximizing a different
    # correlation can prefer a half-speed accent cycle; this check validates
    # evidence at the original candidate rather than choosing a new tempo.
    raw = np.correlate(onset, onset, mode='full')[len(onset)-1:]
    lag = low + int(np.argmax(raw[low:high+1]))
    support = correlations[lag-low]
    # Short overlapping FFT windows yield many correlated opportunities for a
    # random peak. Raise the evidence floor as overlap shrinks. This is a
    # conservative heuristic, not a statistical confidence or accuracy score.
    minimum_support = max(MIN_CENTERED_CORRELATION, math.sqrt(32/(len(onset)-lag)))
    if support < minimum_support:
        return None
    interval = lag / frame_rate
    bpm = 60 / interval
    return dict(bpm=round(float(bpm), 3), interval_seconds=round(float(interval), 6),
                periodicity_support=round(support, 4), lag_frames=lag,
                minimum_periodicity_support=round(minimum_support, 4))


def analyze_samples(samples, *, bpm_hint=None):
    samples = np.asarray(samples, dtype=float)
    if samples.ndim != 1:
        raise ValueError('节奏分析只接受单声道样本')
    if len(samples) > MAX_SECONDS*RATE or not np.isfinite(samples).all():
        raise ValueError('节奏分析需要最多24秒的有限采样')
    peak = float(np.max(np.abs(samples))) if len(samples) else 0
    if len(samples) < RATE or peak < 1e-5:
        return dict(status='insufficient_evidence', analyzed_seconds=round(len(samples)/RATE, 3),
                    limitations=['片段太短或能量不足；不推测鼓型或拍网。'])
    onset, sparse_fallback = _onset_envelope(samples, return_metadata=True)
    frame_rate = RATE / 256
    periodicity = _periodicity(onset, frame_rate, bpm_hint)
    # A caller-supplied hint narrows the search window. Sparse fallback can
    # otherwise expose a half-speed cycle right at that lower boundary; keep
    # that ambiguous result unknown instead of publishing a misleading BPM.
    if (sparse_fallback and periodicity is not None and _finite(bpm_hint) and bpm_hint > 0 and
            periodicity['bpm'] < bpm_hint * .75):
        periodicity = None
    if periodicity is None:
        return dict(status='insufficient_evidence', analyzed_seconds=round(len(samples)/RATE, 3),
                    onset_density=round(float(np.mean(onset > .25)), 4),
                    limitations=['没有稳定的局部周期证据；不把噪声、旋律或检测误差当鼓律动。'])
    interval = periodicity['interval_seconds']
    # Fold onset energy into 16 equal subdivisions of the estimated beat.
    times = np.arange(len(onset)) / frame_rate
    phase = np.mod(times, interval) / interval
    bins = np.minimum((phase * 16).astype(int), 15)
    profile = np.array([np.mean(onset[bins == i]) if np.any(bins == i) else 0 for i in range(16)])
    profile /= max(float(profile.max()), 1e-9)
    # Odd subdivisions are a proxy for syncopation, not a swing detector.
    offbeat = float(np.mean(profile[[1, 3, 5, 7, 9, 11, 13, 15]]))
    downbeat = float(np.mean(profile[[0, 4, 8, 12]]))
    entropy_values = profile / max(float(profile.sum()), 1e-9)
    entropy = float(-np.sum(entropy_values[entropy_values > 0] * np.log2(entropy_values[entropy_values > 0])) / 4)
    return dict(status='estimated', analyzed_seconds=round(len(samples)/RATE, 3),
                onset_density=round(float(np.mean(onset > .25)), 4),
                accent_profile=[round(float(v), 4) for v in profile],
                offbeat_ratio=round(offbeat / max(downbeat + offbeat, 1e-9), 4),
                accent_entropy=round(entropy, 4), **periodicity,
                periodicity_method='centered_overlap_correlation',
                limitations=['这是脉冲包络与细分代理，不是Kick/Snare转写、风格识别、Swing测量或听感评分。'])


def decode_window(path, start=0, end=None):
    if (not _finite(start) or start < 0 or
            (end is not None and (not _finite(end) or not start < end <= start+MAX_SECONDS))):
        raise ValueError('鼓组分析窗口需为有效的0至24秒源区间')
    duration = MAX_SECONDS if end is None else end-start
    args = [*INPUT_OPTIONS]
    if start: args += ['-ss', f'{start:.9f}']
    args += ['-i', Path(path)]
    args += ['-t', f'{duration:.9f}']
    args += ['-ac', '2', '-ar', str(RATE), '-f', 'f32le', 'pipe:1']
    result = run_ffmpeg(args)
    stereo = np.frombuffer(result.stdout, dtype='<f4').reshape(-1, 2)
    expected = round(duration*RATE)
    if (not np.isfinite(stereo).all() or
            (end is not None and len(stereo) < expected-2)):
        raise ValueError('鼓组分析窗口解码截短或包含非有限采样')
    stereo = stereo[:expected]
    if not len(stereo): return np.array([], dtype=float)
    return stereo[:, int(np.argmax(np.mean(stereo.astype(float)**2, axis=0)))].copy()


def analyze_file(path, start=0, end=None, bpm_hint=None):
    return analyze_samples(decode_window(path, start, end), bpm_hint=bpm_hint)


def compare_grooves(first, second):
    def valid(report):
        if not isinstance(report, dict) or report.get('status') != 'estimated':
            return False
        profile = report.get('accent_profile')
        return (isinstance(profile, list) and len(profile) == 16 and
                all(_finite(v) and 0 <= v <= 1 for v in profile) and any(v > 0 for v in profile) and
                _finite(report.get('bpm')) and 0 < report['bpm'] <= 220 and
                _finite(report.get('offbeat_ratio')) and 0 <= report['offbeat_ratio'] <= 1 and
                _finite(report.get('periodicity_support')) and
                MIN_CENTERED_CORRELATION <= report['periodicity_support'] <= 1 and
                report.get('periodicity_method') == 'centered_overlap_correlation')
    if not all(valid(report) for report in (first, second)):
        return dict(status='insufficient_evidence', compatible=None,
                    reason='至少一侧没有稳定鼓组周期证据；保留未知，不自动改鼓。')
    p, q = np.array(first['accent_profile']), np.array(second['accent_profile'])
    cosine = float(np.dot(p, q) / max(np.linalg.norm(p)*np.linalg.norm(q), 1e-9))
    bpm_gap = abs(first['bpm'] - second['bpm']) / max(first['bpm'], second['bpm'])
    offbeat_gap = abs(first['offbeat_ratio'] - second['offbeat_ratio'])
    compatible = bool(bpm_gap <= .025 and cosine >= .72 and offbeat_gap <= .18)
    reasons = []
    if bpm_gap > .025: reasons.append('有效脉冲速度差较大，长时间同响可能漂移。')
    if cosine < .72: reasons.append('16格重音分布相似度较低，可能需要只保留一套鼓骨架。')
    if offbeat_gap > .18: reasons.append('偏拍能量差异明显，直拍/切分或摆动感可能互抢。')
    return dict(status='estimated', compatible=compatible, bpm_gap=round(bpm_gap, 4),
                accent_cosine=round(cosine, 4), offbeat_gap=round(offbeat_gap, 4),
                reasons=reasons or ['局部代理未发现明显冲突；仍须试听确认重音和鼓音色。'],
                limitations=['不是鼓件身份、力度、音色衰减、Swing或口袋听感的自动判定。'])
