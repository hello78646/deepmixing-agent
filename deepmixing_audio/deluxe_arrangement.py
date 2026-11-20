"""Small, explainable actions for a source-specific DJ showcase.

The actions are intentionally source-specific. They describe a bounded musical
gesture and its reason; they do not pretend to infer lyrics or full phrases.
"""
import math


def build_deluxe_actions(case):
    joins = case.get('rhythm-three', {}).get('join_times', [])
    if len(joins) != 2:
        raise ValueError('惊艳编排需要两处已知交接位置')
    first, second = (float(value) for value in joins)
    return [
        {
            'kind': 'vocal_echo_spotlight',
            'purpose': 'anticipation',
            'join': first,
            'start': round(first - 4 * 60 / 104, 6),
            'end': round(first + 8 * 60 / 104, 6),
            'reason': '保护前曲主唱尾部，同时用短回声制造下一首进入前的期待；不覆盖后曲主唱。',
        },
        {
            'kind': 'breath_drop',
            'purpose': 'release',
            'join': second,
            'start': round(second - 2 * 60 / 104, 6),
            'end': round(second + 8 * 60 / 104, 6),
            'reason': '在后曲律动落点前短暂收窄空间，留出呼吸，再让新曲鼓组成为情绪落点。',
        },
    ]


def validate_deluxe_actions(actions, duration):
    if not isinstance(actions, list) or len(actions) != 2:
        raise ValueError('需要两处创意动作')
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('成品时长无效')
    required = {'kind', 'purpose', 'join', 'start', 'end', 'reason'}
    kinds = {'vocal_echo_spotlight', 'breath_drop'}
    for action in actions:
        if set(action) != required or action['kind'] not in kinds:
            raise ValueError('创意动作字段无效')
        start, end, join = action['start'], action['end'], action['join']
        if not all(not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) for value in (start, end, join)):
            raise ValueError('创意动作时间无效')
        if not 0 <= start < end <= duration or end - start > 12:
            raise ValueError('创意动作必须是有界的短动作')
        if not (start <= join <= end):
            raise ValueError('创意动作必须围绕其交接位置')
        if not isinstance(action['reason'], str) or not action['reason'].strip():
            raise ValueError('创意动作必须说明原因')
    if actions[0]['join'] >= actions[1]['join']:
        raise ValueError('创意动作交接顺序无效')
    return actions
