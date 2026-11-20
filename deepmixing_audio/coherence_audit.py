"""Measure same-source reconstruction before and after identical transforms."""
import copy
import math

import numpy as np

from .analysis import INPUT_OPTIONS
from .engine import _slot_filter
from .runtime import run_ffmpeg

RATE=44100
ROLES=('vocal','drums','bass','other')


def reconstruction_metrics(reference, stems):
    reference=np.asarray(reference,dtype=float)
    parts=[np.asarray(s,dtype=float) for s in stems]
    if (len(parts)!=4 or reference.ndim!=2 or reference.shape[1]!=2 or len(reference)<RATE//4
            or len(reference)>RATE*8 or not np.isfinite(reference).all()
            or any(s.shape!=reference.shape or not np.isfinite(s).all() for s in parts)):
        raise ValueError('相干核对需要四份等长有限立体声及0.25至8秒参考')
    signal=float(np.sum(reference*reference))
    if signal/len(reference)<1e-10:
        return dict(status='insufficient_signal',relative_rms_error=None,residual_db=None)
    reconstructed=sum(parts)
    error=float(np.sum((reference-reconstructed)**2))
    dot=float(np.sum(reference*reconstructed))
    denominator=math.sqrt(signal*float(np.sum(reconstructed*reconstructed)))
    mono=(reference[:,0]+reference[:,1])/2
    mono_sum=(reconstructed[:,0]+reconstructed[:,1])/2
    mono_signal=float(np.sum(mono*mono))
    return dict(status='measured',frames=len(reference),relative_rms_error=math.sqrt(error/signal),
                residual_db=10*math.log10(max(error/signal,1e-15)),
                zero_lag_cosine=dot/denominator if denominator>1e-15 else None,
                mono_relative_rms_error=math.sqrt(float(np.sum((mono-mono_sum)**2))/mono_signal) if mono_signal>1e-10 else None,
                maximum_absolute_error=float(np.max(np.abs(reference-reconstructed))))


def review_stem_coherence(clip, tracks, target_bpm=None, resolve_path=None):
    """Review up to eight source seconds; never retime or change live audio."""
    unknown=dict(status='unknown',review_only=True,audio_processed=False,
                 automatic_path_changed=False,musical_approval=None)
    try:
        source=tracks[clip['track_id']]
        if (source.get('stem_role') or clip.get('effects') or clip.get('warp_map')
                or clip.get('gain_envelope') or clip.get('bass_db')):
            return dict(unknown,reason='需要原曲及可比的恒定速度/音高路径；不以原始分轨代表未建模处理。')
        groups={}
        for track in tracks.values():
            if track.get('source_track_id')==source['id'] and track.get('stem_role') in ROLES:
                groups.setdefault(track.get('separation_id'),[]).append(track)
        complete=[group for key,group in groups.items() if key and len(group)==4 and
                  {t.get('stem_role') for t in group}==set(ROLES)]
        if len(complete)!=1:
            return dict(unknown,reason='需要唯一同次人声/鼓/贝斯/其他四轨；缺失或多版本不代选。')
        parts=sorted(complete[0],key=lambda t:ROLES.index(t['stem_role']))
        scales=[t.get('separation_quality',{}).get('shared_scale') for t in parts]
        if (any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in scales)
                or max(scales)-min(scales)>1e-9
                or any(abs(t['duration']-source['duration'])>.05 for t in parts)):
            return dict(unknown,reason='四轨共同缩放或源时间对应证据缺失。')
        speed=target_bpm/clip['bpm'] if target_bpm else 1.
        slot=copy.deepcopy(clip)
        # Analysis duration is bounded in both source and output clocks.
        slot['end']=min(clip['end'],clip['start']+8*min(speed,1.))
        slot.update(gain_db=0,bass_db=0,preserve_original=False)
        for key in ('effects','gain_envelope','warp_map'):slot.pop(key,None)
        if slot['end']-slot['start']<.25*max(1.,speed):
            return dict(unknown,reason='处理后可比窗口不足0.25秒。')
        def decode(track, output_speed, pitch):
            request={**slot,'semitones':pitch}
            target=None if abs(output_speed-1)<1e-9 else request['bpm']*output_speed
            chain=_slot_filter(0,request,target)+'[out]'
            path=resolve_path(track['id']) if resolve_path else track['path']
            # Actual source coverage is checked before the processing graph.
            raw=run_ffmpeg([*INPUT_OPTIONS,'-ss',f"{slot['start']:.9f}",'-i',path,
                '-t',f"{slot['end']-slot['start']:.9f}",'-ac','2','-ar',str(RATE),'-f','f32le','pipe:1']).stdout
            expected=round((slot['end']-slot['start'])*RATE)
            if len(raw)//8<expected-2:raise ValueError('source_truncated')
            data=run_ffmpeg([*INPUT_OPTIONS,'-i',path,'-filter_complex',chain,'-map','[out]',
                '-ar',str(RATE),'-f','f32le','pipe:1']).stdout
            samples=np.frombuffer(data,dtype='<f4').reshape(-1,2).astype(float)
            if not np.isfinite(samples).all():raise ValueError('nonfinite')
            return samples
        def measure(output_speed,pitch):
            reference=decode(source,output_speed,pitch)
            signals=[decode(t,output_speed,pitch)/scales[0] for t in parts]
            lengths=[len(reference),*[len(s) for s in signals]]
            if max(lengths)-min(lengths)>2:raise ValueError('processing_length_mismatch')
            n=min(lengths)
            return reconstruction_metrics(reference[:n],[s[:n] for s in signals])
        native=measure(1.,0)
        processed=measure(speed,clip['semitones']) if abs(speed-1)>1e-9 or clip['semitones'] else copy.deepcopy(native)
        delta=(processed['residual_db']-native['residual_db'] if
               native['residual_db'] is not None and processed['residual_db'] is not None else None)
        return dict(status='measured' if native['status']==processed['status']=='measured' else 'unknown',
                    review_only=True,audio_processed=False,diagnostic_audio_processed=True,
                    automatic_path_changed=False,musical_approval=None,
                    source_start=slot['start'],source_end=slot['end'],tempo_ratio=speed,semitones=clip['semitones'],
                    separation_id=parts[0]['separation_id'],shared_scale=scales[0],native=native,processed=processed,
                    residual_change_db=delta,
                    limitations=['同源四轨按共同缩放复原，再经过相同恒定变速/移调分别核对；未优化增益、时移或相位来隐藏残差。',
                                 '残差包括分离、PCM量化及处理差异，不是纯净度、音乐适配或统一音质评分。',
                                 '只读前至多8秒源/输出窗口；不代表全曲、独立多轨联合变速或任意效果链通过。'])
    except (ValueError,KeyError,TypeError,RuntimeError,OSError,OverflowError):
        return dict(unknown,reason='真实解码、同次来源或处理长度核对未完成，保持未知。')
