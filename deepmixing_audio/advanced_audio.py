"""Validation and safe FFmpeg graph builders for optional per-slot effects."""
import math
from .dynamics_audio import PROCESSORS,validate_processor,processor_filter


_EFFECT_TYPES = {"eq", "filter", "delay", "reverb"}|PROCESSORS


def _finite(value, label, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(label + "须为有限数字")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(label + "须为有限数字")
    if low is not None and (value < low or (high is not None and value > high)):
        raise ValueError(f"{label}须在{low}至{high}之间")
    return float(value)


def _points(raw, length, value_key, low, high, label):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 16:
        raise ValueError("效果控制点需要1至16个")
    result = []
    previous = None
    for point in raw:
        if not isinstance(point, dict) or set(point) != {"time", value_key}:
            raise ValueError(label + "控制点字段无效")
        time = _finite(point["time"], "效果时间", 0, length)
        value = _finite(point[value_key], label, low, high)
        if previous is not None and time - previous < 0.005 - 1e-9:
            raise ValueError("效果控制点须递增，间隔至少5毫秒")
        previous = time
        result.append({"time": time, value_key: value})
    return result


def beat_delay_ms(bpm, beat_fraction):
    """Convert a caller-supplied tempo, not a detected or confirmed beat grid."""
    bpm = _finite(bpm, '回声速度', 20, 400)
    fraction = _finite(beat_fraction, '回声拍数', .125, 4)
    return _finite(60000 * fraction / bpm, '延迟毫秒', 1, 2000)


def validate_effects(effects, length, locked=False, tracks=None):
    """Validate and canonicalise a slot's optional effects list.

    The returned objects contain only fixed enum fields and finite numbers, so
    callers can safely interpolate them into an FFmpeg graph.
    """
    if not isinstance(effects, list):
        raise ValueError("effects须为数组")
    if locked and effects:
        raise ValueError("保持原貌已锁定：不能添加逐轨效果")
    if len(effects) > 8:
        raise ValueError("逐轨效果最多8条")
    length = _finite(length, "效果片段长度", 0)
    result = []
    for effect in effects:
        if not isinstance(effect, dict) or "type" not in effect:
            raise ValueError("效果须为带type的对象")
        kind = effect["type"]
        if not isinstance(kind, str) or kind not in _EFFECT_TYPES:
            raise ValueError("不支持的逐轨效果类型")
        if kind in PROCESSORS:
            result.append(validate_processor(effect,length,_finite,_points,tracks))
            continue
        if kind == "eq":
            if set(effect) != {"type", "band", "points"} or effect["band"] not in ("low", "mid", "high"):
                raise ValueError("EQ效果字段无效")
            points = _points(effect["points"], length, "gain_db", -24, 24, "EQ增益")
            result.append({"type": kind, "band": effect["band"], "points": points})
        elif kind == "filter":
            if set(effect) != {"type", "mode", "points"} or effect["mode"] not in ("lowpass", "highpass"):
                raise ValueError("滤波效果字段无效")
            points = _points(effect["points"], length, "cutoff", 20, 20000, "滤波截止频率")
            result.append({"type": kind, "mode": effect["mode"], "points": points})
        elif kind == "delay":
            required = {"type", "delay_ms", "feedback", "points"}
            if not required <= set(effect) or set(effect) - required - {'wet_highpass_hz', 'wet_lowpass_hz', 'stereo_mode'}:
                raise ValueError("延迟效果字段无效")
            delay_ms = _finite(effect["delay_ms"], "延迟毫秒", 1, 2000)
            feedback = _finite(effect["feedback"], "延迟反馈", 0, .95)
            points = _points(effect["points"], length, "wet", 0, 1, "延迟湿声")
            item = {"type": kind, "delay_ms": delay_ms, "feedback": feedback, "points": points}
            for key, low, high in (('wet_highpass_hz', 20, 4000), ('wet_lowpass_hz', 500, 20000)):
                if key in effect:
                    item[key] = _finite(effect[key], '回声湿声滤波', low, high)
            if item.get('wet_highpass_hz', 20) >= item.get('wet_lowpass_hz', 20000):
                raise ValueError('湿声高通须低于湿声低通')
            if 'stereo_mode' in effect:
                if effect['stereo_mode'] not in ('stereo', 'pingpong'):
                    raise ValueError('回声声道模式无效')
                item['stereo_mode'] = effect['stereo_mode']
            result.append(item)
        else:
            if set(effect) - {"type", "decay", "points", "pre_delay_ms", "wet_highpass_hz", "wet_lowpass_hz"} or "points" not in effect:
                raise ValueError("混响效果字段无效")
            decay = _finite(effect.get("decay", .8), "混响衰减", .05, 2)
            points = _points(effect["points"], length, "wet", 0, 1, "混响湿声")
            item = {"type": kind, "decay": decay, "points": points}
            for key, label, low, high in (
                    ("pre_delay_ms", "混响预延迟", 0, 200),
                    ("wet_highpass_hz", "湿声高通", 20, 4000),
                    ("wet_lowpass_hz", "湿声低通", 500, 20000)):
                if key in effect:
                    item[key] = _finite(effect[key], label, low, high)
            if item.get('wet_highpass_hz', 20) >= item.get('wet_lowpass_hz', 20000):
                raise ValueError("湿声高通须低于湿声低通")
            result.append(item)
    return result


def _piecewise(points, key):
    expression = f"{points[-1][key]:.9f}"
    for left, right in reversed(list(zip(points, points[1:]))):
        delta = right[key] - left[key]
        ramp = f"({left[key]:.9f}+({delta:.9f})*(t-{left['time']:.9f})/{right['time']-left['time']:.9f})"
        expression = f"if(lt(t,{right['time']:.9f}),{ramp},{expression})"
    expression = f"if(lt(t,{points[0]['time']:.9f}),{points[0][key]:.9f},{expression})"
    return expression.replace(",", r"\,")


def _weight(points, index):
    if len(points) == 1:
        return "1"
    if index == 0:
        start, end = points[0]['time'], points[1]['time']
        expr = f"if(lt(t,{start:.9f}),1,if(lt(t,{end:.9f}),({end:.9f}-t)/{end-start:.9f},0))"
    elif index == len(points) - 1:
        start = points[-2]["time"]
        end = points[-1]["time"]
        expr = f"if(lt(t,{start:.9f}),0,if(lt(t,{end:.9f}),(t-{start:.9f})/{end-start:.9f},1))"
    else:
        left, point, right = points[index - 1], points[index], points[index + 1]
        expr = f"if(lt(t,{left['time']:.9f}),0,if(lt(t,{point['time']:.9f}),(t-{left['time']:.9f})/{point['time']-left['time']:.9f},if(lt(t,{right['time']:.9f}),({right['time']:.9f}-t)/{right['time']-point['time']:.9f},0)))"
    return expr.replace(",", r"\,")


def _aeval(label, expression, out):
    # aeval can leave an unspecified two-channel layout on this FFmpeg build.
    # Later channel-selective EQ/filter stages then silently bypass processing.
    return (f"{label}aeval=val(0)*({expression})|val(1)*({expression}):c=stereo,"
            f"aformat=channel_layouts=stereo[{out}]")


def build_effect_graph(source, effects, prefix, sidechain_labels=None):
    """Return ``(semicolon-separated graph fragment, final_label)``."""
    fragments = []
    current = source
    for index, effect in enumerate(effects):
        kind = effect["type"]
        if kind in PROCESSORS:
            dry,proc,wet,out=(f'{prefix}p{index}{suffix}' for suffix in ('dry','proc','wet','out'))
            fragments.append(f'{current}asplit=2[{dry}][{proc}]')
            key=''
            if kind=='sidechain':
                key=(sidechain_labels or {}).get(index)
                if not key:raise ValueError('侧链触发输入缺失')
            if kind=='sidechain':
                block=proc+'block'
                fragments.append(f'[{proc}]asetnsamples=n=1024:p=1[{block}]')
                proc=block
            fragments.append(f'[{proc}]{key}{processor_filter(effect)}[{wet}]')
            weight=_piecewise(effect['points'],'wet')
            fragments.append(_aeval(f'[{dry}]',f'(1-({weight}))',dry+'mix'))
            fragments.append(_aeval(f'[{wet}]',weight,wet+'mix'))
            fragments.append(f'[{dry}mix][{wet}mix]amix=inputs=2:normalize=0:dropout_transition=0[{out}]')
            current=f'[{out}]'
            continue
        if kind in ("eq", "filter"):
            labels = []
            split = [f"{prefix}f{index}_{point_index}" for point_index in range(len(effect["points"]))]
            fragments.append(current + f"asplit={len(split)}" + "".join(f"[{label}]" for label in split))
            for point_index, point in enumerate(effect["points"]):
                base = split[point_index]
                if kind == "filter":
                    filt = effect["mode"] + f"=f={point['cutoff']:.6f}"
                else:
                    gain = point['gain_db']
                    filt = {'low': f'bass=g={gain:.9f}:f=250',
                            'mid': f'equalizer=f=1000:t=q:w=0.7:g={gain:.9f}',
                            'high': f'treble=g={gain:.9f}:f=4000'}[effect['band']]
                fragments.append(f"[{base}]{filt}[{base}raw]")
                weight = _weight(effect["points"], point_index)
                fragments.append(_aeval(f"[{base}raw]", weight, f"{base}wet"))
                labels.append(f"[{base}wet]")
            merged = f"{prefix}filter{index}"
            fragments.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0[{merged}]")
            current = f"[{merged}]"
        elif kind == "delay":
            labels = []
            # A bounded feed-forward expansion of the feedback delay. This is
            # deterministic, has no unstable state, and includes audible repeats.
            taps = 16
            split = [f"{prefix}d{index}_{part}" for part in range(taps + 1)]
            fragments.append(current + f"asplit={taps + 1}" + "".join(f"[{label}]" for label in split))
            for tap in range(1, taps + 1):
                raw = f"{prefix}d{index}_{tap}raw"
                wet = f"{prefix}d{index}_{tap}wet"
                delay = effect["delay_ms"] * tap
                gain = effect["feedback"] ** (tap - 1)
                processing = f'adelay=delays={delay:.6f}:all=1'
                for key, filt in (('wet_highpass_hz', 'highpass'), ('wet_lowpass_hz', 'lowpass')):
                    if key in effect:
                        processing += f",{filt}=f={effect[key]:.6f}"
                if effect.get('stereo_mode') == 'pingpong':
                    # Sum only the wet source to mono, then alternate its taps.
                    # This is a finite response, not a recursive feedback model.
                    mono = '.5*c0+.5*c1'
                    left, right = (mono, '0*c0') if tap % 2 else ('0*c0', mono)
                    processing += f',pan=stereo|c0={left}|c1={right}'
                fragments.append(f"[{split[tap]}]{processing}[{raw}]")
                expr = f"({gain:.9f})*({_piecewise(effect['points'], 'wet')})"
                fragments.append(_aeval(f"[{raw}]", expr, wet))
                labels.append(f"[{wet}]")
            merged = f"{prefix}delay{index}"
            dry = f'{prefix}d{index}dry'
            fragments.append(f"[{split[0]}]anull[{dry}]")
            fragments.append(f"[{dry}]" + "".join(labels) + f"amix=inputs={len(labels)+1}:normalize=0:dropout_transition=0[{merged}]")
            current = f"[{merged}]"
        else:
            labels = []
            # Unequal reflection times plus progressively darker late energy
            # form a small room impulse response, rather than a single echo.
            delays = (29, 43, 61, 89, 127, 173, 229, 293, 367, 449, 557, 677)
            split = [f"{prefix}r{index}_{part}" for part in range(len(delays) + 1)]
            fragments.append(current + f"asplit={len(delays) + 1}" + "".join(f"[{label}]" for label in split))
            for tap, delay in enumerate(delays):
                raw = f"{prefix}r{index}_{tap}raw"
                wet = f"{prefix}r{index}_{tap}wet"
                reflection_ms = delay * effect['decay'] / .8
                # Pre-delay shifts the wet response without weakening it or
                # moving the unfiltered dry branch. Legacy settings are exact.
                milliseconds = reflection_ms + effect.get('pre_delay_ms', 0)
                gain = .32 * math.exp(-6.907755 * reflection_ms / (1000 * effect['decay']))
                cutoff = min(max(1500, 12000 - tap * 800), effect.get('wet_lowpass_hz', 20000))
                highpass = (f",highpass=f={effect['wet_highpass_hz']:.6f}"
                            if 'wet_highpass_hz' in effect else '')
                fragments.append(f"[{split[tap + 1]}]adelay=delays={milliseconds:.6f}:all=1{highpass},lowpass=f={cutoff}[{raw}]")
                expr = f"({gain:.9f})*({_piecewise(effect['points'], 'wet')})"
                fragments.append(_aeval(f"[{raw}]", expr, wet))
                labels.append(f"[{wet}]")
            merged = f"{prefix}reverb{index}"
            dry = f'{prefix}r{index}dry'
            fragments.append(f"[{split[0]}]anull[{dry}]")
            fragments.append(f"[{dry}]" + "".join(labels) + f"amix=inputs={len(labels)+1}:normalize=0:dropout_transition=0[{merged}]")
            current = f"[{merged}]"
    return ";".join(fragments), current

