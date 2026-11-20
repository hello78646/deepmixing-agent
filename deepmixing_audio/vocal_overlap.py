"""Bounded review of two source-aligned vocal contributions, not lyric ASR.

Only explicit vocal sources or unique, scaled, same-source vocal stems count.
No singer identity, phrase completion, musical approval or edits are inferred.
"""
import math

import numpy as np

from .accompaniment_bridge import _stem_scale
from .automatic_analysis import _decode_stereo, RATE
from .continuous import set_layout


def _source(clip, tracks):
    track=tracks[clip['track_id']]
    if track.get('stem_role')=='vocal':
        return track,1.
    if clip['role']!='full_mix' or track.get('stem_role'):
        raise ValueError('不是可声明含主唱的源路径')
    stems=[t for t in tracks.values() if t.get('source_track_id')==track['id']
           and t.get('stem_role')=='vocal' and abs(t.get('duration',-1)-track['duration'])<=.05]
    if len(stems)!=1 or 'shared_scale' not in stems[0].get('separation_quality',{}):
        raise ValueError('缺少唯一同源及共同缩放证据')
    return stems[0],1/_stem_scale(stems[0])


def _ranges(mask, edges, origin):
    changes=np.diff(np.r_[False,mask,False].astype(int))
    return [dict(start=round(float(origin+edges[a]),6),end=round(float(origin+edges[b]),6))
            for a,b in zip(np.flatnonzero(changes==1),np.flatnonzero(changes==-1))]


def review_vocal_overlap(plan, tracks, join_index=None, *, resolve_path=None):
    """Review up to 8s of actual native-speed overlap without changing a plan.

    Stereo 20ms RMS / 10ms hop retains anti-phase evidence. Intrinsic activity
    is gated before applying envelopes, then effective output levels use a
    shared audibility floor. Quiet/effective overlap is acoustic, not semantic.
    """
    unknown=dict(status='unknown',review_only=True,audio_processed=False,
                 confirmed_phrase=False,confirmed_vocal_conflict=False,
                 reason='没有完整的双侧人声活动证据，保留人工核听')
    try:
        if plan.get('performance') or plan.get('echo'):
            return unknown
        is_set=plan.get('kind')=='continuous_set'
        if is_set:
            i=plan['preview_join'] if join_index is None else join_index
            layout=set_layout(plan)
            if any(j['kind']=='echo' for j in plan['joins']):
                return unknown
            clips=plan['clips'][i:i+2]
            origins=[layout['clips'][j]['start'] for j in (i,i+1)]
            begin,finish=layout['joins'][i]['start'],layout['joins'][i]['end']
            fades=[(plan['joins'][j-1]['seconds'] if j else 0,
                    plan['joins'][j]['seconds'] if j<len(plan['joins']) else 0) for j in (i,i+1)]
        else:
            clips=[plan['a'],plan['b']]
            length=clips[0]['end']-clips[0]['start']
            transition=plan['mode']=='transition'
            origins=[0.,length-plan['transition_seconds'] if transition else plan['b_offset_seconds']]
            begin=origins[1]
            finish=min(length,origins[1]+clips[1]['end']-clips[1]['start'])
            fades=[(0,plan['transition_seconds']),(plan['transition_seconds'],0)] if transition else [(0,0),(0,0)]
        if finish<=begin:
            return dict(unknown,status='not_applicable',reason='没有双侧干声同响区；不评价片段外的尾音或语义')
        if len(clips)!=2 or finish-begin<.06 or any(
                c.get('effects') or c.get('warp_map') or c['semitones'] or c['bass_db'] or
                (plan['target_bpm'] is not None and abs(plan['target_bpm']/c['bpm']-1)>1e-9) for c in clips):
            return unknown
        end=min(finish,begin+8.)
        count=round((end-begin)*RATE)
        frame,hop=round(.02*RATE),round(.01*RATE)
        starts=np.arange(0,count-frame+1,hop)
        centers=(starts+frame/2)/RATE
        edges=np.r_[0.,(centers[:-1]+centers[1:])/2,count/RATE]
        clock=begin+np.arange(count)/RATE
        evidence=[]
        for clip,origin,(fade_in,fade_out) in zip(clips,origins,fades):
            track,scale=_source(clip,tracks)
            source_start=clip['start']+begin-origin
            if not 0<=source_start<source_start+count/RATE<=track['duration']+1/RATE:
                return unknown
            path=resolve_path(track['id']) if resolve_path else track['path']
            raw=_decode_stereo(path,source_start,count/RATE).astype(float)
            if raw.ndim!=2 or raw.shape[1]!=2 or len(raw)<count or not np.isfinite(raw).all():
                return unknown
            raw=raw[:count]*scale
            local=clock-origin;length=clip['end']-clip['start']
            level=np.clip(local/.005,0,1)*np.clip((length-local)/.005,0,1)
            if fade_in:level*=np.sin(np.clip(local/fade_in,0,1)*math.pi/2)
            if fade_out:level*=np.sin(np.clip((length-local)/fade_out,0,1)*math.pi/2)
            points=clip.get('gain_envelope',[])
            if points:level*=np.interp(local,[p['time'] for p in points],[p['gain'] for p in points])
            effective=raw*(level*10**(clip['gain_db']/20))[:,None]
            def rms(x):
                power=np.mean(x*x,axis=1)
                cumulative=np.r_[0.,np.cumsum(power)]
                return np.sqrt(np.maximum(0,(cumulative[starts+frame]-cumulative[starts])/frame))
            threshold=max(.003,float(np.sqrt(np.mean(raw*raw)))*.2)
            evidence.append(dict(intrinsic=rms(raw)>=threshold,effective=rms(effective),
                track_id=track['id'],source_start=source_start,source_end=source_start+count/RATE,
                stem_compensation=scale,intrinsic_threshold_rms=threshold,
                contribution_rms=float(np.sqrt(np.mean(effective*effective)))))
        floor=max(.0003,max(float(np.max(e['effective'])) for e in evidence)*.05)
        active=[e['intrinsic'] & (e['effective']>=floor) for e in evidence]
        lengths=np.diff(edges)
        sources=[{k:v for k,v in e.items() if k not in ('intrinsic','effective')} |
                 dict(active_seconds=round(float(np.sum(lengths[mask])),6),
                      active_output_ranges=_ranges(mask,edges,begin)) for e,mask in zip(evidence,active)]
        simultaneous=active[0]&active[1]
        quiet=~(active[0]|active[1])
        return dict(unknown,status='measured',reason='已测量双侧有效人声活动，重叠不等于抢唱或歌词不完整',
            output_start=begin,output_end=begin+count/RATE,total_overlap_seconds=finish-begin,
            scope='actual_overlap_first_up_to_8_seconds',sources=sources,
            simultaneous_activity_seconds=round(float(np.sum(lengths[simultaneous])),6),
            simultaneous_output_ranges=_ranges(simultaneous,edges,begin),
            neither_active_seconds=round(float(np.sum(lengths[quiet])),6),
            neither_active_output_ranges=_ranges(quiet,edges,begin),effective_threshold_rms=floor,
            limitations=['活动含和声、混响及分离漏音，不区分主唱身份、具体字词或完整乐句。',
                         '只核对所列窗口及原速干声路径，不分析未建模效果、演奏事件或最终限幅。',
                         '没有检出同响也不能证明不抢唱；不据此自动裁剪、撤声或移动入口。'])
    except (KeyError,IndexError,TypeError,ValueError,RuntimeError,OSError,OverflowError):
        return unknown
