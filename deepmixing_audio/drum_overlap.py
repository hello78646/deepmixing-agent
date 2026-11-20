"""Review actual simultaneous drum attacks; never quantize or classify taste.

Source provenance, native timing and effective envelopes are required. This
bounded evidence is separate from estimated beat grids or drum-piece identity.
"""
import math

import numpy as np

from .accompaniment_bridge import _stem_scale
from .attack_alignment import attack_landmarks, match_attacks
from .automatic_analysis import _decode_stereo, RATE
from .continuous import set_layout

BANDS = {'low': (35., 180.), 'body': (180., 2500.), 'high': (2500., 9000.)}


def compare_drum_samples(first, second, sample_rate=RATE):
    """Measure equal, already aligned stereo contributions, at most 8s.

    Near doubles use ordered one-to-one matching after coincident attacks are
    removed. Counts are acoustic candidates, not proof of conflicting drums.
    """
    a, b = np.asarray(first, dtype=float), np.asarray(second, dtype=float)
    if (isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or not 22050 <= sample_rate <= 192000
            or a.ndim != 2 or a.shape[1] != 2 or b.shape != a.shape
            or not .25*sample_rate <= len(a) <= 8*sample_rate
            or not np.isfinite(a).all() or not np.isfinite(b).all()):
        raise ValueError('需要等长、有限、0.25至8秒的同时间轴立体声鼓轨')
    seconds = len(a)/sample_rate
    report = dict(status='unknown', duration_seconds=seconds, bands={},
                  audio_processed=False, review_only=True, compatible=None,
                  limitations=['声学起音不是底鼓、军鼓、首拍或唱词标注。',
                               '近邻双击、密度及力度代理不能判定律动好坏；不自动移动或删除鼓击。',
                               '只分析所列窗口与声明路径；分轨漏音和渲染器最终限幅可能改变听感。'])
    for name, band in BANDS.items():
        peaks = [attack_landmarks(x, sample_rate, band=band, min_spacing=.05)['landmarks'] for x in (a, b)]
        maximum = max((p['rms'] for side in peaks for p in side), default=0.)
        # A barely audible stem must not contribute equally after independent
        # normalization. These are effective rendered-contribution levels.
        peaks = [[p for p in side if p['rms'] >= max(1e-5, maximum*.1)] for side in peaks]
        if any(len(side)>64 for side in peaks):
            report['bands'][name] = dict(status='unknown', reason='起音过密，超过一对一核对上限')
            continue
        times = [[p['time'] for p in side] for side in peaks]
        aligned = match_attacks(*times, tolerance=.025)
        used_a = {p['target_index'] for p in aligned['matches']}
        used_b = {p['observed_index'] for p in aligned['matches']}
        remaining = [[t for i, t in enumerate(times[0]) if i not in used_a],
                     [t for i, t in enumerate(times[1]) if i not in used_b]]
        near = match_attacks(*remaining, tolerance=.1)
        n, m = map(len, times)
        # Strength comparison uses only coincident attacks and common gain,
        # not a separately normalized beat-folded profile.
        ratios = [min(peaks[0][p['target_index']]['rise'], peaks[1][p['observed_index']]['rise']) /
                  max(peaks[0][p['target_index']]['rise'], peaks[1][p['observed_index']]['rise'], 1e-12)
                  for p in aligned['matches']]
        report['bands'][name] = dict(status='measured' if n and m else 'insufficient_evidence',
            first_count=n, second_count=m, first_attacks=peaks[0], second_attacks=peaks[1],
            first_density_hz=n/seconds, second_density_hz=m/seconds,
            coincident_count=aligned['matched'], near_double_count=near['matched'],
            near_double_offsets_seconds=[p['error_seconds'] for p in near['matches']],
            unmatched_count=n+m-2*aligned['matched']-2*near['matched'],
            median_coincident_strength_ratio=float(np.median(ratios)) if ratios else None,
            coincidence_tolerance_seconds=.025, near_double_limit_seconds=.1)
    if any(b['status']=='measured' for b in report['bands'].values()):
        report['status'] = 'measured'
    return report


def _drum_source(clip, tracks):
    track = tracks[clip['track_id']]
    if track.get('stem_role')=='drums':
        return track, 1.
    if clip['role']!='full_mix' or track.get('stem_role'):
        raise ValueError('需要明确同源鼓分轨')
    stems = [t for t in tracks.values() if t.get('stem_role')=='drums'
             and t.get('source_track_id')==clip['track_id']
             and abs(t.get('duration', -1)-track['duration']) <= .05]
    if len(stems)!=1:
        raise ValueError('同源鼓轨缺失或有多个版本，不能代选')
    if 'shared_scale' not in stems[0].get('separation_quality',{}):
        raise ValueError('缺少同源分轨共同缩放证据')
    return stems[0], 1/_stem_scale(stems[0])


def review_drum_overlap(plan, tracks, join_index=None, *, resolve_path=None):
    """Read-only diagnostic of a validated pair or set's actual overlap.

    Native speed only, no effects/warps/performance. Source drum stems may be
    selected uniquely for a full mix, with shared-scale compensation. The final
    limiter is outside this diagnostic, so no exact audible-mix claim is made.
    """
    unknown = dict(status='unknown', review_only=True, audio_processed=False,
                   compatible=None, reason='实际双鼓同响证据不足，不自动改鼓')
    try:
        if plan.get('performance') or plan.get('echo'):
            return unknown
        is_set = plan.get('kind')=='continuous_set'
        if is_set:
            i = plan['preview_join'] if join_index is None else join_index
            layout = set_layout(plan)
            clips = plan['clips'][i:i+2]
            origins = [layout['clips'][j]['start'] for j in (i,i+1)]
            begin, finish = layout['joins'][i]['start'], layout['joins'][i]['end']
            if plan['joins'][i]['kind']=='echo':
                return unknown
            fade = True
        else:
            clips = [plan['a'], plan['b']]
            length = clips[0]['end']-clips[0]['start']
            fade = plan['mode']=='transition'
            origins = [0.,length-plan['transition_seconds'] if fade else plan['b_offset_seconds']]
            begin = origins[1]
            finish = min(length, origins[1]+clips[1]['end']-clips[1]['start'])
        if (len(clips)!=2 or finish-begin<.25 or
                any(c.get('warp_map') or c.get('effects') or c['bass_db'] or c['semitones'] or
                    (plan['target_bpm'] is not None and abs(plan['target_bpm']/c['bpm']-1)>1e-9) for c in clips)):
            return unknown
        end = min(finish, begin+8.)
        count = round((end-begin)*RATE)
        output_times = begin+np.arange(count)/RATE
        signals, sources = [], []
        for side, (clip, origin) in enumerate(zip(clips, origins)):
            track, scale = _drum_source(clip, tracks)
            start = clip['start']+begin-origin
            path = resolve_path(track['id']) if resolve_path else track['path']
            samples = _decode_stereo(path, start, end-begin).astype(float)
            if len(samples)<count-2 or not np.isfinite(samples).all():
                return unknown
            if len(samples)<count:
                # Do not fabricate missing frames to make matching succeed.
                return unknown
            relative = output_times-origin
            length = clip['end']-clip['start']
            level = np.minimum(np.clip(relative/.005,0,1), np.clip((length-relative)/.005,0,1))
            if fade:
                phase = np.clip((output_times-begin)/(finish-begin),0,1)*math.pi/2
                level *= np.cos(phase) if side==0 else np.sin(phase)
            points = clip.get('gain_envelope', [])
            if points:
                level *= np.interp(relative,[p['time'] for p in points],[p['gain'] for p in points])
            samples = samples[:count] * (level*scale*10**(clip['gain_db']/20))[:,None]
            signals.append(samples)
            sources.append(dict(track_id=track['id'], source_start=start, source_end=start+count/RATE,
                                clip_output_origin=origin, stem_compensation=scale))
        result = compare_drum_samples(*signals)
        result.update(output_start=begin, output_end=begin+count/RATE, total_overlap_seconds=finish-begin,
                      sources=sources, fade_included=fade, scope='actual_overlap_first_up_to_8_seconds')
        return result
    except (KeyError, IndexError, TypeError, ValueError, RuntimeError, OSError, OverflowError):
        return unknown
