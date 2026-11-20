"""Build a local, source-aligned accompaniment -> solo -> rebuild bridge.

This deliberately changes density rather than pretending two melodic backings
can always be beat-blended. It does not infer lyric phrases or downbeats.
"""
import copy
import math

from .continuous import validate_set,set_layout


def _stem_scale(track):
    """Read the separator's common attenuation, never infer source loudness."""
    quality=track.get('separation_quality',{})
    if not isinstance(quality,dict):raise ValueError('分轨共同缩放元数据无效')
    scale=quality.get('shared_scale',1.)
    if isinstance(scale,bool) or not isinstance(scale,(int,float)) or not math.isfinite(scale) or not 0<scale<=1:
        raise ValueError('分轨共同缩放系数无效')
    return float(scale)


def _stem_gain(clip,track):
    gain=clip['gain_db']-20*math.log10(_stem_scale(track))
    if gain>0:raise ValueError('分轨缩放补偿需要额外增益余量，保留原曲候选')
    return gain


def plan_tail_preserving_bridge(plan,tracks,join,outgoing_vocal,incoming_vocal,incoming_drums,*,preroll_seconds=2.,drum_lowpass_hz=None):
    """Keep selected sources intact; hand A's vocal to B's native drum preroll.

    This is an explicit arrangement candidate, not phrase/beat recognition.
    Only the final join is supported so later placements cannot silently move.
    """
    p=validate_set(plan,tracks)
    if isinstance(join,bool) or not isinstance(join,int) or join!=len(p['joins'])-1:
        raise ValueError('保留尾唱鼓桥先处理末处交接')
    if (isinstance(preroll_seconds,bool) or not isinstance(preroll_seconds,(int,float)) or
            not math.isfinite(preroll_seconds) or not .5<=preroll_seconds<=6):
        raise ValueError('鼓预滚须为0.5至6秒')
    lead=float(preroll_seconds)
    if drum_lowpass_hz is not None and (isinstance(drum_lowpass_hz,bool) or
            not isinstance(drum_lowpass_hz,(int,float)) or not math.isfinite(drum_lowpass_hz) or
            not 80<=drum_lowpass_hz<=8000):
        raise ValueError('鼓预滚低通须为80至8000Hz')
    if p.get('performance') is not None or p['target_bpm'] is not None:
        raise ValueError('保留尾唱鼓桥需要原速且不能覆盖已有取样编排')
    a,b=p['clips'][join:join+2]
    for c in (a,b):
        t=tracks[c['track_id']]
        if (c['preserve_original'] or t.get('preserve_original') or c['role']!='full_mix' or t.get('stem_role') or
                c['semitones'] or c['bass_db'] or any(c.get(k) for k in ('effects','warp_map','gain_envelope'))):
            raise ValueError('不能覆盖原貌锁、分轨片段或已有调音；请保留当前版本')
    length=a['end']-a['start']
    previous=p['joins'][join-1]['seconds'] if join else 0.
    if length-lead-.01-previous<.25 or b['start']<lead or b['end']-b['start']<.25:
        raise ValueError('鼓预滚或前后交接余量不足；不裁短既有选段')
    sources=[(outgoing_vocal,'vocal',a),(incoming_vocal,'vocal',b),(incoming_drums,'drums',b)]
    for sid,role,parent in sources:
        t=tracks.get(sid,{})
        quality=t.get('separation_quality')
        if (t.get('stem_role')!=role or t.get('source_track_id')!=parent['track_id'] or t.get('preserve_original') or
                not t.get('separation_id') or not isinstance(quality,dict) or 'shared_scale' not in quality or
                abs(t.get('duration',0)-tracks[parent['track_id']]['duration'])>.05):
            raise ValueError('需要同源、同时长且带共同缩放记录的真实分轨')
        _stem_scale(t)
    bv,bd=tracks[incoming_vocal],tracks[incoming_drums]
    if bv['separation_id']!=bd['separation_id'] or abs(_stem_scale(bv)-_stem_scale(bd))>1e-9:
        raise ValueError('后曲人声与鼓须来自同一次分离及共同缩放')
    old_duration=set_layout(p)['duration']
    p['joins'][join]=dict(kind='cut',seconds=0)
    boundary=set_layout(p)['clips'][join]['end']
    # Start A's complementary cue before its withdrawal so the cue's own
    # five-ms fade cannot attenuate an otherwise preserved vocal onset.
    a['gain_envelope']=[dict(time=0,gain=1),dict(time=length-lead,gain=1),dict(time=length-lead+.2,gain=0)]
    b['gain_envelope']=[dict(time=0,gain=0),dict(time=.005,gain=0),dict(time=.205,gain=1)]
    def cue(sid,start,end,time,points,clip):
        points=[*points[:-1],(end-start,points[-1][1])]
        return dict(track_id=sid,start=start,end=end,time=time,gain_db=_stem_gain(clip,tracks[sid]),
                    semitones=0,gain_envelope=[dict(time=t,gain=g) for t,g in points])
    # B's drums span its preroll and rebuild as ONE native-time cue. No loop,
    # repeated vocal, source-time reset or advance of the selected B vocal.
    p['performance']=dict(events=[
        cue(outgoing_vocal,a['end']-lead-.01,a['end'],boundary-lead-.01,
            [(0,0),(.01,0),(.21,1),(lead+.01,1)],a),
        cue(incoming_drums,b['start']-lead,b['start']+.215,boundary-lead,
            [(0,0),(.06,1),(lead+.005,1),(lead+.205,0),(lead+.215,0)],b),
        cue(incoming_vocal,b['start'],b['start']+.215,boundary,
            [(0,1),(.005,1),(.205,0),(.215,0)],b)],drums=[])
    if drum_lowpass_hz is not None:
        p['performance']['events'][1]['lowpass_hz']=float(drum_lowpass_hz)
    p=validate_set(p,tracks)
    result=dict(project=p,name='保留尾唱 · 后曲原生鼓预滚 · 原位恢复',
        reasons=['保留两侧全部既有选段，前曲尾唱通过同源人声保留；后曲鼓预滚只播放一次。',
                 '后曲人声从原选段起点进入，再用互补包络恢复同源同时间原曲；不循环、不变速、不移调。',
                 '移除原重叠会延长成品；分轨残留、鼓型与情绪衔接仍需试听，不据此确认词句或首拍。'],
        evidence=dict(method='tail_preserving_incoming_drum_preroll',join=join,
            boundary=boundary,preroll_seconds=lead,preparation_start=boundary-lead,
            incoming_vocal_start=boundary,incoming_vocal_source_start=b['start'],
            source_bounds_preserved=True,vocal_source_continuity=True,drum_looped=False,
            confirmed_phrase=False,confirmed_groove=False,
            duration_delta_seconds=set_layout(p)['duration']-old_duration,
            stem_gains=[dict(track_id=sid,gain_db=_stem_gain(parent,tracks[sid]),scale=_stem_scale(tracks[sid]))
                        for sid,role,parent in sources]))
    if drum_lowpass_hz is not None:
        result['name']='保留尾唱 · 收窄鼓预滚频段 · 原位恢复'
        result['reasons'].append('鼓预滚采用二阶低通减少中高频暴露，恢复原曲后回到原鼓音色；镲片、鼓边缘与亮度也会改变，不代表已去除人声。')
        result['evidence'].update(drum_lowpass_hz=float(drum_lowpass_hz),
            vocal_bleed_removed=False,drum_timbre_changed=True)
    return result


def plan_progressive_bridge(plan,tracks,join,outgoing_instrumental,incoming,report):
    """B's breathing route with independently staged incoming real stems.

    This automates role density, not note generation, drum-hit rewriting or
    semantic phrase recognition. Restore the original at the same source time.
    """
    roles=('vocal','other','drums','bass')
    if not isinstance(incoming,dict) or set(incoming)!=set(roles):
        raise ValueError('渐进前奏需要同一任务的人声、其他乐器、鼓和贝斯')
    bridge_inputs(plan,tracks,join,[(outgoing_instrumental,'instrumental',0)]+[(incoming[r],r,1) for r in roles])
    if join!=len(plan['joins'])-1:raise ValueError('渐进前奏先处理末处交接')
    job=tracks[incoming['vocal']].get('separation_id')
    if not job or any(tracks[incoming[r]].get('separation_id')!=job for r in roles):
        raise ValueError('后曲四角色必须来自同一次分离')
    scales=[_stem_scale(tracks[incoming[r]]) for r in roles]
    if max(scales)-min(scales)>1e-9:
        raise ValueError('后曲四角色共同缩放不一致')
    result=plan_vocal_bridge(plan,tracks,join,outgoing_instrumental,incoming['vocal'])
    p=result['project'];b=p['clips'][join+1];source_start=b['start']-2
    # Prefer actual local pulses, without calling them confirmed downbeats.
    from .transition_refinement import _pulse
    pulse=_pulse(report.get('b',{}))
    def at(seconds):
        if pulse:
            candidates=[v-source_start for v in pulse[0] if source_start+seconds<=v<=source_start+seconds+.65]
            if candidates:return min(candidates)
        return seconds
    other_start=2.;other_full=at(6);drum_start=at(4);drum_full=at(8)
    bass_start=at(6);bass_full=at(10);restore_start=max(other_full,drum_full,bass_full)
    restore_end=restore_start+1.
    if b['end']-source_start<restore_end+.5:
        raise ValueError('后曲余量不足以渐进展开并恢复原曲，保留清唱桥')
    span=source_start+restore_end-source_start
    b['gain_envelope']=[dict(time=0,gain=0),dict(time=restore_start-2,gain=0),dict(time=span-2,gain=1)]
    teaser=result['evidence']['vocal_teaser_start']
    stem_gain=_stem_gain(b,tracks[incoming['vocal']])
    events=[p['performance']['events'][0]]
    stages={'vocal':[(0,0),(.06,1),(restore_start,1),(span,0)],
            'other':[(0,0),(other_start,0),(at(3.5),.25),(at(5),.6),(other_full,1),(restore_start,1),(span,0)],
            'drums':[(0,0),(drum_start,0),(at(5),.25),(at(7),.6),(drum_full,1),(restore_start,1),(span,0)],
            'bass':[(0,0),(bass_start,0),(at(8),.35),(bass_full,1),(restore_start,1),(span,0)]}
    for role in roles:
        # Equal-stage points can coincide. Keep the last identical value once.
        points={float(t):float(g) for t,g in stages[role]}
        events.append(dict(track_id=incoming[role],start=source_start,end=source_start+restore_end,time=teaser,
            gain_db=stem_gain,semitones=0,gain_envelope=[dict(time=t,gain=g) for t,g in sorted(points.items())]))
    p['performance']=dict(events=events,drums=[])
    result.update(project=validate_set(p,tracks),name='清唱呼吸 · 后曲旋律、鼓与低频逐层展开',techniques=['T59','T62','T61','T63'],
        reasons=[result['reasons'][0],
            '保留两秒清唱呼吸；随后先轻露后曲乐器，鼓延后渐入，贝斯最后展开。不是把整套伴奏的淡入简单拉长。',
            '四角色使用同一分离任务及同一源时间，补偿共同缩放；全部展开后再用一秒互补交接恢复同位置原曲，不重唱或跳词。',
            '这是角色密度编排：鼓型未逐击重写；other也可能包含节奏乐器，分离残留和听感仍需比较。'])
    result['evidence'].update(method='progressive_solo_rebuild',drum_pattern_rewritten=False,
        restore_source_start=restore_start,restore_source_end=span,shared_scale_compensated=scales[0],
        stem_gains=[result['evidence']['stem_gains'][0]]+[
            dict(track_id=incoming[r],scale=scales[0],gain_db=stem_gain) for r in roles],
        backing_rebuild_end=teaser+restore_end,local_pulses_used=bool(pulse),
        layers=[dict(role=r,start=teaser+s,full=teaser+f) for r,s,f in
                [('other',other_start,other_full),('drums',drum_start,drum_full),('bass',bass_start,bass_full)]],
        source_roles=result['evidence']['source_roles'][:1]+[
            dict(track_id=incoming[r],role=r,source_track_id=b['track_id'],separation_id=job) for r in roles])
    return result


def bridge_inputs(plan,tracks,join,sources):
    p=validate_set(plan,tracks)
    if isinstance(join,bool) or not isinstance(join,int) or not 0<=join<len(p['joins']):raise ValueError('请选择现有交接')
    if p.get('performance') is not None or p['target_bpm'] is not None:raise ValueError('铺垫桥先保留原速，不能覆盖已有表演')
    a,b=p['clips'][join:join+2]
    for c in (a,b):
        if c['preserve_original'] or c['semitones'] or c['bass_db'] or any(c.get(k) for k in ('effects','warp_map','gain_envelope')):
            raise ValueError('不能覆盖原貌锁或已编辑片段；请以原曲新建铺垫版本')
        if c['role']!='full_mix' or tracks[c['track_id']].get('stem_role') or tracks[c['track_id']].get('preserve_original'):
            raise ValueError('铺垫桥需要两份原曲与对应真实分轨')
        if c['end']-c['start']<8:raise ValueError('铺垫桥两侧至少需要8秒素材')
    for sid,role,index in sources:
        parent=(a,b)[index]
        t=tracks.get(sid,{})
        if t.get('stem_role')!=role or t.get('source_track_id')!=parent['track_id'] or t.get('preserve_original'):
            raise ValueError('需要来自对应原曲的真实分轨，不能重新贴标签')
        if abs(t.get('duration',0)-tracks[parent['track_id']]['duration'])>.05:
            raise ValueError('分轨与原曲时长不同，不能保证人声时间连续')
    before=set_layout(p);a_length=before['clips'][join]['duration']
    # Do not hide the preceding handoff inside the new six-second preparation.
    previous=p['joins'][join-1]['seconds'] if join else 0
    if a_length-6-previous<.25:raise ValueError('前一交接占用了铺垫区间')
    return p


def plan_vocal_bridge(plan,tracks,join,outgoing_instrumental,incoming_vocal):
    p=bridge_inputs(plan,tracks,join,[(outgoing_instrumental,'instrumental',0),(incoming_vocal,'vocal',1)])
    a,b=p['clips'][join:join+2]
    backing_gain=_stem_gain(a,tracks[outgoing_instrumental])
    vocal_gain=_stem_gain(b,tracks[incoming_vocal])
    a_length=set_layout(p)['clips'][join]['duration']
    p['joins'][join]=dict(kind='cut',seconds=0)
    a['gain_envelope']=[dict(time=0,gain=1),dict(time=a_length-6,gain=1),dict(time=a_length-5,gain=0)]
    b_start=b['start'];b['start']+=2
    b['gain_envelope']=[dict(time=0,gain=0),dict(time=2,gain=1)]
    layout=set_layout(p);boundary=layout['clips'][join]['end']
    p['performance']=dict(events=[
        dict(track_id=outgoing_instrumental,start=a['end']-6,end=a['end'],time=boundary-6,
            gain_db=backing_gain,semitones=0,gain_envelope=[dict(time=0,gain=0),dict(time=1,gain=1),dict(time=3.5,gain=1),dict(time=4,gain=0)]),
        dict(track_id=incoming_vocal,start=b_start,end=b_start+4,time=boundary-2,
            gain_db=vocal_gain,semitones=0,gain_envelope=[dict(time=0,gain=0),dict(time=.06,gain=1),dict(time=2,gain=1),dict(time=4,gain=0)])
    ],drums=[])
    p=validate_set(p,tracks)
    return dict(project=p,name='伴奏收束 · 清唱铺垫 · 渐进接管',techniques=['T59','T62','T37'],
        reasons=['先用前曲自己的真实伴奏替代尾段主唱，再收束伴奏，为后曲清唱留出两秒空间。',
            '后曲先出现人声，随后两秒内逐步进入整套伴奏；人声分轨与原曲保持相同源时间，用互补包络交接，避免主唱重唱或跳词。',
            '分轨按保存时的共同缩放补偿，使主唱与同位置原曲交接时不因保存降幅突然变响或变轻；未按响度猜测增益。',
            '这是密度重构和清唱桥，不是已确认乐句、首拍或调性兼容；分轨质感与情绪起伏仍需试听。'],
        evidence=dict(join=join,method='accompaniment_solo_rebuild',preparation_start=boundary-6,
            vocal_teaser_start=boundary-2,backing_rebuild_start=boundary,backing_rebuild_end=boundary+2,
            solo_vocal_seconds=2,vocal_source_continuity=True,confirmed_phrase=False,
            shared_scale_compensated=True,
            stem_gains=[dict(track_id=sid,scale=_stem_scale(tracks[sid]),gain_db=gain)
                        for sid,gain in ((outgoing_instrumental,backing_gain),(incoming_vocal,vocal_gain))],
            source_roles=[dict(track_id=outgoing_instrumental,role='instrumental',source_track_id=a['track_id']),
                          dict(track_id=incoming_vocal,role='vocal',source_track_id=b['track_id'])]))
