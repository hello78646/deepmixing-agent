"""Local selected-region beat evidence, harmonic-key candidates and change points.

Estimates are advisory. They do not identify true downbeats, semantic phrases,
lyrics or guarantee harmonic compatibility. Original audio is never rewritten.
"""
import math
from pathlib import Path

import numpy as np

from .analysis import INPUT_OPTIONS
from .runtime import run_ffmpeg

RATE = 22050
HOP = 512
MAX_SECONDS = 60
ANALYSIS_VERSION = 10
# A firmer dynamic-programming prior reduces beat-to-beat wandering in sparse
# guitar and microphone recordings. It does not resolve half/double tempo or
# establish a downbeat; those remain explicit review evidence.
BEAT_TIGHTNESS = 200
NOTES = ('C', 'C♯', 'D', 'E♭', 'E', 'F', 'F♯', 'G', 'A♭', 'A', 'B♭', 'B')
PROFILES = {
    'major': np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]),
    'minor': np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]),
}


def key_name(tonic, mode):
    return NOTES[tonic] + (' 大调' if mode == 'major' else ' 小调')


def estimate_key(chroma, harmonic_share):
    unknown = {'status': 'unknown', 'tonic': None, 'mode': None, 'name': '调性未确定', 'candidates': []}
    if chroma.ndim != 2 or chroma.shape[0] != 12 or chroma.shape[1] < 8 or harmonic_share < .22:
        return unknown
    profile = np.mean(chroma, axis=1)
    total = float(profile.sum())
    if total <= 1e-8:
        return unknown
    weights = profile / total
    if np.max(weights) > .72 or np.count_nonzero(weights > .035) < 4 or np.std(weights) < .008:
        return unknown
    centered = profile - profile.mean()
    scores = []
    for mode, template in PROFILES.items():
        for tonic in range(12):
            reference = np.roll(template, tonic)
            reference = reference - reference.mean()
            score = float(np.dot(centered, reference) / (np.linalg.norm(centered) * np.linalg.norm(reference)))
            scores.append({'tonic': tonic, 'mode': mode, 'name': key_name(tonic, mode), 'profile_score': round(score, 4)})
    scores.sort(key=lambda item: -item['profile_score'])
    gap = round(scores[0]['profile_score'] - scores[1]['profile_score'], 4)
    status = 'estimated' if scores[0]['profile_score'] >= .55 and gap >= .035 else 'unknown'
    return {'status': status, 'tonic': scores[0]['tonic'] if status == 'estimated' else None,
            'mode': scores[0]['mode'] if status == 'estimated' else None,
            'name': scores[0]['name'] if status == 'estimated' else '调性未确定',
            'candidates': scores[:3], 'score_margin': gap,
            'method': 'harmonic_chroma_profile_correlation',
            'limitation': '相关分数不是正确率；短清唱、转调、调式或和弦变化可能使候选不稳定。'}


def compare_keys(a, b, a_shift=0, b_shift=0):
    unknown = {'relation': 'unknown', 'message': '调性依据不足或使用了非整数移调，先试听判断。'}
    if any(k.get('status') not in ('estimated', 'user') or k.get('mode') not in PROFILES
           or isinstance(k.get('tonic'), bool) or not isinstance(k.get('tonic'), int)
           or not 0 <= k['tonic'] < 12 for k in (a, b)):
        return unknown
    if any(isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s) or s != int(s) for s in (a_shift, b_shift)):
        return unknown
    left, right = (a['tonic'] + int(a_shift)) % 12, (b['tonic'] + int(b_shift)) % 12
    delta = (right - left) % 12
    if a['mode'] == b['mode'] and delta == 0:
        return {'relation': 'same', 'message': '候选调性相同，可先试原调叠搭；和弦及旋律仍需核对。'}
    relative = (a['mode'] == 'major' and b['mode'] == 'minor' and delta == 9 or
                a['mode'] == 'minor' and b['mode'] == 'major' and delta == 3)
    if relative:
        return {'relation': 'relative', 'message': '候选为关系大小调，可作为叠搭起点，仍需试听和弦是否冲突。'}
    if a['mode'] == b['mode'] and delta in (5, 7):
        return {'relation': 'neighbor', 'message': '候选位于五度关系邻近位置，适合先试；不保证整段和声兼容。'}
    return {'relation': 'review', 'message': '候选调性距离较远，建议缩短叠加、换片段或先试移调；不会自动改变人声。'}


def change_candidates(chroma, rms, hop_seconds, start, end, timbre=None):
    from .music_structure import structure_changes
    return structure_changes(chroma,rms,hop_seconds,start,end,timbre)


def _tempo_layer_choice(onset, tempo, librosa):
    """Choose a clearly supported half/double tempo layer, conservatively.

    A beat tracker can lock to every other pulse. We inspect the averaged
    tempogram only to expose a strong octave relation; a close relation stays
    ambiguous and keeps the original tracker result. This is evidence for the
    grid, never a downbeat claim.
    """
    base = float(tempo)
    evidence = {'base_bpm': round(base, 3), 'selected_bpm': round(base, 3),
                'layer': 'base', 'review_required': True}
    if not 45 <= base <= 220:
        return base, evidence
    try:
        tempogram = librosa.feature.tempogram(onset_envelope=onset, sr=RATE, hop_length=HOP)
        autocorrelation = np.mean(np.asarray(tempogram, dtype=float), axis=1)
        def score(bpm):
            lag = int(round(60.0 * RATE / (max(float(bpm), 1e-6) * HOP)))
            return float(autocorrelation[lag]) if 1 <= lag < len(autocorrelation) else 0.0
        base_score = score(base)
        aliases = {'half': base / 2.0, 'double': base * 2.0}
        evidence['tempogram_scores'] = {'base': round(base_score, 4),
                                        'half': round(score(aliases['half']), 4),
                                        'double': round(score(aliases['double']), 4)}
        # A double layer is only considered for an unusually low base tempo;
        # this avoids turning ordinary eighth-note activity into false beats.
        double_score = evidence['tempogram_scores']['double']
        if (45 <= base <= 110 and aliases['double'] <= 240 and
                double_score >= base_score + .03 and
                double_score >= max(.08, base_score * 1.05)):
            selected = aliases['double']
            evidence.update(selected_bpm=round(selected, 3), layer='double_candidate',
                            review_required=True,
                            reason='双倍速度层的平均 tempogram 证据明显高于当前层，仍需人工确认')
            return selected, evidence
        # A very fast tracker result can similarly be an eighth-note layer.
        half_score = evidence['tempogram_scores']['half']
        if (base >= 180 and aliases['half'] >= 40 and
                half_score >= base_score + .04 and
                half_score >= max(.08, base_score * 1.08)):
            selected = aliases['half']
            evidence.update(selected_bpm=round(selected, 3), layer='half_candidate',
                            review_required=True,
                            reason='半速层的平均 tempogram 证据明显高于当前层，仍需人工确认')
            return selected, evidence
    except (TypeError, ValueError, IndexError, FloatingPointError):
        evidence['tempogram_unavailable'] = True
    return base, evidence


def _phase_drift_evidence(onset, beat_frames, start=0.0):
    """Measure phase support and local period drift without forcing a shift."""
    frames = np.asarray(beat_frames, dtype=float)
    unknown = {'phase_offset_seconds': 0.0, 'phase_margin': 0.0,
               'drift_ratio': None, 'correction_applied': False,
               'auto_phase_shift_seconds': 0.0,
               'phase_policy': 'earliest_strong_beat',
               'review_required': True}
    if frames.ndim != 1 or len(frames) < 8 or not np.isfinite(frames).all():
        return unknown
    intervals = np.diff(frames) * HOP / RATE
    if len(intervals) < 4 or not np.isfinite(intervals).all() or np.any(intervals <= 0):
        return unknown
    period = float(np.median(intervals))
    # Support is sampled around the measured grid. A strong winner can be
    # shown to a reviewer, but is not silently applied to source timestamps.
    offsets = np.linspace(-period / 2.0, period / 2.0, 9)
    frame_offsets = offsets * RATE / HOP
    support = []
    for offset in frame_offsets:
        shifted = np.rint(frames + offset).astype(int)
        values = []
        for frame in shifted:
            left, right = max(0, frame - 1), min(len(onset), frame + 2)
            values.append(float(np.max(onset[left:right])) if right > left else 0.0)
        support.append(float(np.mean(values)))
    order = np.argsort(-np.asarray(support))
    best = int(order[0])
    second = float(support[int(order[1])]) if len(order) > 1 else 0.0
    baseline = max(float(np.mean(support)), 1e-9)
    phase_margin = (float(support[best]) - second) / baseline
    phase_candidates = [
        {'offset_seconds': round(float(offsets[index]), 6),
         'support': round(float(support[index]), 4),
         'rank': rank + 1}
        for rank, index in enumerate(order[:3])
    ]
    strongest = float(support[best])
    earliest = None
    # Apply the user's earliest-strong-beat rule only when two phase options
    # are close and the earlier option remains inside the selected region.
    for index in sorted(order, key=lambda value: offsets[value]):
        candidate_time = float(frames[0] * HOP / RATE + offsets[index])
        if candidate_time < -1e-6 or float(support[index]) < strongest * .78:
            continue
        earliest = float(offsets[index])
        break
    auto_shift = earliest if earliest is not None and phase_margin < .45 else 0.0
    chunks = np.array_split(frames, 3)
    local_bpms = []
    for chunk in chunks:
        if len(chunk) < 3:
            continue
        local_period = float(np.median(np.diff(chunk)) * HOP / RATE)
        local_bpms.append(float(round(60.0 / local_period, 3)) if local_period > 0 else None)
    drift_ratio = None
    if len(local_bpms) >= 2 and local_bpms[0] and local_bpms[-1]:
        drift_ratio = float(round((local_bpms[-1] - local_bpms[0]) / max(float(np.mean(local_bpms)), 1e-9), 4))
    return {
        'phase_offset_seconds': round(float(offsets[best]), 6),
        'phase_margin': round(float(phase_margin), 4),
        'phase_support': [round(float(value), 4) for value in support],
        'phase_candidates': phase_candidates,
        'auto_phase_shift_seconds': round(float(auto_shift), 6),
        'phase_policy': 'earliest_strong_beat',
        'drift_ratio': drift_ratio,
        'local_bpms': local_bpms,
        'correction_applied': False,
        'review_required': bool(phase_margin < .12 or drift_ratio is None or abs(drift_ratio) > .04),
        'limitation': '相位和漂移只提供复核证据；未把事后平移或弹性校正写回原音频。',
    }


def analyze_region(path, start, end):
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (start,end)) or start<0 or not .25<=end-start<=MAX_SECONDS+.001:
        raise ValueError('检查区域须为有效的0.25至60秒音频')
    try:
        import librosa
    except ImportError:
        raise RuntimeError('音乐检查依赖未就绪，请安装 requirements-audio.txt；仍可手动选段试听。')
    decoded = run_ffmpeg([*INPUT_OPTIONS, '-ss', f'{start:.9f}', '-i', Path(path), '-t', f'{end-start:.9f}',
                          '-ac', '2', '-ar', str(RATE), '-f', 'f32le', 'pipe:1'])
    stereo = np.frombuffer(decoded.stdout, dtype='<f4').reshape(-1, 2)
    if not len(stereo):
        raise ValueError('所选区域没有可解码音频')
    y = stereo[:, int(np.argmax(np.mean(stereo.astype(float) ** 2, axis=0)))].copy()
    size = min(480, len(y))
    boundaries = np.linspace(0, len(y), size + 1).astype(int)
    waveform = [round(float(np.max(np.abs(y[boundaries[i]:boundaries[i+1]]))), 5) for i in range(size)]
    grid = {'status': 'unknown', 'bpm': None, 'anchor': start, 'interval_variation': None}
    report = {'start': start, 'end': round(start + len(y) / RATE, 6), 'waveform': waveform,
              'beats': [], 'grid': grid, 'key_windows':{'status':'unknown','windows':[]}, 'key': {'status': 'unknown', 'tonic': None, 'mode': None,
              'name': '调性未确定', 'candidates': []}, 'changes': [], 'phrase_changes':[], 'tempo_changes':[], 'beat_drift_candidates':[], 'repetition_candidates':[], 'bar_anchor_candidates':[], 'analysis_version': ANALYSIS_VERSION,
              'limitations': ['检测区域最多60秒；网格和结构变化是候选，不是自动确认的小节强拍或完整乐句。',
                              '结构变化核对约1.2/2.4秒及远侧上下文；短过门和窗口边缘变化可能不报告。',
                              '局部速度变化需约6秒稳定脉冲上下文；半/倍速歧义保留，不能确认节拍层级。',
                              '调性按所选音频局部估计；清唱、噪声、打击乐、转调和调式都可能导致未知或误判。']}
    if len(y) < RATE * 2 or np.max(np.abs(y)) < .003:
        return report
    spectrum = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    harmonic, _ = librosa.decompose.hpss(spectrum)
    share = float(np.sum(harmonic ** 2) / max(float(np.sum(spectrum ** 2)), 1e-12))
    chroma = librosa.feature.chroma_stft(S=harmonic ** 2, sr=RATE, n_fft=2048, hop_length=HOP, tuning=0)
    report['key'] = estimate_key(chroma, share)
    from .music_structure import fit_grid, local_keys, repetition_candidates, bar_anchor_candidates, beat_drift_candidates
    report['key_windows']=local_keys(chroma,share,HOP/RATE,start,report['end'])
    rms = librosa.feature.rms(S=spectrum, frame_length=2048, hop_length=HOP)[0]
    timbre=librosa.feature.melspectrogram(S=spectrum**2,sr=RATE,n_fft=2048,hop_length=HOP,n_mels=24)
    report['changes'] = change_candidates(chroma, rms, HOP / RATE, start, report['end'],timbre=timbre)
    report['repetition_candidates'] = repetition_candidates(chroma, rms, HOP / RATE, start, report['end'], timbre=timbre)
    onset = librosa.onset.onset_strength(S=librosa.amplitude_to_db(spectrum, ref=np.max), sr=RATE, hop_length=HOP)
    from .music_structure import tempo_change_candidates
    report['tempo_changes']=tempo_change_candidates(onset,HOP/RATE,start,report['end'])
    # A steady note's tiny leakage must not become a fabricated beat grid.
    flux = np.maximum(np.diff(spectrum, axis=1), 0).sum(axis=0)
    relative_onset = float(np.percentile(flux, 95) / max(float(spectrum.sum(axis=0).mean()), 1e-9))
    if relative_onset < .015 or np.std(onset) < .15 or np.max(onset) < .2:
        return report
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset, sr=RATE, hop_length=HOP,
                                                 tightness=BEAT_TIGHTNESS, trim=False)
    raw_tempo = float(np.asarray(tempo).reshape(-1)[0])
    selected_tempo, tempo_layer = _tempo_layer_choice(onset, raw_tempo, librosa)
    if abs(selected_tempo - raw_tempo) > 1e-3:
        _, beat_frames = librosa.beat.beat_track(
            onset_envelope=onset, sr=RATE, hop_length=HOP,
            bpm=np.asarray([selected_tempo]), tightness=BEAT_TIGHTNESS, trim=False)
        tempo = selected_tempo
    phase_drift = _phase_drift_evidence(onset, beat_frames, start)
    auto_phase_shift = float(phase_drift.get('auto_phase_shift_seconds') or 0.0)
    if abs(auto_phase_shift) >= HOP / RATE:
        shifted = np.asarray(beat_frames, dtype=float) + auto_phase_shift * RATE / HOP
        shifted = shifted[shifted >= 0]
        if len(shifted) >= 4:
            beat_frames = shifted.astype(int)
            phase_drift = _phase_drift_evidence(onset, beat_frames, start)
            phase_drift['correction_applied'] = True
            phase_drift['applied_offset_seconds'] = round(auto_phase_shift, 6)
            phase_drift['phase_policy'] = 'earliest_strong_beat'
    times = librosa.frames_to_time(beat_frames, sr=RATE, hop_length=HOP)
    if len(times) < 4:
        return report
    intervals = np.diff(times)
    fitted=fit_grid(times)
    bpm = fitted['bpm'] or 60 / float(np.median(intervals))
    variation = float(np.std(intervals) / np.mean(intervals))
    if not 40 <= bpm <= 240 or variation > .22:
        return report
    beats = [round(float(start + t), 6) for t in times if start + t < report['end']]
    report['beats'] = beats
    report['beat_drift_candidates'] = beat_drift_candidates(beats, start, report['end'])
    strengths = onset[np.clip(beat_frames.astype(int), 0, len(onset)-1)] if len(onset) else np.array([])
    if not report['tempo_changes']:
        report['bar_anchor_candidates'] = bar_anchor_candidates(beats, strengths, start, report['end'])
    from .music_structure import boundary_candidates
    if not report['tempo_changes']:
        report['phrase_changes']=boundary_candidates(chroma,rms,beats,HOP/RATE,start,report['end'],
                                                   changes=report['changes'])
    report['grid'] = {'status': 'estimated', 'bpm': round(bpm, 3),
                      'anchor': beats[0] if beats else start, 'interval_variation': round(variation, 4)}
    report['grid']['diagnostics']=fitted
    report['grid']['tempo_layer']=tempo_layer
    phase_drift['review_required'] = bool(phase_drift['review_required'] or fitted['review_required'])
    report['grid']['phase_drift']=phase_drift
    if report['tempo_changes']:
        report['grid']['diagnostics'].update(review_required=True,tempo_change_detected=True,
            message='检测到不同稳定局部脉冲速度，须分段复核；全段单一BPM不能确认对齐。')
    return report
