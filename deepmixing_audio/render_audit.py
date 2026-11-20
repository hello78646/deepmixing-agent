"""Read the final PCM around every declared join without changing audio.

Measurements locate energy, spectral or stereo changes. They do not identify
words, instruments, phrases, rooms, listener intent, or musical compatibility.
"""
import math
import wave

import numpy as np

WINDOW = .25


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _db(power):
    return 10*math.log10(max(power, 1e-15))


def _summary(audio, rate):
    power = float(np.mean(audio*audio))
    mid = (audio[:, 0]+audio[:, 1])/2
    side = (audio[:, 0]-audio[:, 1])/2
    spectra = np.abs(np.fft.rfft(audio*np.hanning(len(audio))[:, None], axis=0))**2
    frequencies = np.fft.rfftfreq(len(audio), 1/rate)
    low = float(spectra[(frequencies >= 20) & (frequencies < 200)].sum())
    total = float(spectra.sum())
    return dict(rms_dbfs=_db(power), low_band_ratio=low/total if total > 1e-15 else None,
                side_energy_fraction=float(np.mean(side*side))/power if power > 1e-12 else None,
                mono_energy_change_db=_db(float(np.mean(mid*mid))/power) if power > 1e-12 else None)


def audit_rendered_joins(path, joins, *, offset_seconds=0., expected_duration=None):
    """Inspect up to seven joins on the final WAV's exact sample clock.

    Join coordinates are absolute in the full output; preview crops explicitly
    report joins outside their coverage. No implicit padding or silence fill.
    """
    if (not isinstance(joins, list) or len(joins) > 7 or not _finite(offset_seconds)
            or offset_seconds < 0 or (expected_duration is not None and
            (not _finite(expected_duration) or expected_duration <= 0))):
        raise ValueError('成品检查需要最多七处交接及有效输出时间轴')
    for join in joins:
        if (not isinstance(join, dict) or not all(_finite(join.get(k)) for k in ('start', 'end'))
                or not 0 <= join['start'] <= join['end']):
            raise ValueError('成品交接坐标无效')
    result = dict(status='measured', audio_processed=False, review_only=True,
                  all_musical_checks_passed=None, joins=[], review_required=False,
                  offset_seconds=offset_seconds,
                  limitations=['只定位实际成品的能量、频谱和左右声道变化；不识别唱词、首拍、房间或乐句。',
                               '标记是听辨位置，不是缺陷或主观适配判决；没有标记也不代表接歌通过。',
                               '安静呼吸、空间变化及频段变化可能是创作意图，不自动修改音频。'])
    with wave.open(str(path)) as handle:
        if (handle.getnchannels(), handle.getsampwidth(), handle.getcomptype()) != (2, 2, 'NONE'):
            raise ValueError('成品交接检查需要完整双声道PCM16 WAV')
        rate, frames = handle.getframerate(), handle.getnframes()
        duration = frames/rate
        result.update(frames=frames, sample_rate=rate, duration_seconds=duration)
        if expected_duration is not None and abs(duration-expected_duration) > 2/rate:
            raise ValueError('成品时长与交接时间轴不一致')
        for index, join in enumerate(joins):
            start, end = join['start'], join['end']
            row = dict(index=index, start=start, end=end, windows=[], flags=[], musical_approval=None)
            lo, hi = max(0., start-1.), end+1.
            visible_lo, visible_hi = max(lo, offset_seconds), min(hi, offset_seconds+duration)
            if visible_hi-visible_lo < WINDOW:
                row.update(status='outside_preview', coverage='none')
                result['joins'].append(row)
                continue
            # Long crossfades remain bounded: inspect both edges and their
            # midpoint, rather than reading two minutes into memory per join.
            spans = [(max(lo, edge-1.), min(hi, edge+1.)) for edge in (start, (start+end)/2, end)]
            frame_starts = set()
            size = round(WINDOW*rate)
            for begin, finish in spans:
                first = round((max(begin, offset_seconds)-offset_seconds)*rate)
                last = round((min(finish, offset_seconds+duration)-offset_seconds)*rate)
                frame_starts.update(range(first, max(first, last-size+1), size))
            summaries = []
            for first in sorted(frame_starts):
                handle.setpos(first)
                raw = handle.readframes(size)
                if len(raw) != size*4:
                    raise ValueError('成品交接窗口采样截短')
                samples = np.frombuffer(raw, dtype='<i2').reshape(-1, 2).astype(float)/32768
                time = offset_seconds+first/rate
                summaries.append(dict(start=time, end=time+size/rate, **_summary(samples, rate)))
            row['windows'] = summaries
            full_context = offset_seconds <= lo+1/rate and offset_seconds+duration >= hi-1/rate
            row.update(status='measured' if full_context else 'partial_context',
                       coverage='complete' if full_context else 'partial')
            if not summaries or max(w['rms_dbfs'] for w in summaries) < -60:
                row['status'] = 'insufficient_signal'
            before = [w for w in summaries if w['end'] <= start+1/rate]
            after = [w for w in summaries if w['start'] >= end-1/rate]
            reference = max((w['rms_dbfs'] for w in before+after), default=None)
            if reference is not None and reference > -60:
                for w in summaries:
                    if w['end'] >= start and w['start'] <= end and w['rms_dbfs'] < reference-12:
                        row['flags'].append(dict(code='local_energy_dip', start=w['start'], end=w['end'],
                                                  relative_drop_db=reference-w['rms_dbfs']))
            for left, right in zip(summaries, summaries[1:]):
                if abs(right['start']-left['end']) > 2/rate:
                    continue
                if max(left['rms_dbfs'], right['rms_dbfs']) > -60 and abs(left['rms_dbfs']-right['rms_dbfs']) > 9:
                    row['flags'].append(dict(code='level_step', time=right['start'],
                                             change_db=right['rms_dbfs']-left['rms_dbfs']))
                a, b = left['side_energy_fraction'], right['side_energy_fraction']
                if a is not None and b is not None and abs(a-b) > .35:
                    row['flags'].append(dict(code='stereo_width_step', time=right['start'], change=b-a))
                a, b = left['low_band_ratio'], right['low_band_ratio']
                if a is not None and b is not None and abs(a-b) > .4:
                    row['flags'].append(dict(code='low_band_step', time=right['start'], change=b-a))
            for w in summaries:
                if w['mono_energy_change_db'] is not None and w['mono_energy_change_db'] < -10 and w['rms_dbfs'] > -60:
                    row['flags'].append(dict(code='mono_cancellation', start=w['start'], end=w['end'],
                                             loss_db=w['mono_energy_change_db']))
            row['review_required'] = bool(row['flags'])
            result['review_required'] |= row['review_required']
            result['joins'].append(row)
    if any(row['status'] != 'measured' for row in result['joins']):
        result['status'] = 'partial_coverage'
    return result


def rendered_project_joins(project):
    """Return the renderer's actual global join coordinates, never source time."""
    if project.get('kind') == 'continuous_set':
        from .continuous import set_layout
        return [{k:join[k] for k in ('start', 'end')} for join in set_layout(project)['joins']]
    from .models import durations
    a, b, _ = durations(project)
    if project['mode'] == 'transition':
        return [dict(start=a-project['transition_seconds'], end=a)]
    start = project['b_offset_seconds']
    return [dict(start=start, end=min(a, start+b))] if start < a else [dict(start=start, end=start)]


def attach_render_audit(result, project, path):
    result['quality']['rendered_joins'] = audit_rendered_joins(path, rendered_project_joins(project),
        offset_seconds=result['preview_start_seconds'], expected_duration=result['duration'])
    return result
