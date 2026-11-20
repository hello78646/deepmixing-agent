"""Reject new automatic bridges that suppress measured outgoing vocal activity.

Stem power is acoustic evidence, never a recognized word or a complete phrase.
Existing saved/manual projects are not rewritten by this review gate.
"""
import math

import numpy as np

from .accompaniment_bridge import _stem_scale
from .automatic_analysis import _decode_stereo, RATE
from .continuous import set_layout, validate_set

STEP = .01


def _envelope(points, clock):
    if not points:
        return np.ones(len(clock))
    return np.interp(clock, [p['time'] for p in points], [p['gain'] for p in points])


def _vocal_coefficient(plan, tracks, index, vocal, source_clock):
    clip = plan['clips'][index]
    layout = set_layout(plan)
    local = source_clock - clip['start']
    length = clip['end'] - clip['start']
    inside = (local >= 0) & (local < length)
    gain = _envelope(clip.get('gain_envelope'), local) * inside
    gain *= np.clip(local / .005, 0, 1) * np.clip((length-local) / .005, 0, 1)
    # Match the renderer's equal-power qsin crossfades, including the preceding
    # handoff. An already attenuated source tail is not counted as a new loss.
    if index and plan['joins'][index-1]['seconds']:
        gain *= np.sin(np.clip(local / plan['joins'][index-1]['seconds'], 0, 1)*math.pi/2)
    if index < len(plan['joins']) and plan['joins'][index]['seconds']:
        gain *= np.sin(np.clip((length-local) / plan['joins'][index]['seconds'], 0, 1)*math.pi/2)
    global_clock = layout['clips'][index]['start'] + local
    for event in plan.get('performance', {}).get('events', []):
        asset = tracks[event['track_id']]
        if event['track_id'] == clip['track_id']:
            scale = 1.
        elif (asset.get('source_track_id') == clip['track_id'] and
              asset.get('stem_role') == 'vocal'):
            scale = _stem_scale(asset)
        else:
            continue
        cue_length = event['end'] - event['start']
        for repeat in range(event['repeats']):
            relative = global_clock-event['time']-repeat*event['interval']
            audible = (relative >= 0) & (relative < cue_length)
            if not audible.any():
                continue
            offset = event['start'] + relative - source_clock
            if event['semitones'] or np.max(np.abs(offset[audible])) > 1/44100:
                raise ValueError('前曲人声事件不与原曲源时间对应')
            level = scale * 10**((event['gain_db']-clip['gain_db'])/20)
            event_gain = _envelope(event.get('gain_envelope'), relative)*audible*level
            event_gain *= np.clip(relative/.005, 0, 1)*np.clip((cue_length-relative)/.005, 0, 1)
            gain += event_gain
    return gain


def review_outgoing_vocal(original, proposed, tracks, join, vocal_id):
    """Compare six-second preparation paths against a real aligned vocal stem.

Only loss of >=6dB versus an audible baseline for >=30ms of measured activity
rejects this route. Decoder/provenance/scale failures remain unknown and cannot
authorize automatic suppression. A pass is not semantic or listening approval.
"""
    result = dict(status='unknown', eligible=False, confirmed_phrase=False,
        scope='outgoing_six_second_bridge_preparation', activity_is_lyrics=False,
        reason='前曲人声活动证据不足，保留原曲候选')
    try:
        before, after = validate_set(original, tracks), validate_set(proposed, tracks)
        if (isinstance(join, bool) or not isinstance(join, int) or
                not 0 <= join < len(before['joins']) or join >= len(after['joins'])):
            return result
        a, b = before['clips'][join], after['clips'][join]
        vocal = tracks.get(vocal_id, {})
        if (a['track_id'] != b['track_id'] or before['target_bpm'] is not None or after['target_bpm'] is not None or
                a['semitones'] or b['semitones'] or a.get('warp_map') or b.get('warp_map') or
                vocal.get('stem_role') != 'vocal' or vocal.get('source_track_id') != a['track_id'] or
                abs(vocal.get('duration', 0)-tracks[a['track_id']]['duration']) > .05):
            return result
        lo, hi = max(a['start'], a['end']-6.25), a['end']
        scale = _stem_scale(vocal)
        raw = _decode_stereo(vocal['path'], lo, hi-lo).astype(float)/scale
        if len(raw) < round((hi-lo)*RATE)-2 or not np.isfinite(raw).all():
            return result
        hop, frame = round(STEP*RATE), round(.02*RATE)
        starts = np.arange(0, len(raw)-frame+1, hop)
        power = np.mean(raw**2, axis=1)
        cumulative = np.concatenate(([0.], np.cumsum(power)))
        rms = np.sqrt(np.maximum(0, (cumulative[starts+frame]-cumulative[starts])/frame))
        clock = lo+(starts+frame/2)/RATE
        if not len(clock):
            return result
        threshold = max(.003, float(np.sqrt(np.mean(power)))*.2)
        active = rms >= threshold
        prior = _vocal_coefficient(before, tracks, join, vocal, clock)
        current = _vocal_coefficient(after, tracks, join, vocal, clock)
        loss = active & (prior >= .5) & (current < prior*.5)
        lost_seconds = float(np.count_nonzero(loss)*hop/RATE)
        eligible = lost_seconds < .03
        result.update(status='passed' if eligible else 'rejected', eligible=eligible,
            measured_source_start=lo, measured_source_end=hi,
            activity_threshold_rms=round(threshold, 6),
            measured_activity_seconds=round(float(np.count_nonzero(active)*hop/RATE), 4),
            newly_suppressed_activity_seconds=round(lost_seconds, 4),
            maximum_source_window_seconds=6.25,
            reason=('当前声学活动未触发新增尾声压低保护；不代表完整唱句已确认' if eligible else
                    '桥接会压低前曲仍有活动的尾段主唱，保留原曲候选'),
            limitations=['分轨活动可能含和声、混响或漏音；不是逐词或主唱身份识别。',
                         '只检查本次桥接六秒准备区域；不验收全曲乐句、后曲起句或听感。'])
    except (KeyError, TypeError, ValueError, RuntimeError, OSError, OverflowError):
        pass
    return result
