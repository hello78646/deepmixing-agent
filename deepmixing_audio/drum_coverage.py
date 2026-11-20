"""Bounded acoustic coverage checks, not a drum or groove classifier."""
import math

import numpy as np

from .accompaniment_bridge import _stem_scale
from .automatic_analysis import _decode_stereo, RATE
from .continuous import set_layout, validate_set
from .transition_refinement import _pulse


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def inspect_drum_coverage(track, start, end, *, maximum_gap_seconds, gain_db=0., gain_envelope=None):
    """Measure a <=6.25s stem window plus up to 2s preceding context.

    Envelope times are relative to start (earlier points may be negative).
    Stereo frame power preserves anti-phase signals. Long quiet gaps reject a
    continuous-drum claim; a pass does not identify drum hits or compatible flow.
    """
    result = dict(status='unknown', eligible=False, method='stereo_rms_gap_coverage',
                  reason='鼓段活动证据不足，保留原曲候选', confirmed_groove=False)
    try:
        if (not isinstance(track, dict) or track.get('stem_role') != 'drums' or
                not all(_finite(v) for v in (start, end, maximum_gap_seconds, gain_db, track.get('duration'))) or
                not 0 <= start < end <= track['duration'] or not .12 <= end-start <= 6.25 or
                not .25 <= maximum_gap_seconds <= 3 or not -60 <= gain_db <= 0):
            return result
        points = gain_envelope or []
        if any(not isinstance(p, dict) or not _finite(p.get('time')) or
               not _finite(p.get('gain')) or not 0 <= p['gain'] <= 2 for p in points):
            return result
        if any(b['time'] <= a['time'] for a, b in zip(points, points[1:])):
            return result
        reference_start = max(0., start-2.)
        raw = _decode_stereo(track['path'], reference_start, end-reference_start).astype(float)
        if len(raw) < round((end-reference_start)*RATE)-2 or not np.isfinite(raw).all():
            return result
        raw *= 10**(gain_db/20) / _stem_scale(track)
        reference_rms = float(np.sqrt(np.mean(raw**2)))
        offset = round((start-reference_start)*RATE)
        raw = raw[offset:offset+round((end-start)*RATE)]
        hop, frame = round(.01*RATE), round(.02*RATE)
        starts = np.arange(0, len(raw)-frame+1, hop)
        power = np.mean(raw**2, axis=1)
        cumulative = np.r_[0., np.cumsum(power)]
        rms = np.sqrt(np.maximum(0., (cumulative[starts+frame]-cumulative[starts])/frame))
        times = (starts+frame/2)/RATE
        if not len(times):
            return result
        level = np.interp(times, [p['time'] for p in points], [p['gain'] for p in points]) if points else 1.
        threshold = max(.0005, reference_rms*.1)
        active = rms*level >= threshold
        # Voronoi frame cells cover both boundaries; edge silence is not
        # discarded just because there is no frame center at t=0 or t=end.
        edges = np.r_[0., (times[:-1]+times[1:])/2, end-start]
        gaps = []
        gap_start = None
        for i, is_active in enumerate(active):
            if not is_active and gap_start is None:
                gap_start = float(edges[i])
            if is_active and gap_start is not None:
                gaps.append((gap_start, float(edges[i])))
                gap_start = None
        if gap_start is not None:
            gaps.append((gap_start, float(end-start)))
        longest = max((b-a for a, b in gaps), default=0.)
        eligible = bool(active.any() and longest <= maximum_gap_seconds)
        result.update(status='passed' if eligible else 'rejected', eligible=eligible,
            source_start=float(start), source_end=float(end), reference_start=reference_start,
            activity_threshold_rms=round(threshold, 8), reference_rms=round(reference_rms, 8),
            gain_db=float(gain_db), activity_fraction=round(float(np.mean(active)), 6),
            longest_inactive_seconds=round(longest, 6), maximum_gap_seconds=float(maximum_gap_seconds),
            inactive_ranges=[dict(start=round(start+a, 6), end=round(start+b, 6)) for a, b in gaps],
            reason=('所测鼓段未触发长静音保护，鼓型与听感仍待核' if eligible else
                    '独立鼓桥区有过长近静音区间，保留原曲候选'),
            limitations=['声学活动可能包含漏音或持续音，不确认鼓击、节拍或律动相容。',
                         '只检查声明由鼓轨承接的局部，不等于整段能量或听感通过。'])
    except (KeyError, TypeError, ValueError, RuntimeError, OSError, OverflowError):
        pass
    return result


def review_drum_bridge(plan, tracks, join, drum_id, report):
    """Review the outgoing isolated-drum region of a new native drum bridge."""
    unknown = dict(status='unknown', eligible=False, reason='鼓桥覆盖证据不足，保留原曲候选',
                   confirmed_groove=False)
    try:
        p = validate_set(plan, tracks)
        if (isinstance(join, bool) or not isinstance(join, int) or
                not 0 <= join < len(p['joins']) or p['target_bpm'] is not None):
            return unknown
        a = p['clips'][join]
        track = tracks[drum_id]
        if (a['semitones'] or a.get('warp_map') or a.get('effects') or
                track.get('source_track_id') != a['track_id'] or
                abs(track['duration']-tracks[a['track_id']]['duration']) > .05):
            return unknown
        pulse = _pulse(report.get('a', {}))
        if not pulse:
            return unknown
        events = [e for e in p.get('performance', {}).get('events', []) if e['track_id'] == drum_id]
        if len(events) != 1:
            return unknown
        event = events[0]
        layout = set_layout(p)
        boundary = layout['clips'][join]['end']
        begin, finish = layout['clips'][join+1]['start'], boundary-.03
        if (event['semitones'] or event['repeats'] != 1 or
                event['time'] > begin or event['time']+event['end']-event['start'] < finish or
                abs(event['start']-a['end']+boundary-event['time']) > 1/44100):
            return unknown
        relative = begin-event['time']
        source_start = event['start']+relative
        # The final 30ms belongs to the explicit incoming full-mix rebuild.
        # Half-beat spaces and normal short drum decays are not a dropout.
        maximum_gap = max(.75, 1.5*pulse[1])
        envelope = [dict(time=point['time']-relative, gain=point['gain'])
                    for point in event.get('gain_envelope', [])]
        result = inspect_drum_coverage(track, source_start, source_start+finish-begin,
            maximum_gap_seconds=maximum_gap,
            gain_db=event['gain_db']+20*math.log10(_stem_scale(track)), gain_envelope=envelope)
        result.update(scope='native_outgoing_drum_bridge_only', output_start=begin,
                      output_end=finish, pulse_period_seconds=pulse[1])
        return result
    except (KeyError, TypeError, ValueError, RuntimeError, OSError, OverflowError):
        return unknown
