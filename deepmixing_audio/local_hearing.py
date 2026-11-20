"""Fine-grained, bounded evidence for a musical handoff.

The measurements here are deliberately descriptive.  They split each side of
an intended join into short source windows and expose likely crowding or
energy discontinuities.  They do not identify vocals, lyrics, instruments,
genre, or a confirmed phrase boundary, and never authorize a speed change.
"""
import math

import numpy as np

from .automatic_analysis import RATE, _decode_stereo


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _db(value):
    return round(20.0 * math.log10(max(float(value), 1e-9)), 3)


def _ratio(value, total):
    return round(float(value) / max(float(total), 1e-12), 5)


def summarize_samples(samples):
    """Summarize a finite stereo array without naming its sources."""
    raw = np.asarray(samples, dtype=np.float64)
    if raw.ndim != 2 or raw.shape[1] != 2 or len(raw) < max(256, RATE // 4):
        return {"status": "insufficient_evidence", "reason": "局部窗口太短，保留未知。"}
    if not np.isfinite(raw).all():
        return {"status": "insufficient_evidence", "reason": "局部窗口含非有限采样，保留未知。"}

    power = np.sqrt(np.mean(raw * raw, axis=0))
    main_channel = int(np.argmax(power))
    mono = raw[:, main_channel]
    frame, hop = 1024, 256
    count = 1 + max(0, (len(mono) - frame) // hop)
    windows = np.lib.stride_tricks.sliding_window_view(mono, frame)[::hop][:count]
    if len(windows) < 2:
        return {"status": "insufficient_evidence", "reason": "局部窗口没有足够的分析帧。"}
    windowed = windows * np.hanning(frame)
    rms_frames = np.sqrt(np.mean(windows * windows, axis=1))
    spectrum = np.abs(np.fft.rfft(windowed, axis=1)) ** 2
    frequencies = np.fft.rfftfreq(frame, 1.0 / RATE)
    total = np.sum(spectrum, axis=1)

    def band(lo, hi):
        mask = (frequencies >= lo) & (frequencies < hi)
        return float(np.mean(np.sum(spectrum[:, mask], axis=1)))

    total_power = float(np.mean(total))
    low = band(20, 200)
    low_mid = band(200, 500)
    mid = band(500, 2000)
    presence = band(2000, 5000)
    air = band(5000, 12000)
    flux = np.maximum(np.diff(spectrum, axis=0), 0).sum(axis=1)
    if len(flux):
        threshold = max(float(np.percentile(flux, 70)), float(np.mean(flux) * 1.35), 1e-12)
        transient_rate = float(np.mean(flux >= threshold)) * RATE / hop
    else:
        transient_rate = 0.0
    active = rms_frames >= max(float(np.percentile(rms_frames, 20)) * 0.45, 1e-5)
    active_values = rms_frames[active]
    low_rms = float(np.percentile(active_values, 10)) if len(active_values) else 0.0
    high_rms = float(np.percentile(active_values, 95)) if len(active_values) else 0.0
    try:
        stereo_correlation = float(np.corrcoef(raw[:, 0], raw[:, 1])[0, 1])
    except (FloatingPointError, ValueError):
        stereo_correlation = 0.0
    if not math.isfinite(stereo_correlation):
        stereo_correlation = 0.0
    return {
        "status": "measured",
        "duration_seconds": round(len(raw) / RATE, 4),
        "rms_db": _db(float(np.sqrt(np.mean(raw * raw)))),
        "peak_db": _db(float(np.max(np.abs(raw)))),
        "crest_db": round(_db(float(np.max(np.abs(raw)))) - _db(float(np.sqrt(np.mean(raw * raw)))), 3),
        "dynamic_range_db": round(_db(high_rms) - _db(low_rms), 3) if low_rms > 0 else None,
        "low_ratio": _ratio(low, total_power),
        "low_mid_ratio": _ratio(low_mid, total_power),
        "mid_ratio": _ratio(mid, total_power),
        "presence_ratio": _ratio(presence, total_power),
        "air_ratio": _ratio(air, total_power),
        "transient_rate": round(transient_rate, 3),
        "silence_ratio": round(1.0 - float(np.mean(active)), 4),
        "stereo_correlation": round(max(-1.0, min(1.0, stereo_correlation)), 4),
        "limitations": [
            "频段是完整混音的能量代理，不等于人声、底鼓或贝斯分离结果。",
            "瞬态密度来自频谱变化，不是鼓件转写、Swing测量或口袋听感评分。",
        ],
    }


def _window(track, start, end):
    if not isinstance(track, dict) or not track.get("path"):
        return None
    if not (_finite(track.get("duration")) and _finite(start) and _finite(end)):
        return None
    start, end = float(start), float(end)
    if start < 0 or end <= start or end > float(track["duration"]) + 1e-6:
        return None
    try:
        samples = _decode_stereo(track["path"], start, end - start)
        if abs(len(samples)-round((end-start)*RATE)) > 2:
            return None
        return summarize_samples(samples)
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


def analyze_handoff(outgoing, incoming, *, outgoing_end, incoming_start,
                    window_seconds=4.0):
    """Analyze four short windows around a source handoff."""
    if not (_finite(outgoing_end) and _finite(incoming_start) and _finite(window_seconds)):
        return {"status": "unavailable", "reason": "交接源坐标无效，保留未知。"}
    if not .25 <= window_seconds <= 24:
        return {"status": "unavailable", "reason": "局部窗口不足，保留未知。"}
    w = float(window_seconds)
    out_end = float(outgoing_end)
    in_start = float(incoming_start)
    out_start = max(0.0, out_end - w)
    in_end = min(float(incoming.get("duration", in_start + w)), in_start + w)
    windows = {
        "outgoing_exit": _window(outgoing, out_start, out_end),
        "incoming_entry": _window(incoming, in_start, in_end),
    }
    # A second pair gives a local slope, without pretending that the detector
    # knows a verse/chorus or lyric boundary.
    if out_start - w >= 0:
        windows["outgoing_pre_exit"] = _window(outgoing, out_start - w, out_start)
    if in_end + w <= float(incoming.get("duration", in_end)):
        windows["incoming_post_entry"] = _window(incoming, in_end, in_end + w)
    if any(not isinstance(v, dict) or v.get("status") != "measured" for v in windows.values()):
        return {"status": "unavailable", "windows": windows,
                "reason": "至少一侧局部窗口无法取得完整信号证据，保留未知。"}

    left, right = windows["outgoing_exit"], windows["incoming_entry"]
    energy_step = abs(left["rms_db"] - right["rms_db"])
    low_overlap = min(left["low_ratio"], right["low_ratio"])
    mid_overlap = min(left["mid_ratio"] + left["presence_ratio"],
                      right["mid_ratio"] + right["presence_ratio"])
    transient_max = max(left["transient_rate"], right["transient_rate"], 1e-6)
    transient_gap = abs(left["transient_rate"] - right["transient_rate"]) / transient_max
    spectral_step = abs((left["low_ratio"] + left["mid_ratio"] + left["presence_ratio"]) -
                        (right["low_ratio"] + right["mid_ratio"] + right["presence_ratio"]))
    smoothness = (
        0.30 * max(0.0, 1.0 - energy_step / 9.0) +
        0.25 * max(0.0, 1.0 - min(low_overlap / 0.30, 1.0) * 0.55) +
        0.20 * max(0.0, 1.0 - min(spectral_step / 0.40, 1.0)) +
        0.15 * max(0.0, 1.0 - min(transient_gap, 1.0)) +
        0.10 * max(0.0, 1.0 - abs(left["stereo_correlation"] - right["stereo_correlation"]) / 1.5)
    )
    smoothness = round(max(0.0, min(1.0, smoothness)), 4)
    risks = []
    if low_overlap >= 0.22:
        risks.append("low_frequency_collision")
    if energy_step >= 6.0:
        risks.append("energy_step")
    if transient_gap >= 0.65:
        risks.append("transient_density_mismatch")
    if mid_overlap >= 0.52:
        risks.append("mid_presence_crowding")
    if min(left["stereo_correlation"], right["stereo_correlation"]) <= -0.35:
        risks.append("stereo_phase_review")
    if smoothness >= 0.72 and not set(risks) & {"energy_step", "stereo_phase_review"}:
        recommendation = "mid_song_smooth_blend_candidate"
        reason = "退出头尾与进入头部的能量、频段和瞬态差异较小，可优先试听中途短叠化。"
    elif smoothness >= 0.48:
        recommendation = "short_mid_song_handoff_candidate"
        reason = "局部证据允许中途接歌，但应缩短同响并先听低频和主旋律是否互抢。"
    else:
        recommendation = "micro_handoff_or_cut_candidate"
        reason = "局部差异较大，优先试听极短中途交接或直接切入，避免长叠。"
    return {
        "status": "measured",
        "windows": windows,
        "comparison": {
            "energy_step_db": round(energy_step, 3),
            "low_overlap_ratio": round(low_overlap, 5),
            "mid_presence_overlap": round(mid_overlap, 5),
            "transient_gap_ratio": round(transient_gap, 5),
            "spectral_step": round(spectral_step, 5),
            "smoothness_score": smoothness,
        },
        "risk_flags": risks,
        "recommendation": recommendation,
        "reason": reason,
        "requires_preview": True,
        "tempo_policy": "original_speed_only",
        "limitations": [
            "中途候选由局部源窗口生成，不确认歌词、乐句、首拍或真正的人声身份。",
            "完整混音频段重叠不等于一定冲突；最终必须听试听版本。",
        ],
    }
