"""Bounded musical evidence, without semantic phrase/downbeat claims."""
import math
import numpy as np


def tempo_change_candidates(onset,hop_seconds,start,end):
    """Find distinct stable local pulse rates; never infer a downbeat or edit.

    Six-second flanks need at least six attacks and a consistent linear fit.
    Half/double-rate ambiguity is left unresolved. A candidate's time is an
    uncertain transition location, not a source cut or semantic section label.
    """
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)
           for v in (hop_seconds,start,end)) or hop_seconds<=0 or start<0 or end<=start:
        return []
    try:values=np.asarray(onset,dtype=float)
    except (TypeError,ValueError,OverflowError):return []
    if (values.ndim!=1 or not np.isfinite(values).all() or np.any(values<0) or
            len(values)<3 or end-start<13 or
            abs(len(values)*hop_seconds-(end-start))>2*hop_seconds+1e-6):return []
    threshold=max(.2,float(np.percentile(values,95))*.25)
    ids=np.flatnonzero((values[1:-1]>values[:-2])&(values[1:-1]>=values[2:])&
                      (values[1:-1]>threshold))+1
    peaks=[]
    for index in sorted(ids,key=lambda i:-values[i]):
        if all(abs(index-other)*hop_seconds>=.2 for other in peaks):peaks.append(int(index))
    times=np.sort(peaks)*hop_seconds
    if len(times)<12:return []
    def flank(lo,hi):
        local=times[(times>=lo)&(times<hi)]
        if len(local)<6 or local[-1]-local[0]<(hi-lo)*.65:return None
        fit=fit_grid(local)
        if fit['review_required']:return None
        return dict(bpm=fit['bpm'],attacks=len(local),
                    start=round(float(start+lo),6),end=round(float(start+hi),6),
                    max_residual_seconds=fit['max_residual_seconds'],
                    interval_variation=fit['interval_variation'])
    rows=[]
    for at in np.arange(6,end-start-6+.000001,.25):
        before=flank(at-6,at-.2);after=flank(at+.2,at+6)
        if before is None or after is None:continue
        ratio=after['bpm']/before['bpm'];gap=abs(after['bpm']-before['bpm'])/max(after['bpm'],before['bpm'])
        if gap<.08 or any(abs(ratio/alias-1)<.06 for alias in (.5,2)):continue
        rows.append(dict(at=float(at),before=before,after=after,gap=gap))
    groups=[]
    for row in rows:
        if groups and row['at']-groups[-1][-1]['at']<=1:
            groups[-1].append(row)
        else:groups.append([row])
    result=[]
    for group in groups:
        # More than one scan must support the same rate pair; a single chance
        # fit around a fill cannot authorize a change warning.
        if len(group)<2:continue
        middle=group[len(group)//2];lo,hi=group[0]['at'],group[-1]['at']
        if hi-lo>2 or any(max(r[side]['bpm'] for r in group)/min(r[side]['bpm'] for r in group)-1>.04
                         for side in ('before','after')):continue
        result.append(dict(time=round(start+(lo+hi)/2,3),
            kind='tempo_change_candidate',strength=round(min(1.,middle['gap']/.25),3),
            review_required=True,confirmed_phrase=False,reason='前后稳定局部脉冲速度不同，须分段复核拍网',
            evidence=dict(method='stable_local_onset_rates_v1',before=middle['before'],after=middle['after'],
                          supporting_scans=len(group),
                          candidate_time_range=[round(start+lo,3),round(start+hi,3)],
                          time_uncertainty_seconds=round((hi-lo)/2+.25,3)),
            limitation='局部发声周期候选，不确认节拍层级、主歌/副歌、首拍或歌词；不自动变速或裁剪。'))
    return result[:6]


def beat_drift_candidates(beats, start, end):
    """Return review-only boundaries where a local beat period changes.

    Adjacent overlapping interval windows make the candidate local instead of
    tying it to one third of the selected region.  A transition must have
    stable support on both sides and survive a one-beat neighbourhood check;
    isolated missed/extra beats therefore remain unknown.  This describes grid
    evidence only: it never warps timestamps or promotes a drift boundary to a
    phrase/downbeat.
    """
    try:
        times = np.asarray(beats, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return []
    if (times.ndim != 1 or len(times) < 12 or not np.isfinite(times).all() or
            np.any(np.diff(times) <= 0) or np.any(times < start) or np.any(times > end)):
        return []
    intervals = np.diff(times)
    if len(intervals) < 10 or not np.isfinite(intervals).all():
        return []
    # Four intervals on either side is the minimum local support.  For longer
    # regions use a bounded window so a short ritardando is not averaged away.
    window = max(4, min(8, len(intervals) // 4))
    if len(intervals) < window * 2:
        return []

    def summary(values):
        period = float(np.median(values))
        if period <= 0 or not np.isfinite(period):
            return None
        variation = float(np.median(np.abs(values - period)) / period)
        spread = float(np.std(values) / period)
        return period, variation, spread

    rows = []
    for boundary in range(window, len(intervals) - window + 1):
        left_values = intervals[boundary - window:boundary]
        right_values = intervals[boundary:boundary + window]
        left = summary(left_values)
        right = summary(right_values)
        if left is None or right is None:
            continue
        if left[1] > .10 or right[1] > .10 or left[2] > .14 or right[2] > .14:
            continue
        left_bpm, right_bpm = 60.0 / left[0], 60.0 / right[0]
        relative = abs(right_bpm - left_bpm) / max((right_bpm + left_bpm) / 2.0, 1e-9)
        # Half/double layers are handled by tempo-layer evidence. Do not call
        # them a local drift even when the interval windows are individually
        # clean.
        ratio = right_bpm / max(left_bpm, 1e-9)
        if relative < .06 or abs(ratio * 2.0 - 1.0) < .06 or abs(ratio / 2.0 - 1.0) < .06:
            continue
        rows.append({
            'index': boundary,
            'time': float((times[boundary] + times[boundary - 1]) / 2.0),
            'left_bpm': left_bpm,
            'right_bpm': right_bpm,
            'relative_change': relative,
            'left_variation': left[1],
            'right_variation': right[1],
            'left_spread': left[2],
            'right_spread': right[2],
            'direction': 1 if right_bpm > left_bpm else -1,
            'window_beats': window,
            'time_range': [float(times[boundary - window]), float(times[boundary + window])],
        })
    if not rows:
        return []
    # A real local change persists for at least one adjacent scan. Keep the
    # strongest point in each close cluster and avoid two reports for one turn.
    clusters = []
    cluster_gap = max(2.6, float(np.median(intervals)) * 2.0)
    for row in sorted(rows, key=lambda item: item['time']):
        if clusters and row['time'] - clusters[-1][-1]['time'] <= cluster_gap:
            clusters[-1].append(row)
        else:
            clusters.append([row])
    chosen = []
    for cluster in clusters:
        if len(cluster) < 3 and len(intervals) > window * 3:
            continue
        # A down-then-up excursion is usually tracker jitter or a temporary
        # density/alias change, not a persistent local tempo transition.
        if len({item['direction'] for item in cluster}) > 1:
            continue
        peak = max(item['relative_change'] for item in cluster)
        tied = [item for item in cluster if item['relative_change'] >= peak * .995]
        chosen.append(tied[len(tied) // 2])
        if len(chosen) >= 6:
            break
    result = []
    for row in sorted(chosen, key=lambda item: item['time']):
        result.append({
            'time': round(row['time'], 3),
            'kind': 'beat_drift_candidate',
            'strength': round(min(1.0, row['relative_change'] / .2), 3),
            'review_required': True,
            'confirmed_drift': False,
            'reason': '相邻稳定拍段的局部 BPM 有可见差异',
            'evidence': {
                'method': 'overlapping_local_period_v2',
                'left_bpm': round(row['left_bpm'], 3),
                'right_bpm': round(row['right_bpm'], 3),
                'relative_change': round(row['relative_change'], 4),
                'left_interval_variation': round(row['left_variation'], 4),
                'right_interval_variation': round(row['right_variation'], 4),
                'left_interval_spread': round(row['left_spread'], 4),
                'right_interval_spread': round(row['right_spread'], 4),
                'window_beats': row['window_beats'],
                'time_range': [round(row['time_range'][0], 3), round(row['time_range'][1], 3)],
                'supporting_scans': sum(abs(candidate['time'] - row['time']) <= max(.75, float(np.median(intervals)))
                                        for candidate in rows),
            },
            'limitation': '局部拍间隔漂移候选；不确认变速边界、首拍或音乐乐句，也不自动弹性校正。',
        })
    return result


def structure_changes(chroma,rms,hop_seconds,start,end,timbre=None):
    """Require a feature contrast to survive short, wide and distant contexts.

    These are acoustic change candidates, not semantic section labels. The
    disjoint distant contexts reject fills that immediately return to the same
    material; a wide adjacent window alone could still accept a loud impulse.
    Prefix sums bound the cost of scanning the existing <=60 second region.
    """
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)
           for v in (hop_seconds,start,end)) or hop_seconds<=0 or start<0 or end<=start:
        return []
    try:
        chroma=np.asarray(chroma,dtype=float);rms=np.asarray(rms,dtype=float)
        if (chroma.ndim!=2 or chroma.shape[0]!=12 or rms.ndim!=1 or
                chroma.shape[1]!=len(rms) or len(rms)<8 or
                not np.isfinite(chroma).all() or not np.isfinite(rms).all() or
                np.any(chroma<0) or np.any(rms<0)):
            return []
        if timbre is not None:
            timbre=np.asarray(timbre,dtype=float)
            if (timbre.ndim!=2 or not 2<=timbre.shape[0]<=128 or timbre.shape[1]!=len(rms) or
                    not np.isfinite(timbre).all() or np.any(timbre<0)):
                return []
    except (TypeError,ValueError,OverflowError):return []
    n=len(rms)
    if abs(n*hop_seconds-(end-start))>2*hop_seconds+1e-6 or np.max(rms)<.003:
        return []
    width=max(2,round(1.2/hop_seconds))
    if n<4*width+1:return []
    active=rms>=max(1e-5,float(np.percentile(rms,90))*.02)
    norm=chroma/np.maximum(np.linalg.norm(chroma,axis=0,keepdims=True),1e-8)
    norm[:,~active]=0
    color=None
    if timbre is not None:
        # Normalized mel power, square rooted: overall gain alone cannot create
        # a timbre change. No new FFT or learned model is needed here.
        color=np.sqrt(timbre/np.maximum(timbre.sum(axis=0,keepdims=True),1e-12))
        color[:,~active]=0
    level=np.minimum(rms,max(.003,float(np.percentile(rms,95))))
    def prefix(values):
        return np.concatenate((np.zeros((values.shape[0],1)),np.cumsum(values,axis=1)),axis=1)
    harmonic_sum=prefix(norm);level_sum=prefix(level[None,:])
    color_sum=prefix(color) if color is not None else None
    def mean(sums,a,b):return (sums[:,b]-sums[:,a])/(b-a)
    def contrast(a,b,c,d):
        harmonic=min(1.,float(np.linalg.norm(mean(harmonic_sum,a,b)-mean(harmonic_sum,c,d)))/math.sqrt(2))
        before=float(mean(level_sum,a,b)[0]);after=float(mean(level_sum,c,d)[0])
        energy=abs(after-before)/max(.01,before,after)
        color_change=(min(1.,float(np.linalg.norm(mean(color_sum,a,b)-mean(color_sum,c,d)))/math.sqrt(2))
                      if color_sum is not None else 0.)
        strength=max(harmonic*.85,energy*.7,color_change*.85)+.1*min(harmonic,energy)
        return min(1.,strength),(harmonic,energy,color_change)
    result=[];stride=max(1,round(.1/hop_seconds))
    for i in range(2*width,n-2*width+1,stride):
        contexts=[contrast(i-width,i,i,i+width),
                  contrast(i-2*width,i,i,i+2*width),
                  contrast(i-2*width,i-width,i+width,i+2*width)]
        strength=min(c[0] for c in contexts)
        if strength<.32:continue
        components=np.min([c[1] for c in contexts],axis=0)
        dominant=int(np.argmax(components*np.array([.85,.7,.85])))
        result.append(dict(time=round(float(start+i*hop_seconds),3),strength=round(strength,3),
            kind='structure_change_candidate',confirmed_phrase=False,review_required=True,
            reason=('持续音高分布变化','持续能量变化','持续音色变化')[dominant],
            evidence=dict(method='sustained_multiscale_contrast_v1',
                          context_scores=[round(c[0],4) for c in contexts],
                          context_seconds=[round(width*hop_seconds,4),round(2*width*hop_seconds,4)],
                          components={key:round(float(value),4) for key,value in zip(('harmonic','energy','timbre'),components)},
                          timbre_available=color is not None),
            limitation='持续声学变化候选；不确认主歌、副歌、首拍、完整歌词或音乐乐句。'))
    chosen=[]
    for item in sorted(result,key=lambda r:-r['strength']):
        if all(abs(item['time']-p['time'])>=3 for p in chosen):chosen.append(item)
        if len(chosen)>=6:break
    return sorted(chosen,key=lambda r:r['time'])


def repetition_candidates(chroma,rms,hop_seconds,start,end,timbre=None):
    """Return conservative pairs of repeated acoustic windows for review only."""
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)
           for v in (hop_seconds,start,end)) or hop_seconds<=0 or start<0 or end<=start:
        return []
    try:
        chroma=np.asarray(chroma,dtype=float);rms=np.asarray(rms,dtype=float)
        if (chroma.ndim!=2 or chroma.shape[0]!=12 or chroma.shape[1]!=len(rms) or len(rms)<16
                or not np.isfinite(chroma).all() or not np.isfinite(rms).all()
                or np.any(chroma<0) or np.any(rms<0)):
            return []
        if timbre is not None:
            timbre=np.asarray(timbre,dtype=float)
            if (timbre.ndim!=2 or not 2<=timbre.shape[0]<=128 or timbre.shape[1]!=len(rms)
                    or not np.isfinite(timbre).all() or np.any(timbre<0)):
                return []
    except (TypeError,ValueError,OverflowError):
        return []
    if end-start<16 or abs(len(rms)*hop_seconds-(end-start))>2*hop_seconds+1e-6 or np.max(rms)<.003:
        return []
    if float(np.mean(np.std(chroma,axis=1)))<1e-4 and float(np.std(rms))<1e-4:
        return []
    window=max(32,round(8/hop_seconds)); step=max(1,window//2)
    if len(rms)<2*window+step:
        return []
    active=rms>=max(1e-5,float(np.percentile(rms,90))*.04)
    if float(np.mean(active))<.2:
        return []
    bins=16
    def descriptor(a,b):
        harmonic=np.mean(chroma[:,a:b],axis=1); harmonic/=max(float(np.linalg.norm(harmonic)),1e-12)
        envelope=np.asarray([np.mean(rms[chunk]) for chunk in np.array_split(np.arange(a,b),bins)],dtype=float)
        envelope/=max(float(np.linalg.norm(envelope)),1e-12)
        color=None if timbre is None else np.mean(timbre[:,a:b],axis=1)
        if color is not None: color/=max(float(np.linalg.norm(color)),1e-12)
        return harmonic,envelope,color
    windows=[(i,min(i+window,len(rms))) for i in range(0,len(rms)-window+1,step)]
    rows=[]
    for pos,(a,b) in enumerate(windows):
        for c,d in windows[pos+1:]:
            if c-b<window//2: continue
            left,right=descriptor(a,b),descriptor(c,d)
            harmonic=max(0.,float(np.dot(left[0],right[0])))
            envelope=max(0.,float(np.dot(left[1],right[1])))
            color=None if left[2] is None else max(0.,float(np.dot(left[2],right[2])))
            similarity=(.55*harmonic+.30*envelope+.15*color if color is not None
                        else .65*harmonic+.35*envelope)
            if similarity<.88 or harmonic<.82 or envelope<.72 or (color is not None and color<.78): continue
            rows.append(dict(time=round(start+a*hop_seconds,3),kind='repetition_candidate',
                strength=round(min(1.,similarity),3),review_required=True,confirmed_phrase=False,
                reason='两个非重叠窗口的局部声学材料相似',
                evidence=dict(method='normalized_window_similarity_v1',
                    first_window=[round(start+a*hop_seconds,3),round(start+b*hop_seconds,3)],
                    second_window=[round(start+c*hop_seconds,3),round(start+d*hop_seconds,3)],
                    harmonic_similarity=round(harmonic,4),envelope_similarity=round(envelope,4),
                    timbre_similarity=None if color is None else round(color,4),
                    window_seconds=round((b-a)*hop_seconds,3)),
                limitation='相似声学窗口关系；不确认主歌/副歌、歌词重复、首拍、完整乐句或安全循环接缝。'))
    chosen=[]
    for item in sorted(rows,key=lambda r:-r['strength']):
        first=item['evidence']['first_window'];second=item['evidence']['second_window']
        if all(abs(first[0]-x['evidence']['first_window'][0])>=window*hop_seconds/2 and
               abs(second[0]-x['evidence']['second_window'][0])>=window*hop_seconds/2 for x in chosen):
            chosen.append(item)
        if len(chosen)>=6: break
    return sorted(chosen,key=lambda r:r['time'])


def bar_anchor_candidates(beats,beat_strength,start,end,beats_per_bar=4):
    """Suggest a bar phase from measured beat accents; never confirm a downbeat."""
    try:
        times=np.asarray(beats,dtype=float);strength=np.asarray(beat_strength,dtype=float)
    except (TypeError,ValueError,OverflowError):
        return []
    if (times.ndim!=1 or strength.ndim!=1 or len(times)!=len(strength) or len(times)<12 or
            not np.isfinite(times).all() or not np.isfinite(strength).all() or
            np.any(np.diff(times)<=0) or np.any(times<start) or np.any(times>end) or
            isinstance(beats_per_bar,bool) or not isinstance(beats_per_bar,int) or
            not 2<=beats_per_bar<=8):
        return []
    grid=fit_grid(times)
    if grid['review_required'] or grid['bpm'] is None:
        return []
    strength=np.maximum(strength,0.)
    if float(np.ptp(strength))<.08 or float(np.mean(strength))<1e-6:
        return []
    means=np.asarray([float(np.mean(strength[phase::beats_per_bar])) for phase in range(beats_per_bar)])
    order=np.argsort(-means);best=int(order[0]);second=float(means[order[1]])
    baseline=float(np.mean(means));contrast=(float(means[best])-baseline)/max(baseline,1e-9)
    margin=(float(means[best])-second)/max(baseline,1e-9)
    if contrast<.12 or margin<.04:
        return []
    anchor_index=best
    anchor=float(times[anchor_index])
    return [dict(time=round(anchor,3),kind='bar_anchor_candidate',strength=round(min(1.,contrast),3),
        review_required=True,confirmed_downbeat=False,beats_per_bar=beats_per_bar,
        reason='测得拍点的重音相位出现稳定四拍周期差异',
        evidence=dict(method='beat_phase_accent_v1',phase=best,
            phase_means=[round(float(v),4) for v in means],
            phase_margin=round(float(margin),4),grid=grid),
        limitation='重音相位候选；不确认首拍、节拍层级、完整小节或音乐乐句。')]


def boundary_candidates(chroma,rms,beats,hop_seconds,start,end,timbre=None,changes=None):
    """Only associate sustained changes with nearby, stable measured pulses.

    Reuse already measured candidates in analyze_region. Irregular pulses leave
    the raw feature changes available, without upgrading them to beat evidence.
    """
    try:times=np.asarray(beats,dtype=float)
    except (TypeError,ValueError,OverflowError):return []
    if (times.ndim!=1 or len(times)<12 or not np.isfinite(times).all() or
            np.any(np.diff(times)<=0) or times[0]<start or times[-1]>end or
            fit_grid(times)['review_required']):return []
    changes=structure_changes(chroma,rms,hop_seconds,start,end,timbre) if changes is None else changes
    tolerance=min(.15,float(np.median(np.diff(times)))*.3)
    result=[]
    for change in changes:
        at=float(times[np.argmin(np.abs(times-change['time']))]);distance=abs(at-change['time'])
        if distance>tolerance or not start+2<=at<=end-2:continue
        result.append(dict(change,time=round(at,3),kind='phrase_boundary_candidate',
            feature_time=change['time'],beat_distance_seconds=round(distance,4),
            limitation='持续声学变化与邻近实测脉冲相符；不确认小节首拍、主歌、副歌或完整乐句。'))
    return result


def fit_grid(beats):
    t=np.asarray(beats,dtype=float)
    unknown={'bpm':None,'review_required':True,'max_residual_seconds':None}
    if len(t)<4 or not np.isfinite(t).all() or np.any(np.diff(t)<=0):
        return unknown
    slope,anchor=np.polyfit(np.arange(len(t)),t,1)
    if not .25<=slope<=1.5:
        return unknown
    residual=t-(anchor+np.arange(len(t))*slope)
    error=float(np.max(np.abs(residual)))
    cv=float(np.std(np.diff(t))/np.mean(np.diff(t)))
    return {'bpm':round(float(60/slope),3),'anchor':round(float(anchor),6),
            'max_residual_seconds':round(error,4),'interval_variation':round(cv,4),
            'review_required':error>.06 or cv>.08,
            'message':'脉冲偏离均匀网格，建议分段校正；这不是弹性变速修复。' if error>.06 or cv>.08 else '候选脉冲与均匀网格接近；首拍和半速/倍速仍需人工核实。'}


def local_keys(chroma,harmonic_share,hop_seconds,start,end):
    from .music_guides import estimate_key
    windows=[]
    length=end-start
    if length<4:
        return {'status':'unknown','windows':[]}
    for offset in np.arange(0,max(.001,length-4),4):
        finish=min(length,float(offset)+8)
        left=int(offset/hop_seconds); right=min(chroma.shape[1],int(finish/hop_seconds))
        key=estimate_key(chroma[:,left:right],harmonic_share)
        windows.append({'start':round(start+float(offset),3),'end':round(start+finish,3),'key':key})
    known={(w['key']['tonic'],w['key']['mode']) for w in windows if w['key']['status']=='estimated'}
    status='varying_candidates' if len(known)>1 else 'one_candidate' if known else 'unknown'
    return {'status':status,'windows':windows,
            'limitation':'8秒窗口、4秒步进；候选不一致只提示复核，不等于已证实转调。'}
