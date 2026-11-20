"""Conservative interpretation of independently observed engagement metrics."""
from decimal import Decimal, InvalidOperation
import re


def _bounds(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None
        return (number, number) if number.is_finite() and number >= 0 else None
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*([万亿wWkKmM]?)\s*', value.replace(',', ''))
    if not match:
        return None
    digits, unit = match.groups()
    number = Decimal(digits)
    factor = {'万':10000, '亿':100000000, 'w':10000, 'k':1000, 'm':1000000}.get(unit.lower(), 1)
    number *= factor
    if not unit:
        return number, number
    places = len(digits.partition('.')[2])
    half_quantum = Decimal(factor) * Decimal(10) ** (-places) / 2
    # Cover both rounding-to-nearest and truncation displays conservatively.
    return max(Decimal(0), number-half_quantum), number+2*half_quantum


def threshold_status(metrics, threshold=500000, mode='all', *, metrics_to_check=('likes','favorites','shares')):
    """Return pass/fail/unknown for strict >, never replacing missing data with zero.

    Suffix displays are treated as rounded intervals, not exact measurements.
    This is a conservative approximation of display precision, not a claim
    about any platform's undocumented rounding algorithm.
    """
    if mode not in ('all', 'any'):
        raise ValueError('mode must be all or any')
    if not isinstance(metrics_to_check,(tuple,list)) or not metrics_to_check or any(m not in ('views','likes','favorites','shares') for m in metrics_to_check):
        raise ValueError('choose explicit per-video views/likes/favorites/shares')
    if isinstance(threshold, bool):
        raise ValueError('threshold must be a nonnegative finite number')
    try:
        boundary = Decimal(str(threshold))
    except InvalidOperation as exc:
        raise ValueError('invalid threshold') from exc
    if not boundary.is_finite() or boundary < 0:
        raise ValueError('invalid threshold')
    states = []
    for metric in metrics_to_check:
        bounds = _bounds(metrics.get(metric))
        if bounds is None:
            states.append('unknown')
        elif bounds[0] > boundary:
            states.append('pass')
        elif bounds[1] <= boundary:
            states.append('fail')
        else:
            states.append('unknown')
    if mode == 'all':
        return 'fail' if 'fail' in states else 'unknown' if 'unknown' in states else 'pass'
    return 'pass' if 'pass' in states else 'unknown' if 'unknown' in states else 'fail'
