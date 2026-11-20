"""Canonical user-controlled audio plans. Reject unsupported or unsafe settings."""
import math

from .advanced_audio import validate_effects
from .elastic_audio import validate_warp_map
from .performance_audio import validate_performance


def number(value, name, low, high):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(name+'须为有限数字')
    if not low<=value<=high:
        raise ValueError(f'{name}须在{low}至{high}之间')
    return float(value)


def validate_project(project, tracks):
    if not isinstance(project,dict):
        raise ValueError('工程须为JSON对象')
    allowed={'version','mode','a','b','target_bpm','transition_seconds','b_offset_seconds','echo','prompt','title','musical_guides','performance'}
    if set(project)-allowed:
        raise ValueError('存在本版不支持的工程参数')
    if project.get('version',1)!=1 or project.get('mode') not in ('transition','mashup'):
        raise ValueError('不支持的工程版本或模式')
    target=project.get('target_bpm')
    if target is not None:
        target=number(target,'目标BPM',40,240)
    out={'version':1,'mode':project['mode'],'target_bpm':target,
         'transition_seconds':number(project.get('transition_seconds',8),'过渡秒数',0,120),
         'b_offset_seconds':number(project.get('b_offset_seconds',0),'伴奏偏移',0,60),
         'echo':number(project.get('echo',0),'回声强度',0,.4)}
    for label in ('title','prompt'):
        text=project.get(label,'')
        if not isinstance(text,str) or len(text)>2000:
            raise ValueError(label+'须为不超过2000字的文本')
        out[label]=text
    lengths=[]
    for name in ('a','b'):
        slot=project.get(name)
        if not isinstance(slot,dict) or set(slot)-{'track_id','role','start','end','bpm','gain_db','bass_db','semitones','preserve_original','gain_envelope','effects','warp_map'}:
            raise ValueError('轨道参数格式错误或含不支持的控制项')
        key=slot.get('track_id')
        if not isinstance(key,str) or key not in tracks:
            raise ValueError('素材不存在，请重新上传对应素材')
        duration=tracks[key]['duration']
        start=number(slot.get('start',0),'起点',0,duration)
        end=number(slot.get('end',duration),'终点',0,duration+.001)
        if end-start<.25:
            raise ValueError('选段至少需要0.25秒')
        role=slot.get('role','full_mix')
        if role not in ('full_mix','vocal','instrumental'):
            raise ValueError('不支持的素材角色')
        bpm=slot.get('bpm')
        if bpm is not None:
            bpm=number(bpm,'素材BPM',40,240)
        if target is not None and bpm is None:
            raise ValueError('指定目标BPM时，两轨都须填写经确认的素材BPM；也可留空目标保持原速')
        speed=target/bpm if target is not None else 1
        if not .5<=speed<=2:
            raise ValueError('本版允许0.5至2倍变速，请调整目标BPM')
        locked=slot.get('preserve_original',False)
        if not isinstance(locked,bool):
            raise ValueError('保持原貌须为布尔值')
        value={'track_id':key,'role':role,'start':start,'end':end,'bpm':bpm,
               'gain_db':number(slot.get('gain_db',-6),'音量dB',-48,6),
               'bass_db':number(slot.get('bass_db',0),'低频dB',-18,6),
               'semitones':number(slot.get('semitones',0),'移调半音',-12,12),
               'preserve_original':locked}
        effects = validate_effects(slot.get('effects', []), (end-start)/speed, locked, tracks)
        if effects:
            value['effects'] = effects
        warp=validate_warp_map(slot.get('warp_map'),end-start,locked)
        if warp:value['warp_map']=warp
        if locked and (abs(speed-1)>1e-8 or value['semitones'] or value['bass_db'] or out['echo']):
            raise ValueError('保持原貌已锁定：请取消变速、移调、低频修改和回声，或明确解除锁定')
        if 'gain_envelope' in slot:
            points=slot['gain_envelope']
            if not isinstance(points,list) or not 1<=len(points)<=16:
                raise ValueError('分段音量需要1至16个控制点；取消时请移除该字段')
            if locked:
                raise ValueError('保持原貌已锁定：不能添加分段抽空或音量编排')
            envelope=[]
            for point in points:
                if not isinstance(point,dict) or set(point)!={'time','gain'}:
                    raise ValueError('分段音量点只能包含time和gain')
                time=number(point['time'],'编排时间（变速后片段内秒数）',0,(end-start)/speed)
                gain=number(point['gain'],'编排相对音量',0,1)
                if envelope and time-envelope[-1]['time']<.005-1e-9:
                    raise ValueError('编排控制点须递增，间隔至少5毫秒')
                envelope.append({'time':time,'gain':gain})
            value['gain_envelope']=envelope
        out[name]=value
        lengths.append((end-start)/speed)
    if out['mode']=='mashup' and (out['a']['role']!='vocal' or out['b']['role']!='instrumental'):
        raise ValueError('叠搭需要A为已有独立人声、B为已有伴奏；本版尚未自动分轨')
    if out['mode']=='transition' and out['transition_seconds']>min(lengths):
        raise ValueError('过渡不能长于任一轨变速后的选段')
    if out['mode']=='transition' and 0<out['transition_seconds']<1/44100:
        raise ValueError('过渡须为0或至少一个采样点')
    if 'musical_guides' in project:
        guides=project['musical_guides']
        if not isinstance(guides,dict) or set(guides)-{'a','b'}:
            raise ValueError('音乐标记须对应A/B轨')
        out['musical_guides']={}
        for slot,guide in guides.items():
            fields={'track_id','start','end','bpm','anchor','marker','key_tonic','key_mode','key_source'}
            if not isinstance(guide,dict) or not fields<=set(guide) or set(guide)-fields-{'bar_anchor','beats_per_bar'} or guide['track_id']!=out[slot]['track_id']:
                raise ValueError('音乐标记的来源或字段不匹配')
            duration=tracks[out[slot]['track_id']]['duration']
            start=number(guide['start'],'标记区域起点',0,duration)
            end=number(guide['end'],'标记区域终点',start,duration+.001)
            if not .25<=end-start<=60.001:
                raise ValueError('音乐标记区域须在0.25至60秒之间')
            tonic,mode=guide['key_tonic'],guide['key_mode']
            if tonic is not None and (isinstance(tonic,bool) or not isinstance(tonic,int) or not 0<=tonic<12):
                raise ValueError('调性主音须为0至11的整数或未知')
            if (tonic is None)!=(mode is None) or mode not in (None,'major','minor'):
                raise ValueError('调性模式无效')
            source=guide['key_source']
            if source not in ('estimated','user','unknown') or (source=='unknown')!=(tonic is None):
                raise ValueError('调性来源与标记不一致')
            out['musical_guides'][slot]={'track_id':guide['track_id'],'start':start,'end':end,
                'bpm':number(guide['bpm'],'标记BPM',40,240) if guide['bpm'] is not None else None,
                'anchor':number(guide['anchor'],'网格起点',start,end),
                'marker':number(guide['marker'],'手动标记',start,end),
                'key_tonic':tonic,'key_mode':mode,'key_source':source}
            if 'bar_anchor' in guide or 'beats_per_bar' in guide:
                if guide.get('beats_per_bar') not in (3,4) or isinstance(guide.get('beats_per_bar'),bool):
                    raise ValueError('手动小节参考仅支持3或4拍')
                out['musical_guides'][slot].update(bar_anchor=number(guide.get('bar_anchor'),'手动小节首拍',start,end),beats_per_bar=guide['beats_per_bar'])
    if 'performance' in project:
        out['performance']=validate_performance(project['performance'],tracks,durations(out)[2]+(min(1,30/(target or 120)) if out['echo'] else 0),
            locked_track_ids(tracks,[out['a'],out['b']]))
    return out


def locked_track_ids(tracks,slots):
    """A locked original cannot be resampled via a newly separated child."""
    locked={s['track_id'] for s in slots if s.get('preserve_original')}
    changed=True
    while changed:
        expanded=locked|{key for key,t in tracks.items() if t.get('source_track_id') in locked}
        changed=expanded!=locked;locked=expanded
    return locked


def durations(project):
    def length(slot):
        speed=project['target_bpm']/slot['bpm'] if project['target_bpm'] else 1
        return (slot['end']-slot['start'])/speed
    a,b=length(project['a']),length(project['b'])
    total=a+b-project['transition_seconds'] if project['mode']=='transition' else max(a,b+project['b_offset_seconds'])
    return a,b,total
