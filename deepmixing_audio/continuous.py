"""Explicit, bounded multi-clip DJ sets. No remote assets or automatic pitch edits."""
import wave
import tempfile
import copy
import math
from pathlib import Path

from .models import number,validate_project,locked_track_ids
from .engine import _slot_filter,measure,prepared_warps,finish_performance
from .performance_audio import validate_performance
from .analysis import INPUT_OPTIONS
from .runtime import run_ffmpeg
from .handoff_refinement import choose_transition_seconds, phase_alignment
from .dynamics_audio import prepared_sidechains
from .stereo_audit import audit_stereo
from .render_audit import attach_render_audit


def validate_set(plan,tracks):
    fields={'kind','version','title','target_bpm','preview_join','clips','joins'}
    if not isinstance(plan,dict) or not fields<=set(plan) or set(plan)-fields-{'performance'} or plan['kind']!='continuous_set' or plan['version']!=1:
        raise ValueError('请选择有效的连续串烧工程')
    clips,joins=plan['clips'],plan['joins']
    if not isinstance(clips,list) or not 2<=len(clips)<=8 or not isinstance(joins,list) or len(joins)!=len(clips)-1:
        raise ValueError('连续串烧需要2–8个片段和对应交接')
    title=plan['title']
    if not isinstance(title,str) or len(title)>120:
        raise ValueError('串烧名称须为不超过120字的文本')
    canonical=[]
    for clip in clips:
        pair={'mode':'transition','a':clip,'b':clip,'target_bpm':plan['target_bpm'],'transition_seconds':0}
        canonical.append(validate_project(pair,tracks)['a'])
    target=number(plan['target_bpm'],'串烧目标BPM',40,240) if plan['target_bpm'] is not None else None
    lengths=[(c['end']-c['start'])/(target/c['bpm'] if target else 1) for c in canonical]
    transitions=[]
    for i,j in enumerate(joins):
        if not isinstance(j,dict) or set(j)!={'kind','seconds'} or j['kind'] not in ('smooth','cut','echo'):
            raise ValueError('不支持的串烧交接方式')
        seconds=number(j['seconds'],'串烧交接秒数',0,min(120,lengths[i],lengths[i+1]))
        if j['kind']=='cut' and seconds!=0 or 0<seconds<1/44100:
            raise ValueError('直接切入交接应为0秒，其余交接至少一个采样点')
        if j['kind']=='echo' and canonical[i]['preserve_original']:
            raise ValueError('前一片段保持原貌已锁定，不能添加尾段回声')
        transitions.append({'kind':j['kind'],'seconds':seconds})
    for i in range(1,len(lengths)-1):
        if transitions[i-1]['seconds']+transitions[i]['seconds']>lengths[i]-.25:
            raise ValueError('前后交接吞没中间片段，请缩短交接或增加选段，至少保留0.25秒')
    index=plan['preview_join']
    if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(transitions):
        raise ValueError('请选择现有的交接位置试听')
    if sum(lengths)-sum(j['seconds'] for j in transitions)>3600:
        raise ValueError('本版连续串烧成品最多60分钟')
    result={'kind':'continuous_set','version':1,'title':title,'target_bpm':target,'preview_join':index,'clips':canonical,'joins':transitions}
    if 'performance' in plan:
        result['performance']=validate_performance(plan['performance'],tracks,set_layout(result)['duration'],locked_track_ids(tracks,canonical))
    return result


def set_layout(plan):
    target=plan['target_bpm'];clips=[];joins=[];start=0
    for i,c in enumerate(plan['clips']):
        length=(c['end']-c['start'])/(target/c['bpm'] if target else 1)
        clips.append({'start':start,'end':start+length,'duration':length})
        if i<len(plan['joins']):
            j=plan['joins'][i];start+=length-j['seconds'];joins.append({'start':start,'end':start+j['seconds'],'kind':j['kind']})
    return {'clips':clips,'joins':joins,'duration':clips[-1]['end']}


def _arrangement_windows(track):
    """Collect bounded evidence for ordering and mid-song join candidates.

    The set planner deliberately keeps the original tempo and does not treat a
    beat estimate as permission to stretch.  A few 24-second windows are enough
    to find compatible local material while keeping a large set responsive.
    """
    duration=float(track.get('duration') or 0)
    basic=track.get('analysis',{}) if isinstance(track.get('analysis'),dict) else {}
    starts=[]
    for value in (0.,duration*.33-12.,duration*.66-12.,duration-24.):
        value=max(0.,min(max(0.,duration-.25),float(value)))
        if not any(abs(value-old)<.5 for old in starts): starts.append(value)
    starts=starts[:4]
    windows=[]
    for start in starts:
        end=min(duration,start+24.)
        if end-start<.25: continue
        report=None
        path=track.get('path')
        if path:
            try:
                from .automatic_analysis import inspect_track
                report=inspect_track(track,start,end)
            except (OSError,RuntimeError,ValueError,TypeError):
                report=None
        report=report if isinstance(report,dict) else {}
        grid=report.get('grid',{}) if isinstance(report.get('grid'),dict) else {}
        bpm=grid.get('bpm') if grid.get('status')=='estimated' else basic.get('bpm_estimate')
        key=report.get('key',{}) if isinstance(report.get('key'),dict) else {}
        changes=[]
        for item in (report.get('phrase_changes',[]),report.get('changes',[])):
            for candidate in item if isinstance(item,list) else []:
                if not isinstance(candidate,dict): continue
                at=candidate.get('time'); strength=candidate.get('strength',0)
                if isinstance(at,(int,float)) and not isinstance(at,bool) and math.isfinite(float(at)):
                    if isinstance(strength,(int,float)) and math.isfinite(float(strength)):
                        changes.append((float(at),float(strength)))
        # Keep only distinct candidates and expose the best edge on each side.
        changes=sorted({(round(at,3),round(strength,3)) for at,strength in changes})
        # Do not trust a detector candidate sitting on the analysis-window
        # edge; those are commonly caused by the window itself ending.
        near_start=max((row for row in changes if row[0]-start<=8 and row[0]>=start+1.0),default=None,key=lambda row:row[1])
        near_end=max((row for row in changes if end-row[0]<=8 and row[0]<=end-1.0),default=None,key=lambda row:row[1])
        windows.append({'start':round(start,6),'end':round(end,6),
                        'bpm':float(bpm) if isinstance(bpm,(int,float)) and math.isfinite(float(bpm)) else None,
                        'grid':copy.deepcopy(grid),
                        'lyrics':copy.deepcopy(report.get('lyrics')),
                        'key':copy.deepcopy(key),'energy':report.get('energy'),
                        'low_frequency_ratio':report.get('low_frequency_ratio'),
                        'grid_review':bool(grid.get('status')!='estimated' or grid.get('review_required') or
                                           grid.get('diagnostics',{}).get('review_required',True) or
                                           grid.get('phase_drift',{}).get('review_required',True) or
                                           (grid.get('tempo_layer',{}).get('layer') in ('half_candidate','double_candidate') and
                                            grid.get('tempo_layer',{}).get('review_required',True)) or
                                           report.get('tempo_changes') or report.get('beat_drift_candidates')),
                        'boundary_in':dict(time=near_start[0],strength=near_start[1]) if near_start else None,
                        'boundary_out':dict(time=near_end[0],strength=near_end[1]) if near_end else None})
    if not windows:
        windows=[{'start':0.,'end':duration,'bpm':basic.get('bpm_estimate'),
                  'grid':{},
                  'key':{},'energy':None,'low_frequency_ratio':None,'grid_review':True,
                  'boundary_in':None,'boundary_out':None}]
    return windows


def _relation_score(left,right):
    from .music_guides import compare_keys
    if not isinstance(left,dict) or not isinstance(right,dict): return .35
    relation=compare_keys(left,right).get('relation')
    return {'same':1.,'relative':.86,'neighbor':.7,'review':.2,'unknown':.35}.get(relation,.35)


def _pair_candidate(left_windows,right_windows,style):
    best=None
    for li,left in enumerate(left_windows):
        for ri,right in enumerate(right_windows):
            la,lb=left.get('bpm'),right.get('bpm')
            if isinstance(la,(int,float)) and isinstance(lb,(int,float)) and max(la,lb)>0:
                gap=abs(la-lb)/max(la,lb)
                tempo=max(0.,1.-gap/.12)
            else:
                gap=None;tempo=.35
            energy=.5
            if isinstance(left.get('energy'),(int,float)) and isinstance(right.get('energy'),(int,float)):
                scale=max(abs(float(left['energy'])),abs(float(right['energy'])),1e-6)
                energy=max(0.,1.-abs(float(left['energy'])-float(right['energy']))/scale)
            edge=(left.get('boundary_out') is not None)+(right.get('boundary_in') is not None)
            boundary=.35+.325*edge
            phase=phase_alignment(
                (left.get('boundary_out') or {}).get('time', left.get('end', 0.0)), left.get('grid', {}),
                (right.get('boundary_in') or {}).get('time', right.get('start', 0.0)), right.get('grid', {}))
            score=.44*tempo+.24*_relation_score(left.get('key',{}),right.get('key',{}))+.16*energy+.10*boundary+.06*phase.get('score',.35)
            if style=='rising' and isinstance(left.get('energy'),(int,float)) and isinstance(right.get('energy'),(int,float)):
                score+=.08 if right['energy']>=left['energy'] else -.08
            item={'score':round(float(score),6),'left_window':li,'right_window':ri,
                  'tempo_gap':round(float(gap),6) if gap is not None else None,
                  'bpm':round(float((la+lb)/2),6) if isinstance(la,(int,float)) and isinstance(lb,(int,float)) else None,
                  'grid_review':bool(left.get('grid_review') or right.get('grid_review')),
                  'phase_alignment':phase,
                  'tempo_policy':'original_speed_only',
                  'boundary_out':copy.deepcopy(left.get('boundary_out')),
                  'boundary_in':copy.deepcopy(right.get('boundary_in'))}
            if best is None or item['score']>best['score']: best=item
    return best or {'score':0.,'left_window':0,'right_window':0,'tempo_gap':None,
                    'tempo_policy':'original_speed_only','boundary_out':None,'boundary_in':None}


def _transition_candidates(item,tracks,windows_by_track=None):
    """Expose bounded alternatives for one join without silently applying one.

    Public DJ tutorials often use a short 4/8-beat handoff, while structure-
    aware work may prefer an incoming/outgoing boundary.  These are audition
    candidates only: a candidate never asserts that a phrase or lyric is
    confirmed, and it never authorizes tempo or pitch edits.
    """
    left=tracks[item['from_id']];right=tracks[item['to_id']]
    left_len=max(.25,float(left.get('duration') or 0.25));right_len=max(.25,float(right.get('duration') or 0.25))
    cap=max(.12,min(left_len,right_len)*.2)
    bpms=[]
    for key,index in (('from_id','left_window'),('to_id','right_window')):
        track=tracks[item[key]]
        windows=windows_by_track[item[key]] if windows_by_track is not None else _arrangement_windows(track)
        value=windows[item[index]].get('bpm') if isinstance(item.get(index),int) and 0<=item[index]<len(windows) else None
        if isinstance(value,(int,float)) and math.isfinite(float(value)) and value>0: bpms.append(float(value))
    bpm=sum(bpms)/len(bpms) if bpms else None
    candidates=[]
    for beats,label in ((4,'public_4_beat_short'),(8,'public_8_beat_short')):
        seconds=min(cap,beats*60/bpm) if bpm else min(cap,1.5 if beats==4 else 3.)
        candidates.append({'method':label,'beats':beats,'kind':'smooth','seconds':round(seconds,4),
                          'applied':False,'review_required':True,
                          'reason':'公开接歌方法只提供短交接候选；需试听歌词、弱起、尾音与低频主导。',
                          'tempo_policy':'original_speed_only'})
    out=item.get('boundary_out');inc=item.get('boundary_in')
    if out and inc:
        confidence=min(float(out.get('strength',0)),float(inc.get('strength',0)))
        candidates.append({'method':'local_structure_boundary','kind':'smooth','seconds':round(min(cap,2.),4),
                           'left_source_time':out.get('time'),'right_source_time':inc.get('time'),
                           'confidence':round(confidence,4),'applied':False,
                           'preferred_when_user_requests_mid_song':True,
                           'review_required':confidence<.42,
                           'reason':'结构变化候选；只有两侧边界证据和人工试听共同通过时才可切入。',
                           'tempo_policy':'original_speed_only'})
    return candidates


def _arrange_ids(ids,tracks,style):
    windows={key:_arrangement_windows(tracks[key]) for key in dict.fromkeys(ids)}
    pair={}
    for left in windows:
        for right in windows:
            # Self transitions are valid when a user intentionally repeats a
            # track instance; they are scored by the same local evidence as a
            # normal handoff instead of being treated as an invalid edge.
            pair[(left,right)]=_pair_candidate(windows[left],windows[right],style)
    # Dynamic programming over *clip positions* (at most eight clips) finds a
    # coherent order without pretending the score is a taste or quality
    # probability.  Positions are intentional: the same track may be selected
    # twice for a return-to-hook, echo loop, or a repeated user-requested clip.
    dp={}
    for position in range(len(ids)): dp[(1<<position,position)]=(0.,[position])
    for mask in range(1,1<<len(ids)):
        for last_position in range(len(ids)):
            state=dp.get((mask,last_position))
            if state is None: continue
            score,path=state
            last=ids[last_position]
            for next_position,nxt in enumerate(ids):
                bit=1<<next_position
                if mask&bit: continue
                candidate=(score+pair[(last,nxt)]['score'],path+[next_position])
                current=dp.get((mask|bit,next_position))
                if (current is None or candidate[0]>current[0]+1e-9 or
                    (abs(candidate[0]-current[0])<=1e-9 and
                     tuple(candidate[1])<tuple(current[1]))):
                    dp[(mask|bit,next_position)]=candidate
    full=(1<<len(ids))-1
    score,path=max((dp[(full,last_position)] for last_position in range(len(ids))),
                   key=lambda value:(value[0],tuple(-position for position in value[1])))
    # Do not reorder a user's selection on a weak or noisy advantage.  This
    # keeps synthetic/ambiguous material stable while still allowing a clear
    # local-tempo/structure win to change the order automatically.
    original_score=sum(pair[(left,right)]['score'] for left,right in zip(ids,ids[1:]))
    if score <= original_score + .08*max(1,len(ids)-1):
        score=float(original_score)
        path=list(range(len(ids)))
    path=[ids[position] for position in path]
    evidence=[]
    for left,right in zip(path,path[1:]):
        item=copy.deepcopy(pair[(left,right)]);item.update(from_id=left,to_id=right)
        item['transition_candidates']=_transition_candidates(item,tracks,windows)
        evidence.append(item)
    return path,windows,evidence,score


def _guard_arrangement_boundary(candidate,clip,window,track,tail):
    """A structure change alone cannot authorize deleting unknown vocals."""
    from .transition_refinement import _guard_trim_candidates
    field='end' if tail else 'start'
    current=clip[field]
    decision={'applied':False,'candidate':copy.deepcopy(candidate),'confirmed_phrase':False}
    if not candidate or candidate.get('strength',0)<.42:
        decision['reason']='结构证据不足，保留源边界。'
        return decision
    at=candidate.get('time')
    if (not isinstance(at,(int,float)) or isinstance(at,bool) or not math.isfinite(at) or
        not clip['start']<=at<=clip['end'] or
        (at-clip['start'] if tail else clip['end']-at)<.25-1e-9):
        decision['reason']='候选超出当前选段或无法保留最短片段，保留源边界。'
        return decision
    safe,guard=_guard_trim_candidates([at],current,window,clip,track,tail)
    decision['vocal_guard']=guard
    # A short local transcript cannot establish that the rest of a song has
    # no lyrics. Require coverage of the entire removed interval for mixes.
    lo,hi=sorted((at,current))
    lyrics=window.get('lyrics')
    coverage=(isinstance(lyrics,dict) and
              all(isinstance(lyrics.get(k),(int,float)) and not isinstance(lyrics[k],bool) and
                  math.isfinite(lyrics[k]) for k in ('start','end')) and
              lyrics['start']<=lo and lyrics['end']>=hi)
    decision['removed_interval_covered']=bool(coverage)
    if safe and (guard['source_nonvocal'] or coverage):
        clip[field]=float(at)
        decision.update(applied=abs(at-current)>1e-7,reason=guard['reason'])
    else:
        decision['reason']=guard['reason'] if not safe else '待裁区间未被唱词分析完整覆盖，保留源边界。'
    return decision


def recommend_set(ids,tracks,style='journey'):
    if not isinstance(ids,list) or not 2<=len(ids)<=8 or any(not isinstance(k,str) or k not in tracks for k in ids):
        raise ValueError('请从本机素材选择2–8段')
    if style not in ('journey','rising','echo'):
        raise ValueError('请选择连贯、递进或回声编排')
    arranged,windows,evidence,order_score=_arrange_ids(ids,tracks,style)
    clips=[dict(track_id=k,role='full_mix',start=0.,end=float(tracks[k]['duration'])) for k in arranged]
    # Keep unknown vocals intact. Structure candidates may trim only with the
    # shared source guard; source ranges belong to clip instances, not songs.
    selected_windows=[[] for k in arranged]
    for i,item in enumerate(evidence):
        left,right=item['from_id'],item['to_id']
        li,ri=item.get('left_window',0),item.get('right_window',0)
        selected_windows[i].append(li)
        selected_windows[i+1].append(ri)
        out=item.get('boundary_out');inc=item.get('boundary_in')
        item['boundary_decisions']={
            'out':_guard_arrangement_boundary(out,clips[i],windows[left][li],tracks[left],True),
            'in':_guard_arrangement_boundary(inc,clips[i+1],windows[right][ri],tracks[right],False)}
    treatments=[]
    for i,k in enumerate(arranged):
        t=tracks[k]
        start,end=clips[i]['start'],clips[i]['end']
        local_bpms=[windows[k][index].get('bpm') for index in selected_windows[i]
                    if isinstance(index,int) and 0<=index<len(windows[k])]
        local_bpms=[float(value) for value in local_bpms if isinstance(value,(int,float)) and math.isfinite(float(value))]
        bpm_hint=(sum(local_bpms)/len(local_bpms) if local_bpms else t.get('analysis',{}).get('bpm_estimate'))
        local_low=[windows[k][index].get('low_frequency_ratio') for index in selected_windows[i]
                   if isinstance(index,int) and 0<=index<len(windows[k])]
        local_low=[float(value) for value in local_low if isinstance(value,(int,float)) and math.isfinite(float(value))]
        low_hint=(sum(local_low)/len(local_low) if local_low else None)
        treatments.append({'track_id':k,'clip_index':i,'window_low_frequency_ratio':round(low_hint,6) if low_hint is not None else None,
                           'suggested_bass_db':-2 if low_hint is not None and low_hint>=.55 else 0,
                           'applied':False,
                           'reason':'仅作为交接前调音候选；实际削减须用交接重叠窗口复测。'})
        clips[i].update({'start':round(start,6),'end':round(end,6),
            'bpm':bpm_hint,'gain_db':-7+(i/(len(ids)-1)*2 if style=='rising' else 0),
            'bass_db':0,'semitones':0,'preserve_original':False})
    # Each middle clip contributes to two overlaps. Budget using its selected
    # duration, with at least .25 seconds outside either handoff.
    caps=[min((c['end']-c['start'])*.2,
              max(0.,(c['end']-c['start']-.25)/2) if 0<i<len(clips)-1 else 120.)
          for i,c in enumerate(clips)]
    joins=[];reasons=[]
    for i in range(len(clips)-1):
        evidence_item=evidence[i] if i < len(evidence) else {}
        # Ordering can select a middle window even when the vocal guard keeps
        # the full song. Its grid must not certify an unmeasured intro/tail.
        actual_grids=[];grid_review=False
        for clip_index,time,field in ((i,clips[i]['end'],'left_window'),
                                      (i+1,clips[i+1]['start'],'right_window')):
            window=windows[clips[clip_index]['track_id']][evidence_item.get(field,0)]
            covered=window['start']<=time<=window['end']
            actual_grids.append(window.get('grid',{}) if covered else {})
            grid_review=grid_review or not covered or window.get('grid_review',True)
        evidence_item['candidate_phase_alignment']=evidence_item.get('phase_alignment',{})
        evidence_item['phase_alignment']=phase_alignment(clips[i]['end'],actual_grids[0],
                                                        clips[i+1]['start'],actual_grids[1])
        evidence_item['grid_review']=bool(grid_review)
        seconds=choose_transition_seconds(
            evidence_item.get('bpm') or clips[i].get('bpm') or clips[i+1].get('bpm'),
            tempo_gap=evidence_item.get('tempo_gap'),
            phase_score=evidence_item.get('phase_alignment', {}).get('score', .35),
            grid_review=bool(evidence_item.get('grid_review', True)), style=style)
        seconds=math.floor(min(seconds,caps[i],caps[i+1])*10000)/10000
        kind=('echo' if style=='echo' else 'smooth') if seconds else 'cut'
        joins.append({'kind':kind,'seconds':seconds})
        explanation=('仅在前段末尾加节奏回声，再交给下一段。' if kind=='echo' else
                     '选段过短，保留中间片段独奏空间并直接切入。' if kind=='cut' else
                     '等功率淡入淡出，减少交接时音量塌陷。')
        reasons.append(f'第{i+1}→{i+2}段：'+explanation+f'交接{seconds:.2f}秒；'+('相对音量逐段提升，曲风能量仍需你试听判断。' if style=='rising' else '保持原速、原调，未按未知调性强行变调。'))
    plan={'kind':'continuous_set','version':1,'title':{'journey':'连贯旅程','rising':'能量递进','echo':'回声转场'}[style],
          'target_bpm':None,'preview_join':0,'clips':clips,'joins':joins}
    return {'project':validate_set(plan,tracks),'audio_processed':False,'explanations':reasons,
            'arrangement':{'order':arranged,'order_score':round(float(order_score),6),
                           'joins':evidence,'track_treatments':treatments,
                           'transition_candidates':[c for item in evidence for c in item.get('transition_candidates',[])],
                           'handoff_policy':'prefer_mid_song_when_locally_supported',
                           'tempo_policy':'original_speed_only',
                           'limitations':['排序分数是局部声学兼容证据，不是好听概率。','中途切入点是结构变化候选，仍需试听确认；未知歌词不会被当成空白。']},
            'limitations':['已先扫描局部速度、调性、能量和结构候选，再决定顺序与中途切点。','交接和音量需试听；未自动对速或移调。']}


def preview_window(layout,index):
    join=layout['joins'][index]
    start=max(0,join['start']-5)
    duration=min(layout['duration']-start,max(25,join['end']-start+5))
    return start,duration


def render_set(plan,tracks,output_dir,preview=False):
    original=validate_set(plan,tracks);directory=Path(output_dir);directory.mkdir(parents=True,exist_ok=True)
    with prepared_warps(original,tracks,directory,'clips') as (prepared,inventory):
        performance=prepared.pop('performance',None)
        if performance and (performance['events'] or performance['drums']):
            with tempfile.TemporaryDirectory(prefix='performance-base-',dir=directory) as base:
                result=_render_set_base(prepared,inventory,base,preview=False)
                start,length=preview_window(set_layout(original),original['preview_join'])
                result=finish_performance(result,performance,tracks,Path(base)/'audio.wav',directory,preview,start,length)
        else:result=_render_set_base(prepared,inventory,directory,preview)
    result['project']=original
    return attach_render_audit(result,original,directory/'audio.wav')


def _render_set_base(plan,tracks,output_dir,preview=False):
    plan=validate_set(plan,tracks);directory=Path(output_dir);directory.mkdir(parents=True,exist_ok=True)
    with prepared_sidechains(plan['clips'],tracks,plan['target_bpm'],directory) as keys:
        return _render_set_keys(plan,tracks,directory,preview,*keys)


def _render_set_keys(plan,tracks,output_dir,preview,key_args,key_chains,key_labels):
    plan=validate_set(plan,tracks);layout=set_layout(plan);directory=Path(output_dir);directory.mkdir(parents=True,exist_ok=True)
    args=[];chains=list(key_chains);names=[]
    for i,c in enumerate(plan['clips']):
        args.extend([*INPUT_OPTIONS,'-i',tracks[c['track_id']]['path']])
        chains.append(_slot_filter(i,c,plan['target_bpm'],{j:v for (k,j),v in key_labels.items() if k==i})+f'[c{i}]');name=f'c{i}';length=layout['clips'][i]['duration']
        if i<len(plan['joins']) and plan['joins'][i]['kind']=='echo':
            window=min(length/2,max(1,plan['joins'][i]['seconds']));at=length-window
            delay=min(750,30000/(plan['target_bpm'] or c['bpm'] or 120))
            chains.extend([f'[{name}]asplit=2[h{i}][t{i}]',f'[h{i}]atrim=end={at:.9f}[head{i}]',
                f'[t{i}]atrim=start={at:.9f},asetpts=PTS-STARTPTS,aecho=1:1:{delay:.4f}:0.22,atrim=duration={window:.9f}[tail{i}]',
                f'[head{i}][tail{i}]concat=n=2:v=0:a=1[e{i}]']);name=f'e{i}'
        filters=[]
        if i>0 and plan['joins'][i-1]['seconds']==0:filters.append('afade=t=in:d=0.005')
        if i<len(plan['joins']) and plan['joins'][i]['seconds']==0:filters.append(f'afade=t=out:st={max(0,length-.005):.9f}:d=0.005')
        if filters:chains.append(f'[{name}]'+','.join(filters)+f'[f{i}]');name=f'f{i}'
        names.append(name)
    current=names[0]
    for i,j in enumerate(plan['joins']):
        operation=f"acrossfade=ns={round(j['seconds']*44100)}:c1=qsin:c2=qsin" if j['seconds'] else 'concat=n=2:v=0:a=1'
        chains.append(f'[{current}][{names[i+1]}]'+operation+f'[join{i}]');current=f'join{i}'
    total=layout['duration'];preview_start,preview_length=preview_window(layout,plan['preview_join'])
    finish=['alimiter=limit=0.84:level=false:latency=true',f'afade=t=out:st={max(0,total-.02):.9f}:d=0.02']
    # concat/acrossfade timestamps can accumulate sub-sample rounding across
    # long clips. Count the same rendered samples as the full WAV instead of
    # using drifted PTS to select the preview (measured up to 488 samples/hour).
    if preview:finish.extend([f'atrim=start_sample={round(preview_start*44100)}:end_sample={round(preview_start*44100)+round(preview_length*44100)}','asetpts=PTS-STARTPTS',
                             'afade=t=in:st=0:d=0.005',f'afade=t=out:st={max(0,preview_length-.02):.9f}:d=0.02'])
    chains.append(f'[{current}]'+','.join(finish)+'[out]')
    output=directory/'audio.wav'
    run_ffmpeg([*args,*key_args,*(['-filter_complex_threads','1'] if key_args else []),'-filter_complex',';'.join(chains),'-map','[out]','-ar','44100','-c:a','pcm_s16le','-y',output],timeout=900)
    run_ffmpeg([*INPUT_OPTIONS,'-i',output,'-c:a','libmp3lame','-b:a','192k','-y',directory/'audio.mp3'],timeout=300)
    with wave.open(str(output)) as f:actual=f.getnframes()/f.getframerate()
    expected=preview_length if preview else total
    if actual<.1 or abs(actual-expected)>.15:raise RuntimeError('串烧成品时长不符合计划')
    quality={'wav':measure(output),'mp3':measure(directory/'audio.mp3')}
    quality['status']='review_required' if any(quality[k]['true_peak_dbtp'] is None or quality[k]['true_peak_dbtp']>-.5 for k in ('wav','mp3')) else 'machine_checks_passed'
    quality['limitations']='机器测量不代表听感通过；交接、回声、调性和响度仍需试听。'
    quality['stereo_compatibility']=audit_stereo(output)
    return {'audio_processed':True,'preview':bool(preview),'duration':round(actual,6),'preview_start_seconds':preview_start if preview else 0,
            'quality':quality,'layout':layout,'files':['audio.wav','audio.mp3'],'project':plan}
