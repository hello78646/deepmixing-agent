"""A bounded, evidence-led mixing pass, separate from choosing the joins."""
import copy
import math

from .automatic_analysis import RATE, _decode_stereo


CELL = .4  # seconds in the output timeline, not source seconds


def _frames(track, slot, speed, start, count):
    """Measure stereo power without cancelling out-of-phase channels."""
    import numpy as np

    source_start = slot['start'] + start * speed
    step = CELL * speed
    width = round(step * RATE)
    if not track.get('path') or width < 256:
        return None
    try:
        raw = _decode_stereo(track['path'], source_start, min(count * step, slot['end'] - source_start))
    except (OSError, RuntimeError, ValueError):
        return None
    indexes = np.rint(np.arange(count + 1) * step * RATE).astype(int)
    if len(raw) < indexes[-1] - 2 or not np.isfinite(raw).all():
        return None
    result = []
    pitch = 2 ** (slot['semitones'] / 12)
    gain = 10 ** (slot['gain_db'] / 20)
    for left, right in zip(indexes, indexes[1:]):
        samples = raw[left:right].astype(np.float64)
        rms = float(np.sqrt(np.mean(samples ** 2))) * gain
        power = np.sum(abs(np.fft.rfft(samples * np.hanning(len(samples))[:, None], axis=0)) ** 2, axis=1)
        hz = np.fft.rfftfreq(len(samples), 1 / RATE) * pitch
        total = max(float(power.sum()), 1e-12)
        low = float(power[(hz >= 25) & (hz <= 200)].sum()) / total
        mid = float(power[(hz >= 500) & (hz <= 1900)].sum()) / total
        result.append(dict(rms=rms, low=low, low_rms=rms * math.sqrt(low), mid_rms=rms * math.sqrt(mid)))
    return result


def _available(slot, inventory):
    return not (slot.get('preserve_original') or inventory[slot['track_id']].get('preserve_original') or slot.get('warp_map'))


def _points(regions, value, normal, length):
    points = []
    for start, end, amount in regions:
        start, end = min(length, start), min(length, end)
        for at, level in ((max(0., start - .12), normal), (start, amount),
                          (end, amount), (min(length, end + .20), normal)):
            if points and at - points[-1]['time'] < .005:
                # Only possible at a clip edge: use the held value there.
                points[-1][value] = level if level != normal else points[-1][value]
            else:
                points.append({'time': at, value: level})
    return points


def _vocal_regions(a, b, offset, left, right, sa, sb, tracks):
    # Analyse up to 24 seconds of actual overlap; never extrapolate into the tail.
    count = min(60, int((right - left + 1e-8) / CELL))
    if count < 2:
        return []
    av = _frames(tracks[a['track_id']], a, sa, offset + left, count)
    bv = _frames(tracks[b['track_id']], b, sb, left, count)
    if not av or not bv:
        return []
    typical = sorted(x['rms'] for x in av)[int(.8 * (count - 1))]
    gate = max(.004, typical * .33)
    runs = []
    for i, (voice, backing) in enumerate(zip(av, bv)):
        if voice['rms'] < gate or voice['mid_rms'] < .0015 or backing['mid_rms'] < max(.003, voice['mid_rms'] * .72):
            continue
        strength = backing['mid_rms'] / voice['mid_rms']
        if runs and runs[-1][1] == i:
            first, _, peak = runs[-1]; runs[-1] = (first, i + 1, max(peak, strength))
        else:
            runs.append((i, i + 1, strength))
    # Prefer the clearest sustained competition, with only two audible moves.
    picked = sorted(runs, key=lambda r: ((r[1] - r[0]) * min(2., r[2]), r[2]), reverse=True)[:2]
    return [(left + i * CELL, left + j * CELL, -min(3.5, 2. + (ratio - .72) * 1.3)) for i, j, ratio in sorted(picked)]


def _section_move(track, slot, speed, left, right, can_eq, can_gain, check):
    count = min(25, int((right - left) / CELL))
    if count < 7:
        return None
    width = count * CELL
    starts = sorted({left, (left + right - width) / 2, right - width}) if right - left - width >= 2 else [left]
    choices = []
    for at in starts:
        check()
        frames = _frames(track, slot, speed, at, count)
        if not frames:
            continue
        for i in range(2, count - 3):
            # Check one- and two-cell swells against the local energy on both
            # sides, rather than comparing a chorus against a quiet breakdown.
            for cells in (1, 2):
                segment = frames[i:i + cells]
                before, after = frames[i-2:i], frames[i+cells:i+cells+2]
                shoulders = before + after
                support = max(x['rms'] for x in shoulders)
                baseline = min(max(x['rms'] for x in before), max(x['rms'] for x in after))
                if baseline < .008 or support > baseline * 1.7:
                    continue
                level = min(x['rms'] for x in segment) / support
                bass = min(x['low_rms'] for x in segment) / max(.001, *(x['low_rms'] for x in shoulders))
                share = min(x['low'] for x in segment)
                region = (at + i * CELL, at + (i + cells) * CELL)
                if can_eq and bass >= 1.9 and level >= 1.35 and share >= .48 and share - max(x['low'] for x in shoulders) >= .20:
                    choices.append((math.log2(bass) + 1, 'low_eq', *region, -min(3.5, 2. + math.log2(bass / 1.9))))
                elif can_gain and level >= 2.05:
                    choices.append((math.log2(level), 'ride', *region, -min(2.8, 1.4 + math.log2(level / 2.05))))
    return max(choices, default=None)


def tune_project(project, tracks, input_project=None, *, allow_full_mix=True, degraded_stems=False, preview=False, check=lambda: None):
    """Return a new project and concrete, serialisable edits, or no change.

    Source locks, manually authored effects/envelopes, and nonlinear time maps
    take precedence. No splits, notes, timing or source audio are modified.
    """
    p = copy.deepcopy(project)
    # An inserted sample or loop changes what is actually audible at a
    # source coordinate. Do not treat the raw tracks as the rendered mix.
    performance = p.get('performance') or {}
    if performance.get('events') or performance.get('drums'):
        return p, []
    is_set = p.get('kind') == 'continuous_set'
    keys = list(range(len(p['clips']))) if is_set else ['a', 'b']
    slots = p['clips'] if is_set else [p[k] for k in keys]
    authored = ((input_project.get('clips') if is_set else [input_project['a'], input_project['b']])
                if input_project else [])
    speeds = [p['target_bpm'] / c['bpm'] if p['target_bpm'] else 1. for c in slots]
    lengths = [(c['end'] - c['start']) / s for c, s in zip(slots, speeds)]
    edits = []
    window_start, window_end = 0., math.inf
    if preview:
        if is_set:
            from .continuous import preview_window, set_layout
            window_start, duration = preview_window(set_layout(p), p['preview_join'])
        elif p['mode'] == 'transition':
            window_start = max(0., lengths[0] - p['transition_seconds'] - 5)
            duration = max(25., p['transition_seconds'] + 10)
        else:
            total = max(lengths[0], lengths[1] + p['b_offset_seconds'])
            window_start = max(0., min(p['b_offset_seconds'] - 3, total - 25))
            duration = 25.
        window_end = window_start + duration
    def occupied(c, field):
        # Also protect repeated occurrences of a manually treated asset.
        return any(a['track_id'] == c['track_id'] and a.get(field) for a in authored)

    if p.get('mode') == 'mashup':
        a, b = slots
        a_track, b_track = [tracks[c['track_id']] for c in slots]
        if (degraded_stems or a['role'] != 'vocal' or b['role'] != 'instrumental'
                or a_track.get('stem_role') != 'vocal' or b_track.get('stem_role') != 'instrumental'
                or a.get('warp_map') or not _available(b, tracks)
                or any(occupied(c, field) for c in slots for field in ('effects', 'gain_envelope'))
                or any(fx['type'] == 'eq' and fx['band'] == 'mid' for fx in b.get('effects', []))
                or len(b.get('effects', [])) >= 8):
            return p, edits
        offset = p['b_offset_seconds']
        check()
        left = max(0., window_start - offset)
        right = min(lengths[0] - offset, lengths[1], window_end - offset)
        regions = _vocal_regions(a, b, offset, left, right, *speeds, tracks)
        if regions:
            points = _points(regions, 'gain_db', 0., lengths[1])
            b.setdefault('effects', []).append(dict(type='eq', band='mid', points=points))
            edits.append(dict(clip='b', control='mid_eq', points=points, technique_ids=['T71'],
                              reason='只在实测人声和伴奏中频挤在一起的段落，让伴奏略退；唱句空档恢复，主唱和鼓点不做整体压低。'))
    elif allow_full_mix:
        joins = [j['seconds'] for j in p['joins']] if is_set else [p['transition_seconds']]
        clip_start = 0.
        for i, (key, c, speed, length) in enumerate(zip(keys, slots, speeds, lengths)):
            left = max(window_start - clip_start, joins[i - 1] if i else 0.) + .25
            outgoing = joins[i] if i < len(joins) else 0.
            right = min(window_end - clip_start, length - outgoing) - .25
            clip_start += length - outgoing
            track = tracks[c['track_id']]
            if c['role'] != 'full_mix' or track.get('stem_role') or not _available(c, tracks):
                continue
            # Keep printed effects/envelopes intact, including their intended feel.
            can_eq = not (c.get('bass_db') or c.get('effects') or occupied(c, 'effects') or c.get('gain_envelope'))
            can_gain = not (c.get('gain_envelope') or c.get('effects') or occupied(c, 'gain_envelope'))
            if not (can_eq or can_gain):
                continue
            move = _section_move(track, c, speed, left, right, can_eq, can_gain, check)
            if not move:
                continue
            _, kind, start, end, db = move
            if kind == 'low_eq':
                points = _points([(start, end, db)], 'gain_db', 0., length)
                c['effects'] = [dict(type='eq', band='low', points=points)]
                reason = '交接以外有一小段低频明显比前后都重，只在这一处轻收低频，随后恢复原音。'
            else:
                points = _points([(start, end, 10 ** (db / 20))], 'gain', 1., length)
                c['gain_envelope'] = points
                reason = '交接以外有一小段音量明显高于前后，只轻收这一处，不给整首歌套压缩。'
            edits.append(dict(clip=key, control=kind, points=points, technique_ids=['T73'], reason=reason))
    return p, edits
