"""Conservative monophonic-vocal pitch events and source-sample Tone Play.

pYIN is not a polyphonic transcription tool. Only an explicitly independent
vocal track is eligible; no notes are inferred from a full mix or global key.
"""
import math
from pathlib import Path

import numpy as np

from .analysis import INPUT_OPTIONS
from .performance_audio import validate_performance
from .runtime import prepare_dependencies, run_ffmpeg

RATE = 16000
HOP = 256
FRAME = 2048
MAX_SECONDS = 8
MIN_NOTE = .08


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _limitations():
    return ['仅检查明确独立人声的前最多8秒，不把整曲和弦或全局调性当旋律。',
            'pYIN单声部估计可能受合唱、残留伴奏、滑音与八度误判影响，音高与时序须试听。',
            '音符量化为整数半音；采样重复保持目标节奏轮廓，不还原发音、歌词与演唱表现。',
            '原貌锁及人声改造许可由调用者与最终工程校验负责。']


def _pitch_frames(y):
    prepare_dependencies()
    import librosa
    f0, voiced, probability = librosa.pyin(y, sr=RATE, fmin=65.4064, fmax=1046.5023,
        frame_length=FRAME, hop_length=HOP, n_thresholds=50)
    energy = librosa.feature.rms(y=y, frame_length=FRAME, hop_length=HOP)[0]
    frames = []
    for index in range(min(len(f0), len(energy), len(voiced), len(probability))):
        frequency = float(f0[index])
        frames.append({'time': index * HOP / RATE,
            'midi': 69 + 12 * math.log2(frequency / 440) if bool(voiced[index]) and math.isfinite(frequency) and frequency > 0 else None,
            'probability': float(probability[index]), 'rms': float(energy[index])})
    return frames


def _stable_notes(frames, duration):
    notes, run = [], []
    current = None

    def finish():
        if not run:
            return
        start, end = run[0]['time'], min(duration, run[-1]['time'] + HOP / RATE)
        values = [frame['midi'] for frame in run]
        confidence = float(np.mean([frame['probability'] for frame in run]))
        if end - start >= MIN_NOTE and float(np.std(values)) <= .20 and confidence >= .80:
            notes.append({'start': round(start, 6), 'end': round(end, 6), 'midi': int(round(float(np.median(values)))),
                          'confidence': round(confidence, 6), 'pitch_spread_semitones': round(float(np.std(values)), 6)})

    for frame in frames:
        midi = frame.get('midi')
        valid = (_finite(midi) and _finite(frame.get('probability')) and _finite(frame.get('rms'))
                 and frame['probability'] >= .80 and frame['rms'] >= .003
                 and abs(midi - round(midi)) <= .30 and 0 <= frame['time'] < duration)
        pitch = int(round(midi)) if valid else None
        if pitch != current or not valid:
            finish()
            run = []
        if valid:
            run.append(frame)
        current = pitch
    finish()
    if notes:
        first = notes[0]['midi']
        for note in notes:
            note['relative_semitones'] = note['midi'] - first
    return notes


def analyze_melody(track):
    """Return only stable pYIN note candidates from a real independent vocal."""
    report = {'status': 'unknown', 'notes': [], 'analyzed_seconds': 0., 'energy': 0.,
              'reason': '没有可靠单声部音高证据', 'limitations': _limitations()}
    if not isinstance(track, dict) or track.get('stem_role') != 'vocal':
        report['reason'] = '需要明确独立人声，不能从整曲或伴奏冒充单旋律'
        return report
    duration = track.get('duration')
    if not _finite(duration) or duration < MIN_NOTE or not track.get('path'):
        report['reason'] = '独立人声没有有效音频范围'
        return report
    try:
        decoded = run_ffmpeg([*INPUT_OPTIONS, '-i', Path(track['path']), '-t', f'{min(MAX_SECONDS, duration):.9f}',
                              '-ar', str(RATE), '-ac', '2', '-f', 'f32le', 'pipe:1'])
        samples = np.frombuffer(decoded.stdout, dtype='<f4')
        if len(samples) < 2 or not np.isfinite(samples).all():
            return report
        samples = samples[:len(samples) - len(samples) % 2].reshape(-1, 2)
        y = samples[:, int(np.argmax(np.mean(samples.astype(float) ** 2, axis=0)))].copy()
        report['analyzed_seconds'] = round(len(y) / RATE, 6)
        report['energy'] = round(float(np.sqrt(np.mean(y.astype(float) ** 2))), 8)
        if report['energy'] < .003 or len(y) < FRAME:
            return report
        report['notes'] = _stable_notes(_pitch_frames(y), len(y) / RATE)
        if report['notes']:
            report.update(status='estimated', reason='取得稳定单声部音高候选，仍须试听核对')
    except Exception:
        report.update(status='unavailable', reason='本地单旋律分析依赖失败，未生成音符证据')
    return report


def tone_play_candidate(source_track, target_vocal_track, duration, entry=0):
    """Build valid performance.events using a measured source pitch and melody.

    The first stable target note starts at entry; later onsets retain their
    measured relative timing. Source samples are looped in chunks of <=250ms.
    This returns a candidate and never changes either audio file.
    """
    result = {'available': False, 'reason': '缺少可信采样音高或目标旋律',
              'performance': {'events': [], 'drums': []}, 'source_midi': None,
              'target_notes': [], 'limitations': _limitations()}
    if not _finite(duration) or duration <= 0 or not _finite(entry) or not 0 <= entry < duration:
        result['reason'] = 'Tone Play目标时长或切入时间无效'
        return result
    if not all(isinstance(track, dict) and isinstance(track.get('id'), str) and track['id'] for track in (source_track, target_vocal_track)):
        return result
    source, target = analyze_melody(source_track), analyze_melody(target_vocal_track)
    source_notes, target_notes = source['notes'], target['notes']
    if source['status'] != 'estimated' or target['status'] != 'estimated' or not source_notes or len(target_notes) < 2:
        return result
    # This is a measured sample pitch, not a tonal root or a chord label.
    root = max(source_notes, key=lambda note: (note['end'] - note['start']) * note['confidence'])
    sample_start = root['start'] + .01
    sample_length = min(.25, root['end'] - .01 - sample_start)
    if sample_length < .01:
        return result
    first_time = target_notes[0]['start']
    if entry + target_notes[-1]['end'] - first_time > duration + 1e-9:
        result['reason'] = '目标时间线容纳不了实测旋律片段'
        return result
    events = []
    for note in target_notes:
        shift = int(note['midi'] - root['midi'])
        if not -12 <= shift <= 12:
            result['reason'] = '实测旋律超出整数正负12半音取样范围'
            return result
        note_length = note['end'] - note['start']
        cue_length = min(sample_length, note_length)
        repetitions = int(math.floor((note_length + 1e-9) / cue_length))
        consumed = 0
        while consumed < repetitions:
            repeat = min(16, repetitions - consumed)
            events.append({'track_id': source_track['id'], 'start': sample_start, 'end': sample_start + cue_length,
                'time': entry + note['start'] - first_time + consumed * cue_length,
                'gain_db': -12., 'semitones': shift, 'repeats': repeat, 'interval': cue_length})
            consumed += repeat
        remainder = note_length - repetitions * cue_length
        if remainder >= .01:
            events.append({'track_id': source_track['id'], 'start': sample_start, 'end': sample_start + remainder,
                'time': entry + note['start'] - first_time + repetitions * cue_length,
                'gain_db': -12., 'semitones': shift, 'repeats': 1, 'interval': remainder})
    if not events or len(events) > 64:
        result['reason'] = '实测旋律无法在有限取样事件内可靠表达'
        return result
    try:
        performance = validate_performance({'events': events, 'drums': []},
            {source_track['id']: source_track, target_vocal_track['id']: target_vocal_track}, duration)
    except (ValueError, TypeError, KeyError):
        result['reason'] = '实测旋律事件未通过取样时间范围校验'
        return result
    result.update(available=True, reason='使用实际独立人声音高生成逐音取样候选，未执行音频改造',
                  performance=performance, source_midi=root['midi'], target_notes=target_notes)
    return result
