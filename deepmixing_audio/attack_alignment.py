"""Review-only attack landmarks and source-aware percussion placement.

An acoustic landmark is neither a word, beat nor downbeat. No audio or project
is modified by this module. Explicit targets are required for placement.
"""
import math
import numpy as np
from .runtime import prepare_dependencies


def _times(values, name):
    result=list(values)
    if len(result)>64 or any(isinstance(x,bool) or not isinstance(x,(int,float,np.floating))
            or not math.isfinite(float(x)) or not 0<=float(x)<=86400 for x in result):
        raise ValueError(f'{name} must contain at most64 finite positive times')
    result=[float(x) for x in result]
    if any(b-a<1e-6 for a,b in zip(result,result[1:])):
        raise ValueError(f'{name} must be strictly increasing')
    return result


def attack_landmarks(samples, sample_rate, *, start=0., band=(150.,3000.),
                     min_spacing=.16, prominence_ratio=.08):
    """Stereo band-power rise peaks; preserves polarity and reports coverage.

    Uses20ms RMS windows,5ms hop and a20ms rise comparison. Edge candidates
    within50ms are excluded. Thresholds label evidence, never safe trim points.
    """
    if isinstance(sample_rate,bool) or not isinstance(sample_rate,int) or not 8000<=sample_rate<=192000:
        raise ValueError('Invalid sample rate')
    x=np.asarray(samples,dtype=float)
    if x.ndim!=2 or x.shape[1]!=2 or len(x)>60*sample_rate or not np.isfinite(x).all():
        raise ValueError('Expected finite stereo samples, at most60 seconds')
    if (not isinstance(start,(int,float)) or isinstance(start,bool) or not math.isfinite(start) or start<0
        or start+len(x)/sample_rate>86400 or len(band)!=2
        or any(isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t) for t in band)
        or not 0<band[0]<band[1]<sample_rate/2
        or not isinstance(min_spacing,(int,float)) or isinstance(min_spacing,bool) or not .05<=min_spacing<=2.
        or not isinstance(prominence_ratio,(int,float)) or isinstance(prominence_ratio,bool) or not .01<=prominence_ratio<=.9):
        raise ValueError('Invalid bounded attack analysis parameters')
    result=dict(status='insufficient_evidence',source_range=[float(start),float(start+len(x)/sample_rate)],
        band_hz=list(band),landmarks=[],review_required=True,confirmed_words=False,confirmed_beats=False,
        method='stereo_band_rms_positive_rise_20ms_hop5ms')
    if len(x)<sample_rate*.25 or np.sqrt(np.mean(x*x))<1e-5:return result
    prepare_dependencies()
    from scipy.signal import butter, sosfiltfilt, find_peaks
    y=sosfiltfilt(butter(3,band,fs=sample_rate,btype='bandpass',output='sos'),x,axis=0)
    hop=max(1,round(sample_rate*.005));frame=max(2,round(sample_rate*.02))
    rms=np.array([np.sqrt(np.mean(y[i:i+frame]**2)) for i in range(0,len(y)-frame+1,hop)])
    if not len(rms) or float(np.max(rms))<1e-5:return result
    smooth=np.convolve(rms,np.ones(3)/3,mode='same')
    lag=max(1,round(.02*sample_rate/hop))
    rise=np.maximum(0,smooth-np.r_[np.repeat(smooth[0],lag),smooth[:-lag]])
    if float(np.max(rise))<1e-5:return result
    peaks,_=find_peaks(rise,distance=max(1,round(min_spacing*sample_rate/hop)),
        prominence=float(np.max(rise))*prominence_ratio)
    times=(peaks*hop+frame/2)/sample_rate
    result['landmarks']=[dict(time=round(float(start+t),7),rise=float(rise[i]),rms=float(smooth[i]))
        for i,t in zip(peaks,times) if .05<=t<=len(x)/sample_rate-.05]
    if result['landmarks']:result['status']='measured_candidates'
    return result


def place_attacks(targets, *, cue_start, cue_end, source_attack, output_end, minimum_gap=.005):
    """Place whole unwarped cues using internal attack offsets, not cue edges.

    Rejects overlap or source truncation instead of silently shortening tails.
    Calls do not authorize use of automatically detected targets as lyric truth.
    """
    targets=_times(targets,'targets')
    values=(cue_start,cue_end,source_attack,output_end,minimum_gap)
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in values):
        raise ValueError('Invalid cue timing')
    if not 0<=cue_start<source_attack<cue_end<=86400 or not 0<output_end<=86400 or not 0<=minimum_gap<=1:
        raise ValueError('Invalid cue bounds')
    duration=cue_end-cue_start;attack_offset=source_attack-cue_start
    starts=[target-attack_offset for target in targets]
    if any(t<0 or t+duration>output_end+1e-9 for t in starts):
        raise ValueError('Whole cue exceeds output range')
    if any(b-a<duration+minimum_gap-1e-9 for a,b in zip(starts,starts[1:])):
        raise ValueError('Percussion tails would overlap')
    return dict(status='review_required',review_required=True,confirmed_word_alignment=False,
        attack_offset_seconds=attack_offset,events=[dict(target_attack=t,time=s,start=cue_start,end=cue_end)
            for t,s in zip(targets,starts)],audio_processed=False)


def match_attacks(targets, observed, tolerance=.025):
    """Ordered one-to-one matches maximize coverage, then minimize error.

    Extra strikes are counted. Independent nearest-neighbour scores can reuse
    one strike or hide unwanted strikes and therefore are not sufficient.
    """
    targets,observed=_times(targets,'targets'),_times(observed,'observed')
    if isinstance(tolerance,bool) or not isinstance(tolerance,(int,float)) or not 0<tolerance<=1:
        raise ValueError('Invalid matching tolerance')
    cache={}
    def solve(i,j):
        if i==len(targets) or j==len(observed):return (0,0.,())
        if (i,j) in cache:return cache[i,j]
        options=[solve(i+1,j),solve(i,j+1)]
        error=observed[j]-targets[i]
        if abs(error)<=tolerance:
            count,cost,pairs=solve(i+1,j+1)
            options.append((count+1,cost+abs(error),((i,j,error),)+pairs))
        best=max(options,key=lambda x:(x[0],-x[1]));cache[i,j]=best;return best
    count,cost,pairs=solve(0,0)
    return dict(matched=count,missed_targets=len(targets)-count,extra_attacks=len(observed)-count,
        mean_absolute_error_seconds=cost/count if count else None,
        max_absolute_error_seconds=max((abs(p[2]) for p in pairs),default=None),
        matches=[dict(target_index=i,observed_index=j,error_seconds=e) for i,j,e in pairs],
        semantic_alignment_confirmed=False)
