"""Bounded melody-rise and pulse evidence for a user-requested acappella drop.

No lyric, emotional, downbeat, phrase or harmonic-compatibility inference.
"""
import math
from pathlib import Path

import numpy as np

from .analysis import INPUT_OPTIONS
from .runtime import run_ffmpeg

WINDOW_SECONDS = 24
SAMPLE_RATE = 22050
HOP = 256


def select_cue(frames, pulses, duration):
    usable = [f for f in frames if f['midi'] is not None and
              math.isfinite(f['midi']) and f['probability'] >= .25 and f['rms'] >= .003]
    times = np.array([f['time'] for f in usable])
    pitches = np.array([f['midi'] for f in usable])

    def evidence(at):
        before = pitches[(times >= at - .55) & (times <= at - .12)]
        after = pitches[(times >= at + .08) & (times <= at + .6)]
        if min(len(before), len(after)) < 20:
            return None
        rise = float(np.median(after) - np.median(before))
        if 1.8 <= rise <= 7:
            return {'entry_seconds': round(float(at), 6), 'rise_semitones': round(rise, 2),
                    'before_frames': len(before), 'after_frames': len(after)}
        return None

    candidates = []
    for at in np.arange(2, min(13, duration - .65) + .001, .125):
        candidate = evidence(at)
        if candidate:
            candidates.append(candidate)
    intervals = np.diff(pulses)
    intervals = intervals[(intervals >= .25) & (intervals <= 1)]
    interval = round(float(np.median(intervals)), 6) if len(intervals) >= 3 else None
    if not candidates:
        return {'status': 'insufficient_evidence', 'entry_seconds': round(min(4, duration * .4), 6),
                'rise_semitones': None, 'pulse_interval_seconds': interval, 'candidates': []}
    def rank(candidate):
        distance=min(abs(t-candidate['entry_seconds']) for t in pulses) if pulses else 0
        return (candidate['rise_semitones'], -distance, min(candidate['before_frames'], candidate['after_frames']))
    best = max(candidates, key=rank)
    if pulses:
        nearest = min(pulses, key=lambda t: abs(t - best['entry_seconds']))
        if abs(nearest - best['entry_seconds']) <= .12:
            snapped = evidence(nearest)
            if snapped:
                best = snapped
    return {**best, 'status': 'measured_rise', 'pulse_interval_seconds': interval,
            'candidates': sorted(candidates, key=lambda c: -c['rise_semitones'])[:6]}


def decode_window(path):
    # Select the more energetic channel after decoding instead of summing an
    # anti-phase stereo source to silence. Analysis only; never change the audio.
    result = run_ffmpeg([*INPUT_OPTIONS, '-i', Path(path), '-t', str(WINDOW_SECONDS),
                         '-ac', '2', '-ar', str(SAMPLE_RATE), '-f', 'f32le', 'pipe:1'])
    stereo = np.frombuffer(result.stdout, dtype='<f4').reshape(-1, 2)
    channel = int(np.argmax(np.mean(stereo.astype(float) ** 2, axis=0)))
    return stereo[:, channel].copy()


def pulse_times(samples, librosa):
    if len(samples) < SAMPLE_RATE * 2 or np.max(np.abs(samples)) < 1e-5:
        return []
    onset = librosa.onset.onset_strength(y=samples, sr=SAMPLE_RATE, hop_length=HOP)
    if not len(onset) or np.max(onset) < .01:
        return []
    _, beats = librosa.beat.beat_track(onset_envelope=onset, sr=SAMPLE_RATE, hop_length=HOP)
    return [round(float(t), 6) for t in librosa.frames_to_time(beats, sr=SAMPLE_RATE, hop_length=HOP)]


def analyze_cue(vocal_path, instrumental_path, pulse_path=None):
    try:
        import librosa
    except ImportError:
        raise RuntimeError('旋律分析依赖未就绪，请安装更新后的 requirements-audio.txt；普通接歌与手动选点仍可用。')
    vocal = decode_window(vocal_path)
    instrumental = decode_window(instrumental_path)
    duration = len(vocal) / SAMPLE_RATE
    frames = []
    if len(vocal) >= 2048 and np.max(np.abs(vocal)) >= .003:
        f0, voiced, probability = librosa.pyin(vocal, sr=SAMPLE_RATE, fmin=librosa.note_to_hz('C2'),
            fmax=librosa.note_to_hz('C6'), frame_length=2048, hop_length=HOP)
        rms = librosa.feature.rms(y=vocal, frame_length=2048, hop_length=HOP)[0]
        for i, frequency in enumerate(f0):
            frames.append({'time': i * HOP / SAMPLE_RATE,
                'midi': float(librosa.hz_to_midi(frequency)) if voiced[i] and np.isfinite(frequency) else None,
                'probability': float(probability[i]), 'rms': float(rms[i])})
    pulses_a = pulse_times(decode_window(pulse_path) if pulse_path else vocal, librosa)
    pulses_b = pulse_times(instrumental, librosa)
    result = select_cue(frames, pulses_a, duration)
    b_start = pulses_b[0] if pulses_b and pulses_b[0] <= len(instrumental) / SAMPLE_RATE - .25 else 0
    result.update(b_start_seconds=b_start, analyzed_seconds=round(duration, 3),
        algorithm='librosa.pyin_and_estimated_pulses', analysis_version=1,
        pulse_source='vocal_parent_mix' if pulse_path else 'vocal',
        warnings=['仅分析开头最多24秒；候选拍点不等于小节强拍或乐句起点。',
                  '音高上行不等于情绪或歌词识别；两曲和声及分轨残留仍须试听。'])
    return result
