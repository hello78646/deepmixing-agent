"""Bounded, local audio evidence for the automatic mix planner.

The functions in this module report measurements and review candidates.  They
do not infer genre, lyrics, confirmed chords, or permission to change audio.
"""
import math
from pathlib import Path

import numpy as np

from .analysis import INPUT_OPTIONS
from .music_guides import ANALYSIS_VERSION, analyze_region, compare_keys
from .runtime import prepare_dependencies, run_ffmpeg

RATE = 22050
MAX_WINDOW = 24.0
_KEY_MODES = {"major", "minor"}


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _defaults(start, end):
    return {
        "start": round(float(start), 6),
        "end": round(float(end), 6),
        "analysis_version": ANALYSIS_VERSION,
        "grid": {"status": "unknown", "bpm": None},
        "key": {"status": "unknown", "tonic": None, "mode": None, "name": "调性未确定", "candidates": []},
        "key_windows": {"status": "unknown", "windows": []},
        "beats": [],
        "phrase_changes": [],
        "tempo_changes": [],
        "beat_drift_candidates": [],
        "repetition_candidates": [],
        "bar_anchor_candidates": [],
        "changes": [],
        "waveform": [],
        "measured_features": [],
        "energy": 0.0,
        "low_frequency_ratio": 0.0,
        "activity_regions": [],
        "boundary_candidates": [],
        "diagnostics": [],
        "limitations": [
            "最多分析24秒局部窗口；网格、拍点和结构变化只是候选，需要人工试听。",
            "结构变化核对持续音高、能量和音色；短过门与窗口边缘可能漏检，不确认主歌或副歌。",
            "没有识别歌词语义、已确认和弦或曲风；调性候选也可能不稳定。",
        ],
    }


def _decode_stereo(path, start, duration):
    result = run_ffmpeg([
        *INPUT_OPTIONS, "-ss", f"{start:.9f}", "-i", Path(path), "-t", f"{duration:.9f}",
        "-ac", "2", "-ar", str(RATE), "-f", "f32le", "-acodec", "pcm_f32le", "pipe:1",
    ])
    raw = np.frombuffer(result.stdout, dtype="<f4")
    if raw.size < 2:
        raise ValueError("区域没有可解码音频")
    raw = raw[: raw.size - (raw.size % 2)].reshape(-1, 2)
    return raw


def _decode(path, start, duration):
    raw = _decode_stereo(path, start, duration)
    # Follow the louder channel so anti-phase stereo is not silently cancelled.
    channel = int(np.argmax(np.mean(raw.astype(np.float64) ** 2, axis=0)))
    return raw[:, channel].astype(np.float32, copy=False)


def inspect_low_band(track, start, end):
    """Measure 20–200 Hz occupancy in an exact, bounded source window.

    Stereo channel powers are combined without summing waveforms. These are
    pre-effect spectral energy proxies, not identified bass notes or kick drums.
    Missing, truncated and very short windows never authorize an EQ adjustment.
    """
    if (not isinstance(track, dict) or not _finite_number(track.get('duration')) or
            not _finite_number(start) or not _finite_number(end) or
            not 0 <= start < end <= track['duration'] or end-start > MAX_WINDOW):
        raise ValueError('低频测量需要轨道范围内、不超过24秒的有限正时长窗口')
    result = dict(status='unknown', start=float(start), end=float(end),
                  energy=None, low_frequency_ratio=None, low_frequency_rms=None,
                  method='stereo_power_20_200_hz', reason='低频窗口不足或音频不可用')
    if end-start < .12-1e-9 or not track.get('path'):
        return result
    try:
        raw = _decode_stereo(track['path'], start, end-start).astype(np.float64)
        if abs(len(raw)/RATE-(end-start)) > 2/RATE or not np.isfinite(raw).all():
            return result
        energy = float(np.sqrt(np.mean(raw**2)))
        result['energy'] = round(energy, 9)
        if energy < 1e-5:
            result.update(status='silent', low_frequency_ratio=0., low_frequency_rms=0.,
                          reason='窗口为静音或低于本地能量门限，不削减低频')
            return result
        # Centred, overlapping windows include transients at both crop edges.
        # A single whole-crop Hann window could hide the first incoming kick.
        size, hop = 2048, 512
        padded = np.pad(raw, ((size//2, size//2), (0, 0)))
        window = np.hanning(size)[:, None]
        frequencies = np.fft.rfftfreq(size, 1/RATE)
        low_bins = (frequencies >= 20) & (frequencies <= 200)
        total, low = 0., 0.
        for offset in range(0, len(padded)-size+1, hop):
            power = np.abs(np.fft.rfft(padded[offset:offset+size]*window, axis=0))**2
            total += float(power.sum())
            low += float(power[low_bins].sum())
        ratio = low/total if total > 1e-12 else 0.
        result.update(status='measured', low_frequency_ratio=round(ratio, 6),
                      low_frequency_rms=round(energy*math.sqrt(ratio), 9),
                      reason='实测源窗口的声道功率；不是分离出的底鼓或相位兼容判断')
    except Exception:
        # Diagnostics must not expose a private path or subprocess error text.
        result['reason'] = '低频窗口解码或测量失败，保留原低频'
    return result


def _fallback_features(report, y, start):
    if y.size == 0:
        return
    y64 = y.astype(np.float64, copy=False)
    energy = float(np.sqrt(np.mean(y64 * y64)))
    report["energy"] = round(energy, 8)
    chunks = np.array_split(np.abs(y64), min(240, max(1, len(y64))))
    report["waveform"] = [round(float(np.max(chunk)), 5) for chunk in chunks if len(chunk)]
    spectrum = np.abs(np.fft.rfft(y64 * np.hanning(len(y64)))) ** 2
    frequencies = np.fft.rfftfreq(len(y64), 1.0 / RATE)
    total = float(np.sum(spectrum))
    low = float(np.sum(spectrum[(frequencies >= 20) & (frequencies <= 200)]))
    report["low_frequency_ratio"] = round(low / total, 6) if total > 1e-12 else 0.0

    # Short RMS frames give observable activity boundaries without naming a
    # section or a lyric.  Silence is explicitly gated before any feature name.
    frame = max(1, int(0.25 * RATE))
    rms = np.array([np.sqrt(np.mean(y64[i : i + frame] ** 2)) for i in range(0, len(y64), frame)])
    if energy < 1e-5 or not len(rms):
        return
    threshold = max(0.003, energy * 0.20)
    active = rms >= threshold
    regions = []
    begin = None
    for idx, is_active in enumerate(active):
        if is_active and begin is None:
            begin = idx
        if begin is not None and (not is_active or idx == len(active) - 1):
            finish = idx + 1 if is_active and idx == len(active) - 1 else idx
            if finish - begin >= 1:
                regions.append({"start": round(start + begin * 0.25, 3),
                                "end": round(min(start + finish * 0.25, report["end"]), 3),
                                "kind": "measured_activity"})
            begin = None
    report["activity_regions"] = regions
    active_share = float(np.mean(active))
    ratio = report["low_frequency_ratio"]
    if energy >= 0.01 and ratio >= 0.45:
        report["measured_features"].append("bass_dense")
    # Sparse is deliberately conservative: intermittent measured activity is
    # evidence of occupancy, not a claim about instrumentation or genre.
    if energy >= 0.01 and 0.0 < active_share <= 0.30 and len(regions) >= 2:
        report["measured_features"].append("sparse_arrangement")


def inspect_track(track, start=0, end=None):
    """Inspect at most 24 seconds of a service track and return JSON values."""
    if not isinstance(track, dict):
        raise TypeError("track必须是服务轨道对象")
    duration = track.get("duration")
    if not _finite_number(duration) or float(duration) < 0:
        raise ValueError("轨道时长无效")
    if not _finite_number(start) or float(start) < 0:
        raise ValueError("开始时间无效")
    requested_start = float(start)
    if requested_start > float(duration):
        raise ValueError("开始时间超出轨道时长")
    actual_start = requested_start
    requested_end = float(duration) if end is None else end
    if not _finite_number(requested_end):
        raise ValueError("结束时间无效")
    actual_end = min(float(requested_end), float(duration), actual_start + MAX_WINDOW)
    if actual_end <= actual_start:
        raise ValueError("所选区域须有正的音频时长")
    report = _defaults(actual_start, actual_end)
    prepare_dependencies()
    path = track.get("path")
    if not path:
        report["diagnostics"].append("轨道缺少可用音频源")
        return report
    try:
        y = _decode(path, actual_start, actual_end - actual_start)
        # FFmpeg can return a slightly shorter tail than requested.
        report["end"] = round(actual_start + len(y) / RATE, 6)
        _fallback_features(report, y, actual_start)
    except Exception:
        # Keep a generic diagnostic.  Exception text can contain the source path.
        report["diagnostics"].append("区域音频解码失败，无法取得能量证据")
        return report
    try:
        measured = analyze_region(path, actual_start, report["end"])
        if isinstance(measured, dict):
            for field in ("grid", "key", "key_windows", "beats", "phrase_changes", "tempo_changes", "beat_drift_candidates", "repetition_candidates", "bar_anchor_candidates"):
                if field in measured:
                    report[field] = measured[field]
            if "changes" in measured:
                report["changes"] = [
                    candidate for candidate in measured["changes"] if isinstance(candidate, dict)
                ]
                report["boundary_candidates"] = [
                    {**candidate, "limitation": "仅为音频特征边界候选，不代表歌词语义或已确认乐句。"}
                    for candidate in measured["changes"]
                    if isinstance(candidate, dict)
                ]
    except Exception:
        report["diagnostics"].append("音乐结构分析依赖失败，已保留能量与静音测量")
    return report


def _reliable_key(report):
    key = report.get("key") if isinstance(report, dict) else None
    if not isinstance(key, dict) or key.get("status") not in ("estimated", "user"):
        return None
    tonic, mode = key.get("tonic"), key.get("mode")
    if isinstance(tonic, bool) or not isinstance(tonic, int) or not 0 <= tonic < 12 or mode not in _KEY_MODES:
        return None
    return tonic, mode


def compare_regions(a_report, b_report):
    """Compare measured region reports; suggestions remain review-only."""
    a_grid = a_report.get("grid", {}) if isinstance(a_report, dict) else {}
    b_grid = b_report.get("grid", {}) if isinstance(b_report, dict) else {}
    a_bpm = a_grid.get("bpm") if a_grid.get("status") == "estimated" and _finite_number(a_grid.get("bpm")) else None
    b_bpm = b_grid.get("bpm") if b_grid.get("status") == "estimated" and _finite_number(b_grid.get("bpm")) else None
    if a_bpm and b_bpm:
        ratio = float(b_bpm) / float(a_bpm)
        a_grid_review = isinstance(a_grid.get("diagnostics"), dict) and bool(a_grid["diagnostics"].get("review_required"))
        b_grid_review = isinstance(b_grid.get("diagnostics"), dict) and bool(b_grid["diagnostics"].get("review_required"))
        pulse = {"status": "measured", "bpm_a": round(float(a_bpm), 3), "bpm_b": round(float(b_bpm), 3),
                 "risk": "review" if abs(ratio - 1.0) > 0.03 or a_grid_review or b_grid_review else "low"}
        tempo_ratio = {"value": round(ratio, 6), "candidates": [round(ratio, 6), round(ratio / 2, 6), round(ratio * 2, 6)],
                       "status": "candidate_only"}
    else:
        pulse = {"status": "unknown", "bpm_a": None, "bpm_b": None, "risk": "unknown"}
        tempo_ratio = {"value": None, "candidates": [], "status": "unknown"}

    a_key = a_report.get("key", {}) if isinstance(a_report, dict) else {}
    b_key = b_report.get("key", {}) if isinstance(b_report, dict) else {}
    harmony = compare_keys(a_key, b_key)
    ka, kb = _reliable_key(a_report), _reliable_key(b_report)
    proposed = None
    if ka and kb:
        shift = ((ka[0] - kb[0] + 6) % 12) - 6
        varying_windows = (
            isinstance(a_report.get("key_windows"), dict) and a_report["key_windows"].get("status") == "varying_candidates"
        ) or (
            isinstance(b_report.get("key_windows"), dict) and b_report["key_windows"].get("status") == "varying_candidates"
        )
        if abs(shift) <= 3 and ka[1] == kb[1] and not varying_windows:
            proposed = int(shift)
        key_status = "measured"
        key_risk = "low" if proposed == 0 else "review"
    else:
        key_status, key_risk = "unknown", "unknown"
    key = {"status": key_status, "risk": key_risk, "proposed_relation": "same_mode_candidate" if ka and kb else None}

    ea = a_report.get("energy") if isinstance(a_report, dict) else None
    eb = b_report.get("energy") if isinstance(b_report, dict) else None
    if _finite_number(ea) and _finite_number(eb):
        scale = max(float(ea), float(eb), 1e-9)
        difference = abs(float(ea) - float(eb)) / scale
        energy = {"status": "measured", "difference_ratio": round(difference, 6),
                  "risk": "review" if difference > 0.35 else "low"}
    else:
        energy = {"status": "unknown", "difference_ratio": None, "risk": "unknown"}
    return {"pulse": pulse, "key": key, "harmony": harmony, "energy": energy,
            "proposed_b_shift": proposed, "tempo_ratio": tempo_ratio,
            "limitations": ["变速与移调候选不会自动执行，需用户明确决定并试听。"]}
