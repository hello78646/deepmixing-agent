"""Conservative local-pulse handoffs; evidence scores are not taste scores."""
import copy
import math
import statistics

from .grid_evidence import grid_review_reasons


def _pulse(report):
    if set(report.get('measured_features',[])) & {'swung','rubato','non_four_four'}:return None
    grid=report.get('grid',{})
    if grid.get('status')!='estimated' or grid_review_reasons(report):
        return None
    beats=report.get('beats',[])
    if len(beats)<6 or any(not isinstance(x,(int,float)) or isinstance(x,bool) or not math.isfinite(x) for x in beats):return None
    if beats[0]<report.get('start',math.inf)-1e-5 or beats[-1]>report.get('end',-math.inf)+1e-5:return None
    gaps=[b-a for a,b in zip(beats,beats[1:])]
    if min(gaps)<=0:return None
    period=statistics.median(gaps)
    if not .25<=period<=1.5 or statistics.pstdev(gaps)/statistics.mean(gaps)>.08:return None
    return beats,period


def _vocal_spans(report):
    """Conservative source-time protection, not proof of complete lyrics.

    A beat can lie outside a word and still delete that entire word. Protect
    the removed interval, including the same onset/tail room as the preceding
    boundary-selection stage. Invalid/weak ASR stays unknown, never silence.
    """
    def finite(value):
        return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
    lyrics=report.get('lyrics')
    if not isinstance(lyrics,dict) or lyrics.get('status')!='estimated':return []
    lo,hi=lyrics.get('start'),lyrics.get('end')
    if not finite(lo) or not finite(hi) or not 0<=lo<hi:return []
    spans=[]
    for field,limit in (('words',5000),('segments',1000)):
        rows=lyrics.get(field)
        if not isinstance(rows,list):continue
        for row in rows[:limit]:
            if not isinstance(row,dict):continue
            start,end,confidence=(row.get(k) for k in ('start','end','confidence'))
            if not all(finite(v) for v in (start,end,confidence)):continue
            if not lo<=start<end<=hi or not .8<=confidence<=1:continue
            if not isinstance(row.get('text'),str) or not row['text'].strip():continue
            if field=='segments' and not all(row.get(k) is True for k in ('boundary_start_reliable','boundary_end_reliable')):continue
            spans.append((max(lo,start-.04),min(hi,end+.08)))
    return spans


def _safe_trim_candidates(candidates,current,spans,tail):
    safe=[]
    for at in candidates:
        left,right=(at,current) if tail else (current,at)
        if not any(min(right,end)-max(left,start)>1e-7 for start,end in spans):
            safe.append(at)
    return safe,dict(status='estimated_spans' if spans else 'unknown',
                     protected_spans=len(spans),candidate_count=len(candidates),
                     rejected_candidates=len(candidates)-len(safe),
                     confirmed_phrase=False)


def _guard_trim_candidates(candidates,current,report,clip,track,tail):
    """Shared source-boundary policy for selection and later pulse refinement.

    A role assigned to a full-mix clip cannot establish absence of vocals.
    Unknown lyrics permit only retaining the exact endpoint, unless actual
    non-vocal source metadata supports audio-only trimming. Even stems retain
    protection for any reliable vocal spans (for example, separation bleed).
    """
    spans=_vocal_spans(report)
    safe,guard=_safe_trim_candidates(candidates,current,spans,tail)
    # Filtered words are uncertainty, not evidence of a lyric-free interval.
    # A normalized partial segment may retain a good word far from the trim.
    lyrics=report.get('lyrics');uncertain=[]
    finite=lambda v:isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
    if isinstance(lyrics,dict) and lyrics.get('status') in ('estimated','unknown'):
        lo,hi=lyrics.get('start'),lyrics.get('end');rows=lyrics.get('unresolved_ranges')
        if finite(lo) and finite(hi) and 0<=lo<hi and isinstance(rows,list):
            for row in rows[:1000]:
                if not isinstance(row,dict):continue
                left,right=row.get('start'),row.get('end')
                if finite(left) and finite(right) and lo<=left<right<=hi:
                    uncertain.append((max(lo,left-.04),min(hi,right+.08)))
    before_uncertain=len(safe)
    safe,_=_safe_trim_candidates(safe,current,uncertain,tail)
    guard.update(unresolved_ranges=len(uncertain),rejected_uncertain_candidates=before_uncertain-len(safe))
    nonvocal=(track.get('stem_role') in ('instrumental','drums','bass','other') and
              clip.get('role')!='vocal')
    guard.update(source_nonvocal=nonvocal,rejected_unknown_candidates=0)
    # Finding one reliable word does not cover the rest of the requested
    # source. Later pulse snapping must enforce the same coverage requirement
    # as the initial arrangement boundary selector, including vocal sources.
    lo,hi=(lyrics.get('start'),lyrics.get('end')) if isinstance(lyrics,dict) else (None,None)
    before_coverage=len(safe)
    if spans and not nonvocal:
        safe=[at for at in safe if abs(at-current)<=1e-7 or
              (finite(lo) and finite(hi) and lo<=min(at,current)<max(at,current)<=hi)]
    guard['rejected_uncovered_candidates']=before_coverage-len(safe)
    if not spans and not nonvocal:
        kept=[current for at in safe if abs(at-current)<=1e-7]
        guard['rejected_unknown_candidates']=len(safe)-len(kept)
        safe=kept
        guard['reason']='唱词证据未知且无非主唱源分轨依据，保留当前源选段边界；低活动或拍点不代表歌词结束'
    else:
        guard['reason']=('仅按有效高置信唱词范围排除裁剪，未识别间隙不代表无歌词或完整乐句' if spans else
                         '源素材有非主唱分轨标记，允许按原音频证据细化；不保证没有分离残留')
    if guard['rejected_uncertain_candidates']:
        guard['reason']='部分ASR词被过滤，待裁区间仍有未决唱词；保留边界，不把识别缺口当作无歌词'
    if guard['rejected_uncovered_candidates']:
        guard['reason']='待裁区间没有被唱词报告完整覆盖，保留边界；局部已识别词不能证明未分析区域无唱词'
    guard['rejected_candidates']=len(candidates)-len(safe)
    return safe,guard


def _bass_handoff(a,b,tracks,length,overlap,sa,sb):
    """Plan bounded, sequential shelves only from the actual source overlap."""
    from .automatic_analysis import inspect_low_band
    evidence={}
    result=dict(status='unknown',reduction_db=0.,evidence=evidence,
                reason='低频证据不足，保留原低频；不按整段标签盲削')
    # Source-band measurements do not describe an already pitch-shifted spectrum.
    if a['semitones'] or b['semitones']:
        result['reason']='已有移调，源窗口频谱不能代替处理后频谱，保留原低频'
        return result
    for side,clip,start,end in (('a',a,a['end']-overlap*sa,a['end']),
                                 ('b',b,b['start'],b['start']+overlap*sb)):
        try:
            evidence[side]=inspect_low_band(tracks[clip['track_id']],start,end)
        except (ValueError,TypeError):
            evidence[side]=dict(status='unknown',start=start,end=end)
    if any(r['status'] not in ('measured','silent') for r in evidence.values()):
        return result
    # Both sources must contain appreciable bass. A vocal-only/treble intro,
    # quiet tail or silence does not justify cutting the sole bass-bearing side.
    active=[]
    for side,clip in (('a',a),('b',b)):
        report=evidence[side]
        audible=report['low_frequency_rms']*10**(clip['gain_db']/20)
        active.append(report['status']=='measured' and
                      report['low_frequency_ratio']>=.08 and audible>=.0005)
    if not all(active):
        result.update(status='not_needed',reason='实际重叠区域未发现双方都有明显低频，保持低频自然交接')
        return result
    density=min(r['low_frequency_ratio'] for r in evidence.values())
    depth=-round(3+3*max(0.,min(1.,(density-.08)/.42)),3)
    begin=length-overlap
    # The incoming shelf reaches unity at 40%, before the outgoing shelf
    # starts reducing. Never attenuate both bass paths at the same instant.
    a.setdefault('effects',[]).append(dict(type='eq',band='low',points=[
        dict(time=0,gain_db=0),dict(time=begin,gain_db=0),
        dict(time=begin+.4*overlap,gain_db=0),
        dict(time=begin+.85*overlap,gain_db=depth),dict(time=length,gain_db=depth)]))
    b.setdefault('effects',[]).append(dict(type='eq',band='low',points=[
        dict(time=0,gain_db=depth),dict(time=.4*overlap,gain_db=0),dict(time=overlap,gain_db=0)]))
    result.update(status='applied',reduction_db=depth,
                  reason=f'实际重叠区域双方均有低频：分阶段交棒，最多削减{-depth:g} dB，避免同时抽空两边')
    return result


def _prepare_bass_handoff(a,b,tracks,length,overlap,sa,sb,target_bpm=None,delay_period=None):
    """Apply the existing treatment, then verify the actual rendered low-band sum."""
    from .transition_quality import probe_low_overlap,probe_summary,add_verified_bass_trim
    originals=[copy.deepcopy(c.get('effects')) for c in (a,b)]
    bass=_bass_handoff(a,b,tracks,length,overlap,sa,sb)
    if delay_period is not None:
        a.setdefault('effects',[]).append(dict(type='delay',delay_ms=delay_period*500,feedback=.22,points=[
            dict(time=0,wet=0),dict(time=length-overlap,wet=0),
            dict(time=length-overlap*.5,wet=.09),dict(time=length,wet=.04)]))
    if bass['status']!='applied':
        return overlap,bass,False
    before=probe_low_overlap(a,b,tracks,overlap,target_bpm)
    guard=dict(before=probe_summary(before),action='none')
    bass['phase_guard']=guard
    if before['status']!='measured':
        guard['reason']='相位渲染证据不足，保留基础交棒曲线，不盲目追加补偿'
    elif before['classification']=='coherent_buildup':
        compensation=add_verified_bass_trim(a,b,tracks,length,overlap,target_bpm,before)
        guard.update(action='verified_low_trim' if compensation['applied'] else 'trim_rejected',
                     compensation=compensation,reason=compensation['reason'])
        bass['reason']+='；'+compensation['reason']
    elif before['classification']=='cancellation' and overlap>.12:
        # Never flip a source's polarity or boost into a cancellation null.
        # Rebuild from the original effects so old long-overlap controls do not
        # survive a shorter fallback, and omit the optional outgoing delay.
        for clip,effects in zip((a,b),originals):
            if effects is None:clip.pop('effects',None)
            else:clip['effects']=effects
        overlap=.12
        bass=_bass_handoff(a,b,tracks,length,overlap,sa,sb)
        guard.update(action='short_overlap',fallback_seconds=overlap,
                     after=probe_summary(probe_low_overlap(a,b,tracks,overlap,target_bpm)),
                     review_required=True,
                     reason='实测持续低频抵消，回退为120毫秒短交棒；未翻转极性或放大低频，仍须试听')
        bass['phase_guard']=guard
        bass['reason']+='；'+guard['reason']
        return overlap,bass,True
    else:
        guard['reason']='本次局部渲染未触发持续抵消或明显同相堆积阈值，不追加处理'
    return overlap,bass,False


def refine_set_transitions(plan,tracks,analysis):
    """Refine new unlocked sets only; unknown vocals keep source boundaries.

    Local beats are pulse candidates, never confirmed downbeats or lyric phrases.
    No time stretching, pitch changes, fabricated drums or source substitution.
    """
    p=copy.deepcopy(plan);decisions=[];score=0
    protected=[bool(c.get('preserve_original') or tracks[c['track_id']].get('preserve_original') or
                    c.get('bass_db') or any(c.get(k) for k in ('effects','gain_envelope','warp_map')))
               for c in p['clips']]
    if p.get('performance') is not None:return dict(project=p,decisions=[],score=0,techniques=[])
    for i,report in enumerate(analysis.get('joins',[])):
        if i>=len(p['joins']):break
        decision=dict(join=i,applied=False,confirmed_phrase=False,reason='局部证据不足或已有控制，保留原交接')
        decisions.append(decision)
        if protected[i] or protected[i+1]:continue
        a,b=p['clips'][i:i+2];ar,br=report.get('a',{}),report.get('b',{})
        pa,pb=_pulse(ar),_pulse(br)
        if pa is None or pb is None:continue
        sa=p['target_bpm']/a['bpm'] if p['target_bpm'] else 1
        sb=p['target_bpm']/b['bpm'] if p['target_bpm'] else 1
        period=pa[1]/sa
        if abs((pb[1]/sb)/period-1)>.025:continue
        outs=[v for v in pa[0] if max(a['start']+1,a['end']-1.25)<=v<=a['end']]
        ins=[v for v in pb[0] if b['start']<=v<=min(b['start']+.12,b['end']-1)]
        outs,ag=_guard_trim_candidates(outs,a['end'],ar,a,tracks[a['track_id']],True)
        ins,bg=_guard_trim_candidates(ins,b['start'],br,b,tracks[b['track_id']],False)
        decision['vocal_guard']=dict(a=ag,b=bg)
        if (not outs and ag['rejected_candidates']) or (not ins and bg['rejected_candidates']):
            decision['reason']=('待裁区间超出唱词报告覆盖，保留已有选段与交接；不能把未分析区域当作无歌词'
                if ag['rejected_uncovered_candidates'] or bg['rejected_uncovered_candidates'] else
                '唱词证据未知，候选拍点需要移动源边界，保留已有选段与交接；未确认完整歌词或音乐乐句'
                if ag['rejected_unknown_candidates'] or bg['rejected_unknown_candidates'] else
                '候选拍点会移除高置信唱词或其短前后空间，保留已有选段与交接；未确认完整歌词或音乐乐句')
        if not outs or not ins:continue
        end=max(outs);start=min(ins)
        # Long blends require a real non-vocal source and harmonic agreement.
        harmony=report.get('comparison',{}).get('harmony',{}).get('relation')
        nonvocal=ag['source_nonvocal'] or bg['source_nonvocal']
        beats=4 if nonvocal and harmony in ('same','neighbor','relative') else 1
        overlap=period*beats
        if overlap>(2.2 if beats==4 else .6):continue
        left=end-overlap*sa
        phase=min(abs(v-left)/sa for v in pa[0])
        if phase>.04:continue
        length=(end-a['start'])/sa;blength=(b['end']-start)/sb
        previous=p['joins'][i-1]['seconds'] if i else 0
        following=p['joins'][i+1]['seconds'] if i+1<len(p['joins']) else 0
        if length-previous-overlap<.25 or blength-following-overlap<.25:continue
        # Previously added head treatment must remain in the trimmed slot.
        if any(pt['time']>length for fx in a.get('effects',[]) for pt in fx['points']):continue
        a['end']=end;b['start']=start
        overlap,bass,shortened=_prepare_bass_handoff(a,b,tracks,length,overlap,sa,sb,p['target_bpm'],period if beats==1 else None)
        p['joins'][i]=dict(kind='smooth',seconds=overlap)
        if shortened:phase=min(abs(v-(end-overlap*sa))/sa for v in pa[0])
        score+=1
        decision.update(applied=True,method='low_band_short_fallback' if shortened else 'local_pulse_bass_handoff',
            overlap_beats=round(overlap/period,6),
            seconds=round(overlap,6),outgoing_end=end,incoming_start=start,phase_error_seconds=round(phase,6),
            bass_reduction_db=bass['reduction_db'],bass_handoff=bass,
            lyrics_status=[r['lyrics'].get('status','unknown') if isinstance(r.get('lyrics'),dict) else 'unknown' for r in (ar,br)],
            reason=('局部脉冲附近的保守短交棒；' if shortened else '局部脉冲接近：在实测拍点交棒；')+bass['reason']+'。'+
                   ('' if shortened else '有真实非主唱分轨及调性候选支持四拍融合。' if beats==4 else '完整歌曲或调性未知，限制为一拍重叠，并加入轻量半拍延迟收尾。')+'未确认歌词语义或小节首拍。')
    return dict(project=p,decisions=decisions,score=score,techniques=['T02','T03','T09','T27'] if score else [])


def choose_candidate(results,policy,decision=None):
    # A checked tuning variant substitutes for its own base at the same
    # preference rank; it must not outrank a better arrangement on its own.
    tuned={c['mix_refined_from']:c for c in results if 'mix_refined_from' in c and c['machine_eligible']}
    eligible=[tuned.get(c.get('index'),c) for c in results if 'mix_refined_from' not in c
              and (c['machine_eligible'] or c.get('index') in tuned)]
    if not eligible:raise ValueError('没有通过机器检查的候选')
    if any(c.get('arrangement_priority',0)>0 for c in eligible):
        chosen=max(eligible,key=lambda c:c.get('arrangement_priority',0))
    elif any(c.get('transition_score',0)>0 for c in eligible):
        chosen=max(eligible,key=lambda c:c.get('transition_score',0))
    else:chosen=eligible[-1] if policy=='creative' else eligible[0]
    if decision is not None:
        routes={'drums_under_vocal_teaser':'drum_bridge','accompaniment_solo_rebuild':'vocal_bridge','progressive_solo_rebuild':'vocal_bridge'}
        actual=routes.get(chosen.get('evidence',{}).get('method'),'fallback')
        decision['planned']=decision.get('planned',decision.get('selected','fallback'))
        decision['selected']=actual
        decision['delivered_method']=chosen.get('evidence',{}).get('method','original_candidate')
        decision['machine_rejected']=[dict(name=c['name'],reason='候选未通过交付机器检查，未被选择。') for c in results if not c['machine_eligible']]
        if actual!=decision.get('preferred'):
            decision['reason']=('首选路线未能交付，实际选择允许的清唱桥对照。' if actual=='vocal_bridge' else
                                '首选铺垫路线未能交付，实际保留原曲候选，不能声称已解决接歌听感。')
    return chosen
