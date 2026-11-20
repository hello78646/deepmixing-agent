"""Bounded, audition-first timbre profiles.

These profiles describe common current listening directions rather than a
"viral sound" guarantee.  They use the existing EQ/filter/reverb primitives,
keep the source tempo/pitch untouched, and return auditable effect data for a
single clip.
"""
import copy


PROFILES = {
    "clean_pop": {
        "title": "清亮流行",
        "summary": "压一点浑浊低频，轻抬人声存在感和空气感。",
        "effects": ("low", -1.0, "mid", 0.8, "high", 1.5),
        "risk": "高频过量会让齿音和刺耳感变明显。",
    },
    "warm_rnb": {
        "title": "暖厚 R&B",
        "summary": "保留低频重量，收一点尖锐高频，增加很短的空间尾巴。",
        "effects": ("low", 1.2, "mid", 0.5, "high", -1.0),
        "reverb": {"decay": 0.55, "wet": 0.045, "pre_delay_ms": 24, "wet_highpass_hz": 220, "wet_lowpass_hz": 6800},
        "risk": "低频密集或尾响过长时容易变糊。",
    },
    "vocal_air": {
        "title": "人声空气感",
        "summary": "给中高频留出清晰度，轻削低频占位，适合人声主角。",
        "effects": ("low", -1.8, "mid", 1.2, "high", 1.2),
        "risk": "原始人声已亮或齿音明显时应降低高频增益。",
    },
    "club_punch": {
        "title": "俱乐部冲击",
        "summary": "轻抬低频冲击和高频瞬态，保持中频不过度拥挤。",
        "effects": ("low", 1.5, "mid", -0.6, "high", 1.0),
        "risk": "不能替代真正的底鼓/贝斯分轨；峰值与低频堆积需复测。",
    },
    "soft_lofi": {
        "title": "柔和 Lo-fi",
        "summary": "收窄高频并保留中低频密度，模拟较柔和的播放质感。",
        "effects": ("low", 0.8, "mid", 0.2, "high", -2.0),
        "filter": {"mode": "lowpass", "cutoff": 10500},
        "risk": "过度低通会掩盖歌词辅音和鼓组起音。",
    },
}


def _points(length, key, value):
    end = max(0.25, float(length))
    return [dict(time=0.0, **{key: float(value)}), dict(time=end, **{key: float(value)})]


def build_timbre_effects(profile, length, *, role="full_mix"):
    """Return validated-shape effects and an explanation for one clip."""
    if profile not in PROFILES:
        raise ValueError("未收录该音色方向")
    if not isinstance(length, (int, float)) or length <= 0:
        raise ValueError("音色片段长度须为正数")
    item = PROFILES[profile]
    low = dict(type="eq", band="low", points=_points(length, "gain_db", item["effects"][1]))
    mid = dict(type="eq", band="mid", points=_points(length, "gain_db", item["effects"][3]))
    high = dict(type="eq", band="high", points=_points(length, "gain_db", item["effects"][5]))
    effects = [low, mid, high]
    if "filter" in item:
        effects.append(dict(type="filter", mode=item["filter"]["mode"],
                            points=_points(length, "cutoff", item["filter"]["cutoff"])))
    if "reverb" in item:
        reverb = item["reverb"]
        effects.append(dict(type="reverb", decay=reverb["decay"], pre_delay_ms=reverb["pre_delay_ms"],
                            wet_highpass_hz=reverb["wet_highpass_hz"], wet_lowpass_hz=reverb["wet_lowpass_hz"],
                            points=_points(length, "wet", reverb["wet"])))
    # A vocal role can use the vocal profile directly; for full mixes we keep
    # the same conservative curve and disclose that it is not stem isolation.
    note = item["summary"] + ("当前轨道是完整混音，不能把该曲线称为独立人声处理。" if role == "full_mix" else "")
    return copy.deepcopy(effects), dict(profile=profile, title=item["title"], summary=note,
                                        risk=item["risk"], role=role,
                                        tempo_policy="original_speed_only", applied=False,
                                        requires_preview=True)


def list_timbre_profiles():
    return [{"id": key, "title": value["title"], "summary": value["summary"], "risk": value["risk"]}
            for key, value in PROFILES.items()]

