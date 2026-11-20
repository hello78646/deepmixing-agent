"""Bounded rendering probes for low-band interaction at a DJ handoff."""
import copy
import math

import numpy as np

from .analysis import INPUT_OPTIONS
from .engine import _slot_filter
from .runtime import run_ffmpeg

RATE = 8000


def probe_low_overlap(a, b, tracks, overlap, target_bpm=None):
    """Render both weighted stereo paths; never mono-sum or flip polarity.

    Uses production slot filters and qsin curves before the master limiter.
    A 20/200 Hz filter pair is a local interaction proxy, not kick separation.
    Long crops, effects failures or incomplete renders explicitly stay unknown.
    """
    unknown = dict(status='unknown', method='rendered_stereo_low_band',
                   reason='交接渲染证据不足，未判断相位相容')
    try:
        if not .08 <= overlap <= 2.2:
            return unknown
        paths = []
        for i, slot in enumerate((a, b)):
            clip = copy.deepcopy(slot)
            speed = target_bpm/clip['bpm'] if target_bpm else 1
            duration = clip['end']-clip['start']
            length = duration/speed
            if not overlap <= length <= 60 or duration > 60 or clip.get('warp_map'):
                return unknown
            source = tracks[clip['track_id']].get('path')
            if not source:
                return unknown
            args = [*INPUT_OPTIONS, '-ss', f"{clip['start']:.9f}", '-i', source]
            clip.update(start=0, end=duration)
            chains = [_slot_filter(0, clip, target_bpm)+'[slot]']
            start = length-overlap if i==0 else 0
            fade = 'out' if i==0 else 'in'
            chains.append(f'[slot]atrim=start={start:.9f}:duration={overlap:.9f},asetpts=PTS-STARTPTS,'
                          f'afade=t={fade}:st=0:d={overlap:.9f}:curve=qsin,aformat=channel_layouts=stereo,'
                          f'highpass=f=20,lowpass=f=200,aresample={RATE},'
                          'aformat=sample_fmts=flt:channel_layouts=stereo[out]')
            rendered = run_ffmpeg([*args, '-filter_complex', ';'.join(chains), '-map', '[out]',
                                   '-ar', RATE, '-ac', 2, '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1'], timeout=30)
            raw = np.frombuffer(rendered.stdout, dtype='<f4')
            if raw.size%2:
                return unknown
            raw = raw.reshape(-1, 2).astype(np.float64)
            if abs(len(raw)/RATE-overlap) > 2/RATE or not np.isfinite(raw).all():
                return unknown
            paths.append(raw)
        samples = min(map(len, paths))
        # Stereo powers, not a mono waveform: opposite L/R polarity is valid.
        left, right = (path[:samples] for path in paths)
        size = min(round(.10*RATE), samples)
        hop = round(.025*RATE)
        offsets = list(range(0, samples-size+1, hop))
        if offsets[-1] != samples-size:
            offsets.append(samples-size)
        frames = []
        for offset in offsets:
            x, y = left[offset:offset+size], right[offset:offset+size]
            px, py = float(np.mean(x*x)), float(np.mean(y*y))
            expected = px+py
            cross = float(np.mean(x*y))
            correlation = cross/max(math.sqrt(px*py), 1e-15)
            combined = max(0., expected+2*cross)
            frames.append(dict(time=(offset+size/2)/RATE, expected_power=expected,
                               combined_power=combined, correlation=max(-1., min(1., correlation)),
                               ratio_db=10*math.log10(max(combined/max(expected, 1e-15), 1e-8))))
        peak = max(f['expected_power'] for f in frames)
        if peak < 1e-10:
            return {**unknown, 'reason':'交接低频低于局部门限，不判断相位相容'}
        useful = [f for f in frames if f['expected_power'] >= max(1e-10, peak*.1)]
        longest = run = 0.
        previous = None
        for frame in frames:
            cancelled = (frame['expected_power']>=max(1e-10, peak*.1) and
                         frame['correlation']<-.65 and frame['ratio_db']<-4.5)
            if cancelled:
                run = size/RATE if previous is None else run+(frame['time']-previous)
                longest = max(longest, run)
                previous = frame['time']
            else:
                run, previous = 0., None
        worst = min(f['ratio_db'] for f in useful)
        excess = max(f['ratio_db'] for f in useful)
        classification = ('cancellation' if longest>=.15 else
                          'coherent_buildup' if excess>1.8 and any(f['correlation']>.65 for f in useful)
                          else 'no_sustained_issue')
        points = []
        for step in range(9):
            at = overlap*step/8
            frame = min(frames, key=lambda f:abs(f['time']-at))
            gain = (max(-3.1, -max(0., frame['ratio_db']))
                    if 0<step<8 and frame['expected_power']>=peak*.1 and frame['correlation']>.65 else 0.)
            points.append(dict(time=at, gain_db=round(gain, 4)))
        return dict(status='measured', method='rendered_stereo_low_band', classification=classification,
                    overlap_seconds=overlap, peak_sum_excess_db=round(excess, 4),
                    minimum_sum_ratio_db=round(worst, 4),
                    longest_cancellation_seconds=round(longest, 6), trim_points=points, frames=frames,
                    reason='实际加权声道的局部功率代理；不是全曲相位保证或听感评分')
    except Exception:
        return unknown


def probe_summary(probe):
    return {key:value for key,value in probe.items() if key not in ('frames', 'trim_points')}


def add_verified_bass_trim(a, b, tracks, length, overlap, target_bpm, before):
    """Keep a common low shelf only after re-rendering against the old power reference."""
    originals = [copy.deepcopy(c.get('effects')) for c in (a, b)]
    for i, clip in enumerate((a, b)):
        offset = length-overlap if i==0 else 0
        points = [dict(time=p['time']+offset, gain_db=p['gain_db']) for p in before['trim_points']]
        if offset>0:
            points.insert(0, dict(time=0, gain_db=0))
        clip.setdefault('effects', []).append(dict(type='eq', band='low', points=points))
    after = probe_low_overlap(a, b, tracks, overlap, target_bpm)
    accepted, high, low = False, None, None
    if after['status']=='measured' and len(after['frames'])==len(before['frames']):
        peak = max(f['expected_power'] for f in before['frames'])
        ratios = [10*math.log10(max(y['combined_power']/x['expected_power'], 1e-8))
                  for x,y in zip(before['frames'], after['frames']) if x['expected_power']>=max(1e-10, peak*.1)]
        high, low = max(ratios), min(ratios)
        accepted = high < before['peak_sum_excess_db']-.25 and low>=-1.5
    if not accepted:
        for clip, effects in zip((a, b), originals):
            if effects is None:
                clip.pop('effects', None)
            else:
                clip['effects'] = effects
    return dict(applied=accepted, after=probe_summary(after),
                reference_peak_excess_db=round(high, 4) if high is not None else None,
                reference_minimum_db=round(low, 4) if low is not None else None,
                reason='共同低频补偿已复测，峰值堆积减少且未明显抽空' if accepted else
                       '补偿未取得可靠改善，已撤回新增补偿并保留原曲线')
