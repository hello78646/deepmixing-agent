"""Measured, local delivery presets and static-gain A/B listening copies.

Presets are choices for this project, not requirements imposed by a platform.
The normalization pass never compresses or limits one candidate independently.
"""
import math
import os
from pathlib import Path
import tempfile

from .analysis import INPUT_OPTIONS
from .engine import measure
from .runtime import run_ffmpeg


_TARGETS = {
    'preview': ('试听比较', -18., -2.),
    'social': ('社交分享建议', -14., -1.5),
    'listening': ('日常聆听', -18., -1.),
}


def delivery_target(name):
    """Return a copy of an explicit, advisory loudness/true-peak preset."""
    if not isinstance(name, str) or name not in _TARGETS:
        raise ValueError('请选择preview、social或listening交付目标')
    label, loudness, peak = _TARGETS[name]
    return dict(name=name, label=label, target_lufs=loudness,
                peak_ceiling_dbtp=peak, platform_standard=False,
                note='这是本项目的交付建议，不代表平台硬性响度标准。')


def _number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + '须为有限数字')
    try:
        valid = math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f'{label}须在{low}至{high}之间')
    return float(value)


def _measurement(path):
    result = measure(path)
    loudness, peak = result['integrated_lufs'], result['true_peak_dbtp']
    # EBU R128 reports its -70 LUFS floor for silence and material below its
    # gate. That sentinel is not a usable measured loudness for normalization.
    if loudness is not None and loudness <= -69.9:
        loudness = None
    return dict(integrated_lufs=loudness, true_peak_dbtp=peak)


def _source_paths(paths):
    if not isinstance(paths, (list, tuple)) or not 1 <= len(paths) <= 8:
        raise ValueError('等响度比较需要1至8个本机音频路径')
    result = []
    for path in paths:
        if not isinstance(path, (str, os.PathLike)):
            raise ValueError('比较源须为本机文件路径')
        try:
            source = Path(path).resolve(strict=True)
        except (OSError, ValueError, RuntimeError) as exc:
            raise ValueError('比较源不是可读取的本机文件') from exc
        if not source.is_file():
            raise ValueError('比较源不是本机音频文件')
        result.append(source)
    return result


def normalize_comparison(paths, output_dir, target_lufs=-18, *, peak_ceiling_dbtp=-1.5):
    """Export WAV/MP3 listening copies at a common measured loudness.

    Each source gets one static gain derived from its measured LUFS. If any
    candidate needs extra peak headroom, every candidate receives the same
    additional attenuation, preserving fair loudness matching and dynamics.
    Unknown/silent sources are exported but never labelled normalized.
    Output names are generated in a unique child folder and never reuse sources.
    """
    target = _number(target_lufs, '目标响度', -36, -8)
    ceiling = _number(peak_ceiling_dbtp, '真实峰值上限', -12, -0.1)
    sources = _source_paths(paths)
    if not isinstance(output_dir, (str, os.PathLike)):
        raise ValueError('输出目录须为本机目录路径')
    directory = Path(output_dir).resolve()
    if directory.exists() and not directory.is_dir():
        raise ValueError('输出路径已是文件，请选择目录')

    before = [_measurement(source) for source in sources]
    gains = [target - item['integrated_lufs'] if item['integrated_lufs'] is not None else 0.
             for item in before]
    predicted = [item['true_peak_dbtp'] + gain for item, gain in zip(before, gains)
                 if item['true_peak_dbtp'] is not None]
    # Reserve a little room for sample-rate conversion and MP3 reconstruction.
    common = min(0., ceiling - .5 - max(predicted)) if predicted else 0.
    directory.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix='comparison-', dir=directory))
    candidates = [dict(source=str(source), source_measurement=measurement,
                       wav=str(destination / f'candidate-{index+1:02d}.wav'),
                       mp3=str(destination / f'candidate-{index+1:02d}.mp3'))
                  for index, (source, measurement) in enumerate(zip(sources, before))]

    # Static-gain retries apply a shared reduction to the entire comparison.
    # Both delivery codecs are re-measured; no prediction is called a result.
    for attempt in range(3):
        for source, gain, candidate in zip(sources, gains, candidates):
            applied = gain + common
            run_ffmpeg([*INPUT_OPTIONS, '-i', source, '-map', '0:a:0', '-vn',
                        '-af', f'volume={applied:.9f}dB', '-ar', '44100',
                        '-c:a', 'pcm_s24le', '-y', candidate['wav']], timeout=600)
            run_ffmpeg([*INPUT_OPTIONS, '-i', candidate['wav'], '-map', '0:a:0',
                        '-c:a', 'libmp3lame', '-b:a', '192k', '-y', candidate['mp3']], timeout=600)
            candidate['quality'] = {kind: _measurement(candidate[kind]) for kind in ('wav', 'mp3')}
            candidate['applied_gain_db'] = round(applied, 6)
        observed = [quality['true_peak_dbtp'] for candidate in candidates
                    for quality in candidate['quality'].values() if quality['true_peak_dbtp'] is not None]
        excess = max(observed) - ceiling if observed else 0.
        if excess <= .01 or attempt == 2:
            break
        common -= excess + .1

    effective = target + common if any(item['integrated_lufs'] is not None for item in before) else None
    for candidate in candidates:
        known = candidate['source_measurement']['integrated_lufs'] is not None
        if not known:
            candidate['status'] = 'unknown_or_silent'
            continue
        passed = all(quality['integrated_lufs'] is not None and
                     quality['true_peak_dbtp'] is not None and
                     quality['true_peak_dbtp'] <= ceiling + .01 and
                     abs(quality['integrated_lufs'] - effective) <= .5
                     for quality in candidate['quality'].values())
        candidate['status'] = 'measured' if passed else 'review_required'
    return dict(candidates=candidates, output_dir=str(destination), requested_lufs=target,
                effective_lufs=round(effective, 6) if effective is not None else None,
                common_attenuation_db=round(common, 6), peak_ceiling_dbtp=ceiling,
                status='measured' if all(item['status'] == 'measured' for item in candidates) else 'review_required',
                gain_method='static_gain_with_shared_peak_attenuation',
                limitations=['静音或低于响度测量门限的素材保持未知。',
                             '为保留动态并控制峰值，共同试听响度可能低于所选目标。',
                             '实际测量不代表听感通过；交付目标是项目建议，不是平台硬性标准。'])
