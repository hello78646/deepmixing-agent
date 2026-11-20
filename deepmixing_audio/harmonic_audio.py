"""Local harmonic pitch-class windows, separate from global key estimation.

Chroma overlap can reveal a local conflict candidate but cannot identify chord
functions, voicings, extensions, or prove that two performances sound compatible.
"""
import math
from pathlib import Path

import numpy as np

from .analysis import INPUT_OPTIONS
from .runtime import prepare_dependencies, run_ffmpeg

RATE = 22050
FFT = 4096
HOP = 256
MAX_SECONDS = 24


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _limitations():
    return [
        '逐窗谐波音高分布与全局调性是不同证据；这里不识别或命名已确认和弦。',
        '三和弦模板只生成局部候选根音与性质，不覆盖七和弦、挂留、扩展音、转位或复杂配器。',
        '相似度与置信度只描述特征支持度，不是正确率；音色、旋律与转位可能影响结果。',
        '局部半音建议只供试听，不代表已获移调授权；没有可靠谐波证据时保持未知。',
    ]


# These are deliberately small, transparent templates.  They provide evidence
# for a handoff decision without pretending to be a chord recognizer.
_TRIAD_TEMPLATES = {
    'major': (0, 4, 7),
    'minor': (0, 3, 7),
    'diminished': (0, 3, 6),
}


def _chord_candidate(profile, tonal_strength):
    """Return a conservative local triad candidate for a 12-bin profile."""
    if profile is None or len(profile) != 12 or tonal_strength < .12:
        return None
    values = np.asarray(profile, dtype=float)
    if not np.isfinite(values).all() or float(values.sum()) <= 1e-9:
        return None
    values = values / float(values.sum())
    candidates = []
    for root in range(12):
        for quality, intervals in _TRIAD_TEMPLATES.items():
            template = np.zeros(12, dtype=float)
            template[[(root + interval) % 12 for interval in intervals]] = 1.
            score = float(np.dot(values, template) / np.linalg.norm(template))
            candidates.append((score, root, quality, intervals))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    score, root, quality, intervals = candidates[0]
    runner = candidates[1][0] if len(candidates) > 1 else 0.
    margin = score - runner
    # Weak or symmetric evidence stays unknown.  The exact threshold is less
    # important than preventing a decorative template from becoming a claim.
    if score < .38 or margin < .015:
        return None
    return {'root_pc': int(root), 'quality': quality, 'score': round(score, 6),
            'margin': round(margin, 6),
            'tones': [int((root + interval) % 12) for interval in intervals]}


def _candidate_tones(candidate, shift=0):
    if not isinstance(candidate, dict) or not isinstance(candidate.get('tones'), list):
        return set()
    return {(int(value) + shift) % 12 for value in candidate['tones']
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 12}


def _chroma_features(y):
    prepare_dependencies()
    import librosa
    spectrum = np.abs(librosa.stft(y, n_fft=FFT, hop_length=HOP))
    harmonic, _ = librosa.decompose.hpss(spectrum)
    power, harmonic_power = spectrum ** 2, harmonic ** 2
    chroma = librosa.feature.chroma_stft(S=harmonic_power, sr=RATE, n_fft=FFT, hop_length=HOP, tuning=0)
    return chroma, harmonic_power.sum(axis=0), power.sum(axis=0)


def analyze_harmony(path, start, end, window_seconds=1.0, hop_seconds=.5):
    """Decode a local region and report measured, overlapping chroma windows."""
    if not all(_finite(value) for value in (start, end, window_seconds, hop_seconds)):
        raise ValueError('和声分析时间须为有限数字')
    if start < 0 or end <= start or not .25 <= window_seconds <= 4 or not .1 <= hop_seconds <= window_seconds:
        raise ValueError('和声窗口或开始结束时间无效')
    start, end = float(start), min(float(end), float(start) + MAX_SECONDS)
    report = {'status': 'unknown', 'start': start, 'end': end, 'energy': 0., 'windows': [],
              'window_seconds': float(window_seconds), 'hop_seconds': float(hop_seconds),
              'method': 'local_harmonic_chroma_overlap', 'diagnostics': [], 'limitations': _limitations()}
    try:
        result = run_ffmpeg([*INPUT_OPTIONS, '-ss', f'{start:.9f}', '-i', Path(path), '-t', f'{end-start:.9f}',
                            '-ar', str(RATE), '-ac', '2', '-f', 'f32le', 'pipe:1'])
        audio = np.frombuffer(result.stdout, dtype='<f4')
        if len(audio) < 2 or not np.isfinite(audio).all():
            raise ValueError('没有有效解码采样')
        audio = audio[:len(audio) - len(audio) % 2].reshape(-1, 2)
        # A valid short remainder is not evidence for the requested handoff.
        # Keep the requested bounds, and never turn a truncated crop into a
        # supported constant-shift suggestion. Allow decoder rounding only.
        if len(audio) < round((end-start)*RATE)-2:
            report['decoded_seconds'] = round(len(audio)/RATE, 6)
            report['diagnostics'].append('指定区域解码截短，没有完整局部和声证据')
            return report
        audio = audio[:round((end-start)*RATE)]
        y = audio[:, int(np.argmax(np.mean(audio.astype(float) ** 2, axis=0)))].copy()
        length = len(y) / RATE
        report['end'] = round(start + length, 6)
        report['energy'] = round(float(np.sqrt(np.mean(y.astype(float) ** 2))), 8)
    except Exception:
        report['diagnostics'].append('区域解码失败，没有可用局部和声证据')
        return report
    # Silence and very short crops cannot establish a local harmonic window.
    if report['energy'] < .003 or length < .25:
        return report
    try:
        chroma, harmonic_power, total_power = _chroma_features(y)
    except Exception:
        report['diagnostics'].append('局部和声分析依赖失败，仅保留实际解码能量')
        return report
    offsets = np.arange(0, max(.000001, length - .249999), hop_seconds)
    for offset in offsets:
        finish = min(length, float(offset) + window_seconds)
        if finish - offset < .25:
            continue
        left = int(offset * RATE / HOP)
        right = min(chroma.shape[1], max(left + 1, int(finish * RATE / HOP)))
        samples = y[round(offset * RATE):round(finish * RATE)].astype(float)
        energy = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.
        share = float(harmonic_power[left:right].sum() / max(float(total_power[left:right].sum()), 1e-12))
        profile = np.mean(chroma[:, left:right], axis=1)
        weights = profile / max(float(profile.sum()), 1e-12)
        entropy = -float(np.sum(weights * np.log(np.maximum(weights, 1e-12)))) / math.log(12)
        tonal_strength = max(0., min(1., 1 - entropy))
        confidence = min(1., max(0., share)) * min(1., tonal_strength / .30)
        known = energy >= .003 and share >= .35 and tonal_strength >= .12 and confidence >= .35
        candidate = _chord_candidate(weights, tonal_strength) if known else None
        item = {'start': round(start + float(offset), 6), 'end': round(start + finish, 6),
            'status': 'estimated' if known else 'unknown', 'chroma': [round(float(value), 7) for value in weights] if known else [],
            'energy': round(energy, 8), 'harmonic_share': round(share, 6),
            'tonal_strength': round(tonal_strength, 6), 'confidence': round(confidence, 6) if known else 0.}
        if candidate is not None:
            item['chord_candidate'] = candidate
        report['windows'].append(item)
    if any(window['status'] == 'estimated' for window in report['windows']):
        report['status'] = 'estimated'
    return report


def _vector(window):
    if not isinstance(window, dict) or window.get('status') != 'estimated':
        return None
    confidence = window.get('confidence')
    values = window.get('chroma')
    if not _finite(confidence) or not .35 <= confidence <= 1 or not isinstance(values, list) or len(values) != 12:
        return None
    if not all(_finite(value) and value >= 0 for value in values):
        return None
    vector = np.asarray(values, dtype=float)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-9 else None


def _valid_windows(report):
    if not isinstance(report, dict) or not _finite(report.get('start')) or not _finite(report.get('end')):
        return []
    windows = report.get('windows')
    if not isinstance(windows, list):
        return []
    return [window for window in windows[:240] if isinstance(window, dict)
            and _finite(window.get('start')) and _finite(window.get('end'))
            and report['start'] <= window['start'] < window['end'] <= report['end'] + 1e-6]


def _chord_evidence(a_report, b_report, b_shift=0, matched_windows=None):
    """Compare local chord-like candidates and measured tone overlap."""
    matched_windows = matched_windows or []
    items, shared, conflicts, intervals = [], [], [], []
    for item in matched_windows:
        aw, bw = item.get('_a'), item.get('_b')
        ac = aw.get('chord_candidate') if isinstance(aw, dict) else None
        bc = bw.get('chord_candidate') if isinstance(bw, dict) else None
        av, bv = _vector(aw), _vector(bw)
        if ac is None or bc is None or av is None or bv is None:
            continue
        # Compare the strongest measured pitch classes, while keeping the
        # triad template as an additional, explicitly bounded signal.
        a_tones = set(np.argsort(av)[-4:].tolist())
        b_tones = {(int(value) + b_shift) % 12 for value in np.argsort(bv)[-4:].tolist()}
        union = a_tones | b_tones
        common = len(a_tones & b_tones) / max(1, len(union))
        conflict = len((a_tones - b_tones) | (b_tones - a_tones)) / max(1, len(union))
        template_a, template_b = set(ac['tones']), {(int(v) + b_shift) % 12 for v in bc['tones']}
        template_common = len(template_a & template_b) / max(1, len(template_a | template_b))
        item_out = {'a_start': item.get('a_start'), 'a_end': item.get('a_end'),
                    'b_start': item.get('b_start'), 'b_end': item.get('b_end'),
                    'a_candidate': {k: ac[k] for k in ('root_pc', 'quality', 'score', 'margin')},
                    'b_candidate': {k: bc[k] for k in ('root_pc', 'quality', 'score', 'margin')},
                    'shared_tone_ratio': round(common, 6),
                    'conflict_ratio': round(conflict, 6),
                    'template_shared_ratio': round(template_common, 6)}
        items.append(item_out)
        shared.append(common)
        conflicts.append(conflict)
        intervals.append((item.get('a_start', 0), item.get('a_end', 0)))
    if not items:
        return {'status': 'unknown', 'matched_windows': [], 'shared_tone_ratio': None,
                'conflict_ratio': None, 'conflict_windows': [],
                'limitations': ['匹配窗口缺少稳定的三和弦候选；不对和弦兼容性下结论。']}
    mean_shared, mean_conflict = float(np.mean(shared)), float(np.mean(conflicts))
    conflict_windows = [item for item in items if item['conflict_ratio'] >= .55]
    status = 'review' if conflict_windows or mean_conflict >= .48 else 'estimated'
    return {'status': status, 'matched_windows': items,
            'shared_tone_ratio': round(mean_shared, 6), 'conflict_ratio': round(mean_conflict, 6),
            'conflict_windows': conflict_windows,
            'limitations': ['候选和弦来自局部三和弦模板，不能替代完整编曲、转位、七和弦或试听判断。']}


def _covered_seconds(intervals):
    end = -math.inf
    covered = 0.
    for left, right in sorted(intervals):
        covered += max(0., right - max(left, end))
        end = max(end, right)
    return covered


def compare_harmony(a_report, b_report, b_shift=0):
    """Compare corresponding local windows; shifts are additional to b_shift.

    The supplied shift is an already applied integer semitone adjustment. Each
    proposed_b_shift is an additional suggestion in [-3, 3], never an operation.
    """
    if isinstance(b_shift, bool) or not isinstance(b_shift, int) or not -12 <= b_shift <= 12:
        raise ValueError('比较移调须为-12至12之间整数')
    report = {'status': 'unknown', 'windows': [], 'similarity': None, 'confidence': 0.,
              'proposed_b_shift': None, 'applied_b_shift': b_shift, 'limitations': _limitations(),
              'chord_evidence': {'status': 'unknown', 'matched_windows': [], 'shared_tone_ratio': None,
                                 'conflict_ratio': None, 'conflict_windows': [],
                                 'limitations': ['尚未获得可用的局部和弦候选。']},
              'shift_decision': 'insufficient_support',
              'shift_requirements': {'distinct_pairs': 3, 'covered_seconds': 1.5, 'confidence': .5,
                                     'candidate_margin': .06}}
    a_windows, b_windows = _valid_windows(a_report), _valid_windows(b_report)
    if not a_windows or not b_windows:
        return report
    suggestions, similarities, confidences, intervals, pairs = [], [], [], [], set()
    for aw in a_windows:
        relative_start, relative_end = aw['start'] - a_report['start'], aw['end'] - a_report['start']
        midpoint = (relative_start + relative_end) / 2
        candidates = []
        for bw in b_windows:
            bs, be = bw['start'] - b_report['start'], bw['end'] - b_report['start']
            overlap = max(0., min(relative_end, be) - max(relative_start, bs))
            # A tiny window cannot stand in for most of a much longer one.
            if overlap >= .5 * max(relative_end - relative_start, be - bs):
                candidates.append((abs(midpoint - (bs + be) / 2), bw))
        bw = min(candidates, key=lambda pair: pair[0])[1] if candidates else None
        item = {'a_start': aw['start'], 'a_end': aw['end'], 'b_start': bw['start'] if bw else None,
                'b_end': bw['end'] if bw else None, 'status': 'unknown', 'similarity': None,
                'confidence': 0., 'proposed_b_shift': None}
        item['_a'], item['_b'] = aw, bw
        av, bv = _vector(aw), _vector(bw)
        if av is not None and bv is not None:
            confidence = min(float(aw['confidence']), float(bw['confidence']))
            scores = {shift: float(np.dot(av, np.roll(bv, b_shift + shift))) for shift in range(-3, 4)}
            similarity = scores[0]
            best = max(scores, key=lambda shift: (scores[shift], -abs(shift)))
            runner_up = max(score for shift, score in scores.items() if shift != best)
            margin = scores[best] - runner_up
            suggested = 0 if similarity >= .82 else best if scores[best] >= .82 and scores[best] - similarity >= .12 and margin >= .06 else None
            item.update(status='compatible_candidate' if similarity >= .82 else 'review',
                        similarity=round(similarity, 6), confidence=round(confidence, 6),
                        proposed_b_shift=suggested, candidate_margin=round(margin, 6))
            similarities.append(similarity)
            confidences.append(confidence)
            suggestions.append(suggested)
            bs, be = bw['start'] - b_report['start'], bw['end'] - b_report['start']
            left, right = max(relative_start, bs), min(relative_end, be)
            intervals.append((left, right))
            pairs.add(tuple(round(v, 6) for v in (relative_start, relative_end, bs, be)))
        report['windows'].append(item)
    report['chord_evidence'] = _chord_evidence(a_report, b_report, b_shift, report['windows'])
    for item in report['windows']:
        item.pop('_a', None)
        item.pop('_b', None)
    if similarities:
        mean = float(np.average(similarities, weights=confidences))
        report['similarity'] = round(mean, 6)
        # Coverage lowers confidence when only a few local windows are usable.
        report['confidence'] = round(float(np.mean(confidences)) * len(similarities) / len(report['windows']), 6)
        report['status'] = 'compatible_candidate' if all(item['status'] == 'compatible_candidate' for item in report['windows']) else 'review'
        # A constant transposition is only suggested when every reliable local
        # window agrees; otherwise keep individual suggestions for review.
        coverage = _covered_seconds(intervals)
        report['shift_support'] = dict(distinct_pairs=len(pairs), covered_seconds=round(coverage, 6),
                                       usable_windows=len(similarities), total_windows=len(report['windows']))
        unanimous = len(similarities) == len(report['windows']) and suggestions and all(shift == suggestions[0] for shift in suggestions)
        if unanimous and suggestions[0] == 0:
            report.update(proposed_b_shift=0, shift_decision='unchanged_candidate')
        elif (unanimous and suggestions[0] is not None and len(pairs) >= 3
              and coverage >= 1.5 and report['confidence'] >= .5):
            report.update(proposed_b_shift=suggestions[0], shift_decision='consistent_shift_candidate')
        elif coverage >= 1.5 and len(pairs) >= 3:
            report['shift_decision'] = 'review_or_reselect'
    return report
