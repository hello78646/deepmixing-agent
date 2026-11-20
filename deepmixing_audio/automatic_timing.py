"""Small timing candidates supported only by measured local pulse evidence.

This module never identifies a bar downbeat and never extends a locally
estimated pulse grid over unanalysed audio. A caller must still enforce locks.
"""
import math

from .elastic_audio import validate_warp_map
from .grid_evidence import grid_review_reasons

_BLOCKED = {'swung', 'rubato', 'non_four_four'}
MAX_CORRECTION = .060


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _inside(report, start, end):
    return (isinstance(report, dict) and _finite(report.get('start')) and _finite(report.get('end'))
            and report['start'] <= start < end <= report['end'] + 1e-6)


def propose_warp_map(report, start, end, features=()):
    """Suggest a length-preserving, relative source->target map or stay original."""
    result = {'available': False, 'warp_map': [], 'reason': '保持原速：没有可靠局部修正依据',
              'evidence': {'method': 'measured_local_pulse_linear_fit', 'beat_count': 0,
                           'maximum_allowed_correction_seconds': MAX_CORRECTION,
                           'bar_downbeat_identified': False}}
    if not _finite(start) or not _finite(end) or start < 0 or end <= start:
        result['reason'] = '保持原速：选段范围无效'
        return result
    if not _inside(report, start, end):
        result['reason'] = '保持原速：选段没有被局部实测完整覆盖，不能外推到未分析区间'
        return result
    tags = set()
    for values in (features, report.get('features', ()), report.get('measured_features', ())):
        if isinstance(values, str):
            tags.add(values)
        elif isinstance(values, (list, tuple, set)):
            tags.update(value for value in values if isinstance(value, str))
    if tags & _BLOCKED:
        result['reason'] = '保持原速：摆动、自由速度或非四拍特征不适合自动拉直'
        return result
    grid = report.get('grid')
    if not isinstance(grid, dict) or grid.get('status') != 'estimated' or not _finite(grid.get('bpm')) or not 40 <= grid['bpm'] <= 240:
        result['reason'] = '保持原速：没有有效候选拍网'
        return result
    diagnostics = grid.get('diagnostics', {})
    if not isinstance(diagnostics, dict) or grid_review_reasons(report):
        result['reason'] = '保持原速：局部拍网仍需复核'
        return result
    raw_beats = report.get('beats')
    if not isinstance(raw_beats, list) or not all(_finite(beat) for beat in raw_beats):
        result['reason'] = '保持原速：拍点证据无效'
        return result
    if any(right <= left for left, right in zip(raw_beats, raw_beats[1:])):
        result['reason'] = '保持原速：拍点不是严格递增的实测序列'
        return result
    beats = [float(beat) for beat in raw_beats if start <= beat <= end]
    result['evidence'].update(beat_count=len(beats), analyzed_start=report['start'], analyzed_end=report['end'],
                              selected_start=float(start), selected_end=float(end))
    if len(beats) < 6:
        result['reason'] = '保持原速：选段内不足6个实测拍点'
        return result
    count = len(beats)
    middle = (count - 1) / 2
    average = sum(beats) / count
    denominator = sum((index - middle) ** 2 for index in range(count))
    interval = sum((index - middle) * (beat - average) for index, beat in enumerate(beats)) / denominator
    anchor = average - middle * interval
    intervals = [right - left for left, right in zip(beats, beats[1:])]
    interval_mean = sum(intervals) / len(intervals)
    variation = math.sqrt(sum((value - interval_mean) ** 2 for value in intervals) / len(intervals)) / interval_mean
    fitted = [anchor + index * interval for index in range(count)]
    corrections = [target - source for source, target in zip(beats, fitted)]
    largest = max(abs(correction) for correction in corrections)
    result['evidence'].update(fitted_bpm=round(60 / interval, 6), interval_variation=round(variation, 6),
                              maximum_correction_seconds=round(largest, 9))
    if variation > .08 or abs((60 / interval) / grid['bpm'] - 1) > .03:
        result['reason'] = '保持原速：实测间距不够稳定或与候选拍速不一致'
        return result
    if largest > MAX_CORRECTION:
        result['reason'] = '保持原速：所需局部修正超过60毫秒'
        return result
    if largest < .001:
        result['reason'] = '保持原速：实测拍点已经接近均匀，无需添加弹性处理'
        return result
    length = float(end - start)
    interior = [{'source': round(beat - start, 9), 'target': round(target - start, 9)}
                for beat, target in zip(beats, fitted)
                if min(beat - start, target - start, end - beat, end - target) >= .04]
    if len(interior) > 62:
        interior = [interior[round(index * (len(interior) - 1) / 61)] for index in range(62)]
    points = [{'source': 0., 'target': 0.}, *interior, {'source': length, 'target': length}]
    point_correction = max(abs(point['source'] - point['target']) for point in points)
    if point_correction > MAX_CORRECTION:
        result['reason'] = '保持原速：控制点精度后修正超过60毫秒'
        return result
    if not interior or point_correction < .001:
        result['reason'] = '保持原速：没有适合安全分段的局部修正点'
        return result
    try:
        canonical = validate_warp_map(points, length)
    except ValueError:
        result['reason'] = '保持原速：局部修正未通过分段时长与速度范围校验'
        return result
    result.update(available=True, warp_map=canonical,
                  reason='仅对完整实测选段提出不超过60毫秒的局部脉冲校正候选；不是小节首拍识别')
    return result


def _quiet_intervals(report):
    """Complement measured RMS activity only when decoding actually succeeded."""
    waveform, regions = report.get('waveform'), report.get('activity_regions')
    if not isinstance(waveform, list) or not waveform or not all(_finite(value) and value >= 0 for value in waveform):
        return []
    if not _finite(report.get('energy')) or not isinstance(regions, list) or not _inside(report, report.get('start'), report.get('end')):
        return []
    if not regions and report['energy'] >= .003:
        return []
    valid = []
    for region in regions:
        if (not isinstance(region, dict) or region.get('kind') != 'measured_activity'
                or not _finite(region.get('start')) or not _finite(region.get('end'))
                or not _inside(report, region['start'], region['end'])):
            return []
        valid.append((region['start'], region['end']))
    valid.sort()
    gaps, cursor = [], report['start']
    for left, right in valid:
        if left - cursor >= .12:
            gaps.append((cursor, left))
        cursor = max(cursor, right)
    if report['end'] - cursor >= .12:
        gaps.append((cursor, report['end']))
    return gaps


def locate_boundary(report, start, end, tail=True):
    """Choose a labelled boundary candidate; never claim a semantic audio cut.

    Input can contain ``lyrics`` from analyze_lyrics, or itself be an ASR report.
    High-confidence ASR segments without known word gaps outrank quiet intervals;
    feature changes are explicitly marked nonsemantic candidates.
    """
    empty = {'available': False, 'time': None, 'kind': 'unknown', 'semantic': False,
             'confirmed_phrase':False,'review_required':True,
             'confidence': None, 'reason': '没有可靠边界证据，保持已有选段', 'evidence': {}, 'candidates': []}
    if not isinstance(report, dict) or not _finite(start) or not _finite(end) or start < 0 or end <= start or not isinstance(tail, bool):
        return empty
    candidates = []
    lyrics = report.get('lyrics', report)
    if isinstance(lyrics, dict) and lyrics.get('status') == 'estimated' and isinstance(lyrics.get('segments'), list):
        for segment in lyrics['segments']:
            if not isinstance(segment, dict) or not _finite(segment.get('confidence')) or not .80 <= segment['confidence'] <= 1:
                continue
            if not isinstance(segment.get('text'), str) or not segment['text'].strip():
                continue
            if (segment.get('boundary_start_reliable') is not True or segment.get('boundary_end_reliable') is not True
                    or segment.get('words_complete') is False):
                continue
            left, right = segment.get('start'), segment.get('end')
            if not _finite(left) or not _finite(right) or not _inside(lyrics, left, right):
                continue
            time = right if tail else left
            if start <= time <= end:
                candidates.append({'time': float(time), 'kind': 'asr_segment_end_candidate' if tail else 'asr_segment_start_candidate',
                    'semantic': True, 'confidence': float(segment['confidence']),
                    'reason': '首尾词通过且无已知词过滤缺口的ASR文本端点候选，仍需核对唱词与音乐乐句',
                    'evidence': {'segment_start': left, 'segment_end': right, 'priority': 0}})
    for left, right in _quiet_intervals(report):
        left, right = max(start, left), min(end, right)
        if right - left >= .12:
            candidates.append({'time': float((left + right) / 2), 'kind': 'measured_quiet_candidate', 'semantic': False,
                'confidence': .65, 'reason': '实测低活动空隙候选；分帧RMS不保证完全静音或歌词结束',
                'evidence': {'quiet_start': left, 'quiet_end': right, 'priority': 1}})
    feature_changes = report.get('boundary_candidates', [])
    if isinstance(feature_changes, list) and _inside(report, report.get('start'), report.get('end')):
        for change in feature_changes:
            if not isinstance(change, dict) or change.get('kind') not in ('structure_change_candidate', 'phrase_boundary_candidate'):
                continue
            time, strength = change.get('time'), change.get('strength')
            if _finite(time) and _finite(strength) and strength >= .5 and start <= time <= end and _inside(report, max(report['start'], time - .000001), min(report['end'], time + .000001)):
                candidates.append({'time': float(time), 'kind': 'feature_change_candidate', 'semantic': False,
                    'confidence': None, 'reason': '音频特征变化候选，不代表歌词语义或已确认乐句',
                    'evidence': {'strength': float(strength), 'priority': 2}})
    if not candidates:
        return empty
    candidates.sort(key=lambda candidate: (candidate['evidence']['priority'], -candidate['time'] if tail else candidate['time']))
    return {'available': True, **candidates[0], 'candidates': candidates,
            'confirmed_phrase':False,'review_required':True}
