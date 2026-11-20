"""Explicit drum-accent targets over a preserved vocal tail.

The caller supplies targets; neither acoustic attacks nor this arrangement
identify words or authorize movement of a selected vocal phrase.
"""
import copy
import math
from pathlib import Path
import tempfile
import wave

from .accompaniment_bridge import plan_tail_preserving_bridge
from .attack_alignment import place_attacks,attack_landmarks,match_attacks
from .automatic_analysis import _decode_stereo,RATE
from .continuous import validate_set
from .drum_coverage import inspect_drum_coverage
from .performance_audio import mix_performance


def plan_targeted_drum_bridge(plan,tracks,join,outgoing_vocal,incoming_vocal,incoming_drums,*,
        targets,cue_start,cue_end,source_attack,preroll_seconds=2.):
    """Replace only the early native drum cue, then restore native source time.

    Whole cue tails may not overlap, reach the native restore, or exceed the
    bridge. Repeated percussion is explicit; all vocal paths remain unchanged.
    """
    if not isinstance(targets,list) or not 1<=len(targets)<=16:
        raise ValueError('鼓重音须为1至16个明确目标的数组')
    result=plan_tail_preserving_bridge(plan,tracks,join,outgoing_vocal,incoming_vocal,incoming_drums,
        preroll_seconds=preroll_seconds)
    p=result['project'];evidence=result['evidence'];boundary=evidence['boundary']
    placement=place_attacks(targets,cue_start=cue_start,cue_end=cue_end,source_attack=source_attack,
        output_end=boundary-.2)
    if not .1<=cue_end-cue_start<=.75:
        raise ValueError('重音桥需要1至16个明确目标及0.1至0.75秒完整鼓取样')
    if cue_end>tracks[incoming_drums]['duration']:
        raise ValueError('鼓取样超出真实素材')
    preparation=evidence['preparation_start']
    if any(e['time']<preparation+.01 or e['target_attack']<preparation+.08 or
           e['target_attack']>boundary-.4 for e in placement['events']):
        raise ValueError('鼓重音须留在尾唱准备区，并为后曲原生鼓恢复留出余量')
    old=copy.deepcopy(p['performance']['events'][1]);lead=preroll_seconds
    native=copy.deepcopy(old)
    native['start']+=lead-.15;native['time']=boundary-.15
    native['end']=old['end'];native['interval']=native['end']-native['start']
    native['gain_envelope']=[dict(time=t,gain=g) for t,g in
        [(0,0),(.03,1),(.155,1),(.355,0),(native['end']-native['start'],0)]]
    length=cue_end-cue_start
    cues=[dict(track_id=incoming_drums,start=e['start'],end=e['end'],time=e['time'],gain_db=old['gain_db'],
        semitones=0,repeats=1,interval=length,
        gain_envelope=[dict(time=t,gain=g) for t,g in [(0,0),(.02,1),(length-.02,1),(length,0)]])
        for e in placement['events']]
    p['performance']['events']=[p['performance']['events'][0],native,p['performance']['events'][2],*cues]
    result.update(project=validate_set(p,tracks),name='保留尾唱 · 明确重音目标 · 原生鼓恢复',
        reasons=['保留两侧全部既有选段和尾唱，只重编准备区域内的鼓。',
            '将后曲一个真实鼓击按明确目标逐次放置，保留已选取样时长；目标过密或越界则拒绝。',
            '仅重编局部鼓型；前后人声、原曲源起止及后曲人声进入时间与保留尾唱版完全相同。',
            '边界前150毫秒恢复后曲同源同时间鼓轨，防止移动后曲起句。取样重复可能改变律动，需对照试听。'])
    evidence.update(method='targeted_percussion_tail_bridge',drum_looped=False,drum_hit_reused=True,
        target_attacks=list(targets),source_attack=source_attack,cue_source_range=[cue_start,cue_end],
        cue_attack_offset_seconds=placement['attack_offset_seconds'],native_restore_start=boundary-.15,
        target_measurement_range=[preparation,boundary-.2],vocal_paths_identical_to_native_bridge=True,
        confirmed_words=False,automatic_target_detection=False)
    return result


def review_targeted_drum_bridge(proposal,tracks,drum_id):
    """Render the actual isolated drum path, reject missing/extra accents.

    Checks source cue and output with the same low-band rise detector. This
    verifies acoustic placement only, never independent semantic accuracy.
    """
    result=dict(status='unknown',eligible=False,drum_path_rendered=False,confirmed_words=False,confirmed_groove=False,
        reason='鼓重音实际渲染证据不足，保留当前工程')
    try:
        p=validate_set(proposal['project'],tracks);e=proposal['evidence']
        start,end=e['preparation_start'],e['boundary']+.215
        cue_start,cue_end=e['cue_source_range']
        track=tracks[drum_id]
        context_start=max(0,cue_start-.1);context_end=min(track['duration'],cue_end+.1)
        raw=_decode_stereo(track['path'],context_start,context_end-context_start)
        if len(raw)<round((context_end-context_start)*RATE)-2:return result
        source=attack_landmarks(raw,RATE,start=context_start,band=(30.,180.),min_spacing=.05,prominence_ratio=.2)
        landmarks=[v for v in source['landmarks'] if cue_start<=v['time']<=cue_end]
        source.update(checked_cue_range=[cue_start,cue_end],cue_landmarks=landmarks)
        if len(landmarks)!=1 or abs(landmarks[0]['time']-e['source_attack'])>.025:
            return dict(result,status='rejected',reason='取样未确认单一低频起音或内部重音位置不符',source_cue=source)
        events=[copy.deepcopy(event) for event in p['performance']['events'] if event['track_id']==drum_id]
        if not events or end-start>6.25:return result
        for event in events:event['time']-=start
        with tempfile.TemporaryDirectory(prefix='drum-accent-review-') as tmp:
            folder=Path(tmp);silence=folder/'silence.wav';output=folder/'drums.wav'
            with wave.open(str(silence),'wb') as audio:
                audio.setparams((2,2,44100,0,'NONE',''));audio.writeframes(bytes(round((end-start)*44100)*4))
            rendered=mix_performance(silence,dict(events=events,drums=[]),tracks,output)
            peak=rendered.get('true_peak_dbtp')
            result.update(drum_path_rendered=True,true_peak_dbtp=peak)
            if peak is None or not math.isfinite(peak) or peak>-.5:
                return dict(result,reason='鼓路径峰值检查未通过，保留当前工程')
            scope_end=e['target_measurement_range'][1]
            raw=_decode_stereo(output,0,scope_end-start)
            observed=attack_landmarks(raw,RATE,start=start,band=(30.,180.),min_spacing=.05,prominence_ratio=.2)
            times=[v['time'] for v in observed['landmarks']]
            match=match_attacks(e['target_attacks'],times,.025)
            # This internal measurement asset represents already gained/rendered
            # output; shared scale is1 so it must not be compensated again.
            coverage=inspect_drum_coverage(dict(path=str(output),duration=end-start,stem_role='drums'),0,end-start,
                maximum_gap_seconds=.75)
            eligible=match['missed_targets']==0 and match['extra_attacks']==0 and coverage['eligible']
            result.update(status='passed' if eligible else 'rejected',eligible=eligible,
                source_cue=source,observed_attacks=times,matching=match,drum_coverage=coverage,
                true_peak_dbtp=rendered.get('true_peak_dbtp'),
                reason='明确重音的实际输出位置及鼓活动保护通过，听感待核' if eligible else
                       '实际鼓输出有漏击、多击或长空档，保留当前工程',
                limitations=['低频声学起音不等于逐字、首拍或独立音乐真值。',
                    '同一检测方法只验证渲染位置，重复取样音色和鼓型相容仍需试听。'])
    except (KeyError,TypeError,ValueError,RuntimeError,OSError,OverflowError):
        pass
    return result
