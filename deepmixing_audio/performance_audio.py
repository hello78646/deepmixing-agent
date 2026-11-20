"""Validated, local Cue/sample loops, pitched notes and deterministic drums.

Only track IDs resolve source files. These events are signal operations, not
semantic lyric slicing, an instrument transcription, or a cultural style model.
"""
import math
import json
import os
from pathlib import Path
import re
import tempfile
import wave

import numpy as np

from .analysis import INPUT_OPTIONS
from .runtime import run_ffmpeg

RATE = 44100
_DRUM_LENGTHS = {'kick': .35, 'snare': .22, 'hat': .08}
_EVENT_FIELDS = {'track_id', 'start', 'end', 'time', 'gain_db', 'semitones'}
_BATCH_SIZE = 16


def _number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + '须为有限数字')
    if not low <= value <= high:
        raise ValueError(label + '超出支持范围')
    return float(value)


def _integer(value, label, low, high):
    result = _number(value, label, low, high)
    if int(result) != result:
        raise ValueError(label + '须为整数')
    return int(result)


def validate_performance(raw, tracks, duration, locked_ids=()):
    """Return canonical numeric events, rejecting unknown fields and sources."""
    duration = _number(duration, '基底时长', .01, 86400)
    if not isinstance(raw, dict) or set(raw) - {'events', 'drums'}:
        raise ValueError('performance字段无效')
    events, drums = raw.get('events', []), raw.get('drums', [])
    if not isinstance(events, list) or len(events) > 64:
        raise ValueError('取样事件须为最多64项的数组')
    if not isinstance(drums, list) or len(drums) > 128:
        raise ValueError('鼓事件须为最多128项的数组')
    locked = set(locked_ids)
    canonical = {'events': [], 'drums': []}
    for event in events:
        if not isinstance(event, dict) or not _EVENT_FIELDS <= set(event) or set(event) - _EVENT_FIELDS - {'repeats', 'interval', 'gain_envelope', 'lowpass_hz'}:
            raise ValueError('取样事件字段无效')
        track_id = event['track_id']
        if not isinstance(track_id, str) or track_id not in tracks:
            raise ValueError('取样事件引用未知素材')
        if track_id in locked:
            raise ValueError('保持原貌已锁定：不能取样改造')
        source_duration = _number(tracks[track_id].get('duration'), '源素材时长', .01, 86400)
        start = _number(event['start'], '取样开始', 0, source_duration)
        end = _number(event['end'], '取样结束', 0, source_duration)
        length = end - start
        if length < .01 - 1e-9:
            raise ValueError('每次取样须至少10毫秒')
        time = _number(event['time'], '事件时间', 0, duration)
        gain = _number(event['gain_db'], '取样增益', -48, 0)
        semitones = _integer(event['semitones'], '取样移调', -12, 12)
        repeats = _integer(event.get('repeats', 1), '重复次数', 1, 16)
        interval = _number(event.get('interval', length), '重复间隔', .01, duration)
        if time + (repeats - 1) * interval + length > duration + 1e-9:
            raise ValueError('取样事件超出基底时长')
        row={'track_id': track_id, 'start': start, 'end': end,
            'time': time, 'gain_db': gain, 'semitones': semitones, 'repeats': repeats, 'interval': interval}
        if 'lowpass_hz' in event:
            if tracks[track_id].get('stem_role')!='drums':
                raise ValueError('取样低通只用于真实鼓分轨，不能把完整混音或人声当鼓处理')
            row['lowpass_hz']=_number(event['lowpass_hz'],'鼓取样低通频率',80,8000)
        if 'gain_envelope' in event:
            points=event['gain_envelope']
            if not isinstance(points,list) or not 1<=len(points)<=16:raise ValueError('取样包络需要1至16个点')
            envelope=[]
            for point in points:
                if not isinstance(point,dict) or set(point)!={'time','gain'}:raise ValueError('取样包络字段无效')
                at=_number(point['time'],'取样包络时间',0,length)
                level=_number(point['gain'],'取样包络增益',0,1)
                if envelope and at-envelope[-1]['time']<.005-1e-9:raise ValueError('取样包络点须递增')
                envelope.append(dict(time=at,gain=level))
            row['gain_envelope']=envelope
        canonical['events'].append(row)
    for drum in drums:
        if not isinstance(drum, dict) or set(drum) != {'type', 'time', 'gain_db'}:
            raise ValueError('鼓事件字段无效')
        if not isinstance(drum['type'], str) or drum['type'] not in _DRUM_LENGTHS:
            raise ValueError('不支持的鼓类型')
        at = _number(drum['time'], '鼓事件时间', 0, duration)
        if at >= duration:
            raise ValueError('鼓事件须位于基底范围内')
        canonical['drums'].append({'type': drum['type'], 'time': at,
            'gain_db': _number(drum['gain_db'], '鼓事件增益', -48, 0)})
    return canonical


def _write_drum(path, kind):
    n = round(_DRUM_LENGTHS[kind] * RATE)
    t = np.arange(n) / RATE
    rng = np.random.default_rng({'kick': 7, 'snare': 11, 'hat': 17}[kind])
    if kind == 'kick':
        # Exponential downward chirp with a short attack and decaying body.
        phase = 2 * math.pi * (48 * t + 100 * .025 * (1 - np.exp(-t / .025)))
        signal = .8 * np.sin(phase) * np.exp(-t / .09)
    elif kind == 'snare':
        noise = rng.standard_normal(n)
        noise = np.concatenate(([0.], np.diff(noise))) / 4
        signal = (.6 * noise + .25 * np.sin(2 * math.pi * 180 * t)) * np.exp(-t / .045)
    else:
        noise = rng.standard_normal(n)
        noise = np.concatenate(([0.], np.diff(noise))) / 5
        signal = .65 * noise * np.exp(-t / .013)
    ramp = min(round(.001 * RATE), n // 2)
    signal[:ramp] *= np.linspace(0, 1, ramp)
    signal[-ramp:] *= np.linspace(1, 0, ramp)
    samples = np.repeat(np.clip(signal[:, None], -.95, .95), 2, axis=1)
    with wave.open(str(path), 'wb') as out:
        out.setparams((2, 2, RATE, 0, 'NONE', ''))
        out.writeframes((samples * 32767).astype('<i2').tobytes())


def _render_cue(path, event, output):
    length = event['end'] - event['start']
    filters = [f'atrim=duration={length:.9f}', 'asetpts=PTS-STARTPTS', 'aresample=44100',
               'aformat=sample_fmts=fltp:channel_layouts=stereo']
    if event['semitones']:
        filters.append(f"rubberband=tempo=1:pitch={2 ** (event['semitones'] / 12):.12f}")
    if 'lowpass_hz' in event:
        filters.append(f"lowpass=f={event['lowpass_hz']:.9f}:p=2")
    samples = round(length * RATE)
    filters += [f"volume={event['gain_db']:.9f}dB", f'apad=whole_len={samples}',
                f'atrim=end_sample={samples}', 'afade=t=in:st=0:d=0.005',
                f'afade=t=out:st={max(0, length - .005):.9f}:d=0.005']
    if event.get('gain_envelope'):
        from .engine import _envelope_expression
        expr=_envelope_expression(event['gain_envelope'])
        filters.append(f'aeval=val(0)*({expr})|val(1)*({expr}):c=stereo')
    run_ffmpeg([*INPUT_OPTIONS, '-ss', f"{event['start']:.9f}", '-i', path,
                '-t', f'{length:.9f}', '-af', ','.join(filters), '-ar', str(RATE), '-ac', '2',
                '-c:a', 'pcm_f32le', '-y', output], timeout=600)


def _true_peak(path):
    result = run_ffmpeg([*INPUT_OPTIONS, '-i', path, '-af', 'ebur128=peak=true', '-f', 'null', '-'], loglevel='info')
    summary = result.stderr.decode(errors='replace').split('Summary:')[-1]
    match = re.search(r'Peak:\s*([-+\d.inf]+)\s*dBFS', summary)
    if match:
        value = float(match.group(1))
        if math.isfinite(value):
            return value
    return None


def mix_performance(base_wav, performance, tracks, output_wav):
    """Overlay validated events with bounded FFmpeg batches; preserve the base."""
    base, output = Path(base_wav).resolve(), Path(output_wav).resolve()
    protected = {base}
    protected.update(Path(track['path']).resolve() for track in tracks.values() if track.get('path'))
    if output in protected:
        raise ValueError('不能覆盖基底或原始素材')
    with wave.open(str(base)) as source:
        duration = source.getnframes() / source.getframerate()
    canonical = validate_performance(performance, tracks, duration)
    samples = round(duration * RATE)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='performance-', dir=str(output.parent)) as temporary:
        folder = Path(temporary)
        bus = folder / 'bus-0.wav'
        run_ffmpeg([*INPUT_OPTIONS, '-i', base, '-af',
                    f'aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,apad=whole_len={samples},atrim=end_sample={samples},asetpts=PTS-STARTPTS',
                    '-ar', str(RATE), '-ac', '2', '-c:a', 'pcm_f32le', '-y', bus], timeout=600)
        placements = []
        cue_cache = {}
        for index, event in enumerate(canonical['events']):
            cache_key = tuple(event[key] for key in ('track_id', 'start', 'end', 'gain_db', 'semitones'))
            cache_key += (json.dumps(event.get('gain_envelope'),sort_keys=True),)
            cache_key += (event.get('lowpass_hz'),)
            if cache_key not in cue_cache:
                cue = folder / f'cue-{index}.wav'
                _render_cue(tracks[event['track_id']]['path'], event, cue)
                cue_cache[cache_key] = cue
            cue = cue_cache[cache_key]
            for repeat in range(event['repeats']):
                placements.append((cue, event['time'] + repeat * event['interval'], 0.))
        drum_paths = {}
        for drum in canonical['drums']:
            kind = drum['type']
            if kind not in drum_paths:
                drum_paths[kind] = folder / f'{kind}.wav'
                _write_drum(drum_paths[kind], kind)
            placements.append((drum_paths[kind], drum['time'], drum['gain_db']))
        for offset in range(0, len(placements), _BATCH_SIZE):
            batch = placements[offset:offset + _BATCH_SIZE]
            arguments = [*INPUT_OPTIONS, '-i', bus]
            graph = []
            for index, (path, at, gain) in enumerate(batch, 1):
                arguments.extend([*INPUT_OPTIONS, '-i', path])
                graph.append(f'[{index}:a:0]volume={gain:.9f}dB,adelay={round(at * RATE)}S:all=1[e{index}]')
            inputs = '[0:a:0]' + ''.join(f'[e{index}]' for index in range(1, len(batch) + 1))
            graph.append(inputs + f'amix=inputs={len(batch) + 1}:duration=first:normalize=0,atrim=end_sample={samples}[out]')
            following = folder / f'bus-{offset + 1}.wav'
            run_ffmpeg([*arguments, '-filter_complex', ';'.join(graph), '-map', '[out]',
                        '-ar', str(RATE), '-ac', '2', '-c:a', 'pcm_f32le', '-y', following], timeout=600)
            # Buses can be long; retain only the next completed accumulation.
            bus.unlink()
            bus = following
        final = folder / 'final.wav'
        run_ffmpeg([*INPUT_OPTIONS, '-i', bus, '-af',
                    f'alimiter=limit=0.84:level=false:latency=true,apad=whole_len={samples},atrim=end_sample={samples}',
                    '-ar', str(RATE), '-ac', '2', '-c:a', 'pcm_s16le', '-y', final], timeout=600)
        true_peak = _true_peak(final)
        if true_peak is not None and true_peak > -1.5:
            attenuated = folder / 'attenuated.wav'
            run_ffmpeg([*INPUT_OPTIONS, '-i', final, '-af', f'volume={-1.6 - true_peak:.6f}dB',
                        '-c:a', 'pcm_s16le', '-y', attenuated], timeout=600)
            final = attenuated
            true_peak = _true_peak(final)
        with wave.open(str(final)) as source:
            frames = source.getnframes()
            rate, channels = source.getframerate(), source.getnchannels()
            peak = 0
            while True:
                chunk = source.readframes(RATE)
                if not chunk:
                    break
                peak = max(peak, int(np.max(np.abs(np.frombuffer(chunk, dtype='<i2').astype(np.int32)))))
        if frames != samples or rate != RATE or channels != 2 or (true_peak is not None and true_peak > -1.5):
            raise RuntimeError('演奏叠加未通过时长、声道或峰值验证')
        os.replace(final, output)
    return {'duration': round(samples / RATE, 6), 'sample_rate': RATE, 'channels': 2,
            'event_count': sum(event['repeats'] for event in canonical['events']),
            'drum_count': len(canonical['drums']),
            'peak_dbfs': round(20 * math.log10(peak / 32768), 4) if peak else None,
            'true_peak_dbtp': true_peak}
