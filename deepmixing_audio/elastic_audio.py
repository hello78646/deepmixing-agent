"""Bounded piecewise elastic timing with FFmpeg Rubberband, preserving pitch.

This is a constant-tempo render for each map segment, with short fades at
seams. It is not a continuously varying, perfectly transparent stretch and
does not detect beats, words or musical phrases. The map keeps overall length.
Paths belong to the internal caller's trusted track inventory, never a client
supplied HTTP parameter.
"""
import math
from pathlib import Path
import tempfile
import wave

from .runtime import run_ffmpeg


SAMPLE_RATE = 44100
CHANNELS = 2
MIN_SEGMENT = .04
MIN_SPEED = .5
MAX_SPEED = 2.
MAX_POINTS = 64


def _finite(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + '须为有限数字')
    try:
        value = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(name + '须为有限数字') from exc
    if not math.isfinite(value):
        raise ValueError(name + '须为有限数字')
    if positive and value <= 0:
        raise ValueError(name + '须大于0')
    return value


def validate_warp_map(points, length, locked=False):
    """Canonicalise an optional source->target map relative to a selected clip.

    Nonempty maps have 2..64 points. Both clocks start at zero and end at the
    original clip length. Each interval lasts at least 40 ms on both clocks;
    source_delta / target_delta is the Rubberband tempo, bounded to 0.5..2.
    An original-source lock forbids even an identity map supplied explicitly.
    """
    length = _finite(length, '弹性选段长度', positive=True)
    if points is None:
        return []
    if not isinstance(points, list):
        raise ValueError('warp_map须为控制点数组')
    if locked and points:
        raise ValueError('保持原貌已锁定：不能添加弹性时间映射')
    if not points:
        return []
    if not 2 <= len(points) <= MAX_POINTS:
        raise ValueError('弹性时间映射需要2至64个控制点')
    result = []
    for point in points:
        if not isinstance(point, dict) or set(point) != {'source', 'target'}:
            raise ValueError('弹性控制点只能包含source和target')
        source = _finite(point['source'], '弹性源时间')
        target = _finite(point['target'], '弹性目标时间')
        if not 0 <= source <= length or not 0 <= target <= length:
            raise ValueError('弹性控制点须位于选段内')
        result.append(dict(source=source, target=target))
    if any(abs(result[0][clock]) > 1e-9 or abs(result[-1][clock] - length) > 1e-9
           for clock in ('source', 'target')):
        raise ValueError('弹性映射须从0,0开始，以原选段长度结束，不改变整体时长')
    result[0] = dict(source=0., target=0.)
    result[-1] = dict(source=length, target=length)
    for left, right in zip(result, result[1:]):
        source_delta = right['source'] - left['source']
        target_delta = right['target'] - left['target']
        if min(source_delta, target_delta) < MIN_SEGMENT - 1e-9:
            raise ValueError('弹性源和目标时间须严格递增，每段至少40毫秒')
        speed = source_delta / target_delta
        if not MIN_SPEED - 1e-9 <= speed <= MAX_SPEED + 1e-9:
            raise ValueError('弹性分段变速须在0.5至2倍之间')
    return result


def tempo_ramp_map(length, start_speed, end_speed, segments=16):
    """Sample a linear relative speed *shape*, normalised to unchanged length.

    Speeds are relative weights, not BPM promises: (100, 200) is identical to
    (1, 2). Speed is linear over target time, divided by its mean, so its
    integral consumes exactly ``length`` source seconds in ``length`` target
    seconds. Incompatible shapes are rejected, not silently clamped. Short
    clips use fewer intervals to retain the 40 ms minimum on both clocks.
    """
    length = _finite(length, '弹性选段长度', positive=True)
    start = _finite(start_speed, '起始相对速度', positive=True)
    end = _finite(end_speed, '结束相对速度', positive=True)
    if isinstance(segments, bool) or not isinstance(segments, int) or not 1 <= segments < MAX_POINTS:
        raise ValueError('速度渐变分段数须为1至63的整数')
    # Scale before averaging to avoid overflow for large finite relative weights.
    scale = max(start, end)
    start, end = start / scale, end / scale
    mean = (start + end) / 2
    start, end = start / mean, end / mean
    if min(start, end) < MIN_SPEED - 1e-9 or max(start, end) > MAX_SPEED + 1e-9:
        raise ValueError('相对速度曲线归一化后超出0.5至2倍范围')
    count = min(segments, max(1, int(length / MIN_SEGMENT)))
    while count:
        points = []
        for index in range(count + 1):
            fraction = index / count
            target = length * fraction
            source = length * (start * fraction + (end - start) * fraction * fraction / 2)
            points.append(dict(source=source, target=target))
        points[0] = dict(source=0., target=0.)
        points[-1] = dict(source=length, target=length)
        if all(min(b['source']-a['source'], b['target']-a['target']) >= MIN_SEGMENT - 1e-9
               for a, b in zip(points, points[1:])):
            return validate_warp_map(points, length)
        count -= 1
    raise ValueError('速度渐变选段至少需要40毫秒')


def render_warp(path, start, end, points, output_wav):
    """Render trusted audio through each validated interval at constant tempo.

    Output is 44.1 kHz stereo PCM16. Each segment is padded/trimmed to its exact
    target sample count before a >=5 ms fade at both edges, then concatenated
    without overlap. This deliberately permits a short level dip at seams;
    fades do not shorten the clip. Total duration is sample-exact (within one
    output sample of the requested seconds). Original input is never replaced.
    """
    start = _finite(start, '弹性选段起点')
    end = _finite(end, '弹性选段终点')
    if start < 0 or end <= start:
        raise ValueError('弹性选段须为非负起点和递增终点')
    length = end - start
    canonical = validate_warp_map(points, length)
    if not canonical:
        canonical = validate_warp_map([dict(source=0, target=0), dict(source=length, target=length)], length)
    source = Path(path).resolve()
    output = Path(output_wav).resolve()
    if not source.is_file():
        raise ValueError('弹性源音频不存在')
    if source == output or (output.exists() and source.samefile(output)):
        raise ValueError('弹性输出不能覆盖原素材')
    output.parent.mkdir(parents=True, exist_ok=True)
    count = len(canonical) - 1
    total_frames = round(length * SAMPLE_RATE)
    fade_frames = math.ceil(.005 * SAMPLE_RATE)
    split = ''.join(f'[warp_in_{index}]' for index in range(count))
    graph = [f'[0:a]atrim=start={start:.12f}:end={end:.12f},asetpts=PTS-STARTPTS,'
             f'aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,asplit={count}{split}']
    labels = []
    for index, (left, right) in enumerate(zip(canonical, canonical[1:])):
        source_start = round(left['source'] * SAMPLE_RATE)
        source_end = round(right['source'] * SAMPLE_RATE)
        target_frames = round(right['target'] * SAMPLE_RATE) - round(left['target'] * SAMPLE_RATE)
        tempo = (right['source'] - left['source']) / (right['target'] - left['target'])
        label = f'warp_out_{index}'
        graph.append(f'[warp_in_{index}]atrim=start_sample={source_start}:end_sample={source_end},'
                     f'asetpts=PTS-STARTPTS,rubberband=tempo={tempo:.12f}:pitch=1:transients=crisp:'
                     'detector=compound:phase=laminar:window=short:channels=together,'
                     f'apad=whole_len={target_frames},atrim=end_sample={target_frames},'
                     f'afade=t=in:ss=0:ns={fade_frames},'
                     f'afade=t=out:ss={target_frames-fade_frames}:ns={fade_frames},'
                     f'asetpts=PTS-STARTPTS[{label}]')
        labels.append(f'[{label}]')
    graph.append(''.join(labels) + f'concat=n={count}:v=0:a=1,apad=whole_len={total_frames},'
                 f'atrim=end_sample={total_frames},asetpts=PTS-STARTPTS[warped]')
    # Keep any previous destination untouched if decoding/stretching fails.
    with tempfile.TemporaryDirectory(prefix='deepmixing-warp-', dir=output.parent) as directory:
        staged = Path(directory) / 'warped.wav'
        run_ffmpeg(['-i', source, '-filter_complex', ';'.join(graph), '-map', '[warped]',
                    '-ar', SAMPLE_RATE, '-ac', CHANNELS, '-c:a', 'pcm_s16le', '-y', staged])
        with wave.open(str(staged)) as result:
            if (result.getframerate(), result.getnchannels(), result.getnframes()) != (SAMPLE_RATE, CHANNELS, total_frames):
                raise ValueError('弹性音频长度检查未通过，原素材与已有输出保留')
        staged.replace(output)
    return dict(duration=total_frames / SAMPLE_RATE, sample_rate=SAMPLE_RATE, channels=CHANNELS)
