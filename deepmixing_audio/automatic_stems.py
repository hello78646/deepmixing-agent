"""Build real-stem arrangements; never relabel an original mix as a stem.

Public interfaces:
  plan_redrum(project, tracks, source_stems, donor_stems)
  plan_stem_relay(source_stems, tracks)

A stem bundle is the separation job's ``stems`` mapping: role -> track ID or
public ``{'id': track_id, ...}`` record. Roles are vocal (vocals is an alias),
instrumental, drums, bass and other. Optional bundle fields are start/end in
source seconds, source_track_id and preserve_original. Registered tracks, not
caller-supplied records, supply the authoritative roles/provenance/file paths.
Every used bundle must come from one separation_id and one source_track_id.

Redrum needs source vocal/bass/other/drums and donor drums. It returns a new
canonical mashup: vocal + other in the two base slots, bass + donor drums in
performance.events. Neither the old drums nor either full instrumental enters
the mix. Source/donor windows default to their matching input-project slots.

Relay needs 2-8 vocal/instrumental bundles. Vocal clips form a continuous_set;
B's real backing covers A, B then becomes itself, and C's backing previews in
B's tail before C becomes itself. Source-second windows default to complete
stems. Short backing material can repeat up to 16 times, with explicit reasons.

Both functions return {name, project, reasons, techniques, timeline,
role_evidence}. They do not render or mutate inputs. The project can be passed
directly to engine.render / continuous.render_set. All timing is at original
speed; short crossings are not claims of beat, lyric, or phrase alignment.
"""
import math
from pathlib import Path
import wave

from .continuous import set_layout, validate_set
from .models import validate_project


_ROLES = {'vocal', 'instrumental', 'drums', 'bass', 'other'}
_OPTIONS = {'start', 'end', 'source_track_id', 'preserve_original'}


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + '须为有限数字')
    try:
        valid = math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(name + '超出支持范围')
    return float(value)


def _wave_duration(track):
    """Check the real final frame as well as its declared metadata duration."""
    try:
        path = Path(track['path'])
        if not path.is_file():
            raise ValueError('分轨素材文件不存在')
        with wave.open(str(path), 'rb') as source:
            frames, rate = source.getnframes(), source.getframerate()
            frame_bytes = source.getsampwidth() * source.getnchannels()
            if frames <= 0 or rate <= 0 or source.getnchannels() not in (1, 2):
                raise ValueError('分轨音频格式或时长无效')
            source.setpos(frames - 1)
            if len(source.readframes(1)) != frame_bytes:
                raise ValueError('分轨文件存在截断，不能作为完整素材编排')
            actual = frames / rate
        declared = _number(track.get('duration'), '分轨时长', .01, 86400)
        if abs(actual - declared) > 1 / rate + 1e-8:
            raise ValueError('分轨文件实际时长与素材登记不一致')
        return actual
    except (OSError, EOFError, wave.Error, KeyError, TypeError) as exc:
        raise ValueError('需要可读取的完整本机WAV分轨') from exc


def _bundle(raw, tracks, required):
    if not isinstance(raw, dict) or set(raw) - _ROLES - _OPTIONS - {'vocals'}:
        raise ValueError('分轨映射包含不支持的角色或参数')
    raw = dict(raw)
    if 'vocals' in raw:
        if 'vocal' in raw:
            raise ValueError('vocal和vocals不能重复指定')
        raw['vocal'] = raw.pop('vocals')
    locked = raw.get('preserve_original', False)
    if type(locked) is not bool:
        raise ValueError('分轨原貌锁须为布尔值')
    if locked:
        raise ValueError('保持原貌已锁定：不能进行分轨改编')
    if not set(required) <= set(raw):
        raise ValueError('缺少所需的真实分轨素材')
    ids, durations, parents, separations = {}, [], set(), set()
    for role in _ROLES:
        if role not in raw:
            continue
        value = raw[role]
        key = value.get('id') if isinstance(value, dict) else value
        if not isinstance(key, str) or key not in tracks:
            raise ValueError('分轨引用了不存在的素材')
        record = tracks[key]
        if record.get('id', key) != key or record.get('stem_role') != role:
            raise ValueError('素材登记的真实分轨角色不匹配，不能重新贴标签冒充')
        parent, separation = record.get('source_track_id'), record.get('separation_id')
        if not isinstance(parent, str) or parent not in tracks or not isinstance(separation, str) or not separation:
            raise ValueError('分轨缺少经登记的原曲和分离任务来源')
        if record.get('preserve_original') or tracks[parent].get('preserve_original'):
            raise ValueError('保持原貌已锁定：不能使用其派生分轨改编')
        ids[role] = key
        parents.add(parent)
        separations.add(separation)
        durations.append(_wave_duration(record))
    if len(parents) != 1 or len(separations) != 1:
        raise ValueError('同一组分轨须来自同一原曲和同一次分离')
    parent = next(iter(parents))
    if raw.get('source_track_id', parent) != parent:
        raise ValueError('分轨来源声明不一致')
    if max(durations) - min(durations) > 1 / 22050 + 1e-8:
        raise ValueError('同组分轨的实际长度不一致')
    duration = min(durations)
    start = _number(raw.get('start', 0), '分轨选段开始', 0, duration)
    end = _number(raw.get('end', duration), '分轨选段结束', 0, duration)
    minimum = .25 if 'vocal' in required else .01
    if end - start < minimum - 1e-9:
        raise ValueError('分轨选段过短')
    return dict(ids=ids, source=parent, start=start, end=end,
                explicit_window='start' in raw or 'end' in raw, duration=duration)


def _belongs(track_id, source_id, tracks):
    return track_id == source_id or tracks[track_id].get('source_track_id') == source_id


def _project_window(bundle, slots, tracks, minimum=.25):
    matching = next((slot for slot in slots if _belongs(slot['track_id'], bundle['source'], tracks)), None)
    if matching is None:
        raise ValueError('分轨原曲与待改编工程的素材不对应')
    if not bundle['explicit_window']:
        bundle['start'] = _number(matching['start'], '分轨片段开始', 0, bundle['duration'])
        bundle['end'] = _number(matching['end'], '分轨片段结束', 0, bundle['duration'])
    if bundle['end'] - bundle['start'] < minimum - 1e-9:
        raise ValueError('改编选段过短')


def _slot(track_id, role, bundle):
    return dict(track_id=track_id, role=role, start=bundle['start'], end=bundle['end'],
                bpm=None, gain_db=-9., bass_db=0., semitones=0., preserve_original=False)


def _coverage(events, track_id, start, end, time, length):
    """Cover an interval exactly with one bounded real-source repeat event."""
    available = end - start
    if available < .01 - 1e-9 or length < .01 - 1e-9:
        raise ValueError('每个真实取样事件至少需要10毫秒')
    repeats = max(1, math.ceil(length / available - 1e-9))
    if repeats > 16:
        raise ValueError('伴奏或供体鼓过短，需要超过16次循环；请选用更长的真实分轨')
    # Equal loop lengths avoid a final sub-10ms fragment. This is a source-time
    # loop choice, explicitly not a detected beat or bar boundary.
    cue_length = length / repeats
    if cue_length < .01 - 1e-9:
        raise ValueError('分轨循环片段至少需要10毫秒')
    events.append(dict(track_id=track_id, start=start, end=start + cue_length,
                       time=time, gain_db=-9., semitones=0,
                       repeats=repeats, interval=cue_length))
    return repeats > 1


def _evidence(ids, tracks):
    return [dict(track_id=key, stem_role=tracks[key]['stem_role'],
                 source_track_id=tracks[key]['source_track_id'])
            for key in dict.fromkeys(ids)]


def plan_redrum(project, tracks, source_stems, donor_stems):
    """Keep the real source vocal/other/bass and replace its entire drum stem."""
    original = validate_project(project, tracks)
    if any(original[name]['preserve_original'] for name in ('a', 'b')):
        raise ValueError('保持原貌已锁定：不能替换原曲分轨或供体鼓组')
    source = _bundle(source_stems, tracks, {'vocal', 'bass', 'other', 'drums'})
    donor = _bundle(donor_stems, tracks, {'drums'})
    if source['source'] == donor['source']:
        raise ValueError('换鼓需要另一首曲目的真实供体鼓组')
    _project_window(source, (original['a'], original['b']), tracks)
    _project_window(donor, (original['b'], original['a']), tracks, .01)
    length = source['end'] - source['start']
    events = []
    _coverage(events, source['ids']['bass'], source['start'], source['end'], 0, length)
    looped = _coverage(events, donor['ids']['drums'], donor['start'], donor['end'], 0, length)
    result = dict(version=1, mode='mashup',
                  a=_slot(source['ids']['vocal'], 'vocal', source),
                  b=_slot(source['ids']['other'], 'instrumental', source),
                  target_bpm=None, transition_seconds=0, b_offset_seconds=0, echo=0,
                  title='原曲旋律与新鼓组', prompt=original['prompt'],
                  performance=dict(events=events, drums=[]))
    reasons = ['保留原曲真实vocal、other、bass三轨，仅用另一曲目的真实drums取代原drums；不混入仍含旧鼓的整套伴奏。',
               '以当前选段原速原调重建分轨，未对拍；不声称已找到小节首拍或乐句边界。',
               '原工程的全轨效果、变速、移调及原伴奏不会自动沿用到新分轨方案。']
    if looped:
        reasons.append('供体鼓较短，使用明确的原速真实片段循环补齐；循环边界未对拍。')
    return dict(name='保留旋律的真实换鼓版', project=validate_project(result, tracks),
                reasons=reasons, techniques=['T44'],
                timeline=[dict(kind='redrum', start=0., end=length,
                               melodic_source=source['source'], drum_source=donor['source'])],
                role_evidence=_evidence([source['ids']['vocal'], source['ids']['other'],
                                         source['ids']['bass'], donor['ids']['drums']], tracks))


def plan_stem_relay(source_stems, tracks):
    """Plan A-vocal x B-backing -> B -> B-vocal x C-backing -> C."""
    if not isinstance(source_stems, list) or not 2 <= len(source_stems) <= 8:
        raise ValueError('分轨接力需要2至8组真实人声和伴奏')
    bundles = [_bundle(raw, tracks, {'vocal', 'instrumental'}) for raw in source_stems]
    if len({bundle['source'] for bundle in bundles}) != len(bundles):
        raise ValueError('接力需要不同原曲，不能用同一曲目的分轨冒充下一首')
    lengths = [bundle['end'] - bundle['start'] for bundle in bundles]
    crossing = min(.05, max(0., (min(lengths) - .25) / 2))
    if crossing < .01:
        crossing = 0.
    plan = dict(kind='continuous_set', version=1, title='真实分轨人声接力',
                target_bpm=None, preview_join=0,
                clips=[_slot(bundle['ids']['vocal'], 'vocal', bundle) for bundle in bundles],
                joins=[dict(kind='smooth' if crossing else 'cut', seconds=crossing)
                       for _ in range(len(bundles) - 1)])
    plan = validate_set(plan, tracks)
    layout = set_layout(plan)
    events, timeline = [], []
    looped = False
    # B introduces itself beneath A before B's own vocal takes over.
    b = bundles[1]
    b_start = layout['clips'][1]['start']
    looped |= _coverage(events, b['ids']['instrumental'], b['start'], b['end'], 0, b_start)
    timeline.append(dict(kind='mashup', start=0., end=b_start,
                         vocal_source=bundles[0]['source'], backing_source=b['source']))
    preview_starts = {}
    for index in range(2, len(bundles)):
        bundle = bundles[index]
        at = layout['clips'][index]['start']
        lead = min(.25, lengths[index - 1] * .1, lengths[index] * .25)
        if lead < .01 - 1e-9:
            raise ValueError('下一曲伴奏预告至少需要10毫秒')
        preview_starts[index] = at - lead
        # Use a real tail pre-roll so the new song's own vocal/backing can both
        # restart at their original crop start without inventing prior audio.
        _coverage(events, bundle['ids']['instrumental'], bundle['end'] - lead,
                  bundle['end'], at - lead, lead)
        timeline.append(dict(kind='mashup', start=at - lead, end=at,
                             vocal_source=bundles[index - 1]['source'], backing_source=bundle['source']))
    for index in range(1, len(bundles)):
        bundle = bundles[index]
        at = layout['clips'][index]['start']
        end = preview_starts.get(index + 1, layout['clips'][index]['end'])
        looped |= _coverage(events, bundle['ids']['instrumental'], bundle['start'], bundle['end'], at, end - at)
        timeline.append(dict(kind='source_song', start=at, end=end,
                             vocal_source=bundle['source'], backing_source=bundle['source']))
    plan['performance'] = dict(events=events, drums=[])
    reasons = ['A段真实人声叠在B真实伴奏上；B进入后使用B人声与B伴奏，再在B尾部预告C伴奏并交给C。',
               '全部分轨原速原调，未对拍；人声只作短交叉淡化，不假设已识别歌词句尾或小节首拍。',
               '下一曲的伴奏尾部用于短预告，进入本段时从所选起点重启，时序明确保存于事件层。']
    if looped:
        reasons.append('较短的真实伴奏使用明确片段循环补齐；循环边界未对拍。')
    return dict(name='共同曲目真实分轨接力', project=validate_set(plan, tracks),
                reasons=reasons, techniques=['T43'],
                timeline=sorted(timeline, key=lambda section: section['start']),
                role_evidence=_evidence([bundle['ids'][role] for bundle in bundles
                                         for role in ('vocal', 'instrumental')], tracks))
