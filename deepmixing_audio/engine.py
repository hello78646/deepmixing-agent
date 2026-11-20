"""Execute validated numeric plans with FFmpeg; report actual encoded results."""
import json
import math
from pathlib import Path
import re
import wave
import copy
from contextlib import contextmanager
import tempfile

from .analysis import INPUT_OPTIONS
from .advanced_audio import build_effect_graph
from .models import validate_project, durations
from .runtime import run_ffmpeg
from .elastic_audio import render_warp
from .performance_audio import mix_performance
from .dynamics_audio import prepared_sidechains
from .stereo_audit import audit_stereo
from .render_audit import attach_render_audit


def _envelope_expression(points):
    """Numeric-only, per-sample linear amplitude, holding endpoint values."""
    expression=f"{points[-1]['gain']:.9f}"
    for left,right in reversed(list(zip(points,points[1:]))):
        at,end=left['time'],right['time'];gain=left['gain'];delta=right['gain']-gain
        ramp=f'({gain:.9f}+({delta:.9f})*(t-{at:.9f})/{end-at:.9f})'
        expression=f'if(lt(t,{end:.9f}),{ramp},{expression})'
    expression=f"if(lt(t,{points[0]['time']:.9f}),{points[0]['gain']:.9f},{expression})"
    return expression.replace(',',r'\,')


def _slot_filter(name, slot, target, sidechain_labels=None):
    speed=target/slot['bpm'] if target else 1
    pitch=2**(slot['semitones']/12)
    filters=[f"atrim=start={slot['start']:.9f}:end={slot['end']:.9f}",'asetpts=PTS-STARTPTS',
             'aresample=44100','aformat=sample_fmts=fltp:channel_layouts=stereo']
    if abs(speed-1)>1e-8 or slot['semitones']:
        filters.append(f'rubberband=tempo={speed:.9f}:pitch={pitch:.9f}')
    filters.append(f"volume={slot['gain_db']}dB")
    if slot['bass_db']:
        filters.append(f"bass=g={slot['bass_db']}:f=110:w=0.5")
    if slot.get('gain_envelope'):
        expression=_envelope_expression(slot['gain_envelope'])
        filters.append(f'aeval=val(0)*({expression})|val(1)*({expression}):c=stereo')
        # aeval can lose its negotiated layout before a zero-overlap concat;
        # keep the same explicit stereo contract as the source preparation.
        filters.append('aformat=sample_fmts=fltp:channel_layouts=stereo')
    # Fade each cropped source independently, after speed conversion. A voice
    # can end while its backing continues, so a master fade cannot protect it.
    length=(slot['end']-slot['start'])/speed
    filters.extend(['afade=t=in:st=0:d=0.005',f'afade=t=out:st={max(0,length-.005):.9f}:d=0.005'])
    effects=slot.get('effects') or []
    if not effects:
        return f'[{name}:a:0]'+','.join(filters)
    source=f'[adv{str(name)}in]'
    base=f'[{name}:a:0]'+','.join(filters)+source
    graph, final_label = build_effect_graph(source, effects, f'adv{str(name)}_',sidechain_labels)
    samples=round(length*44100)
    ending=(f'apad=whole_len={samples},atrim=end_sample={samples},'
            'afade=t=in:st=0:d=0.005,'
            f'afade=t=out:st={max(0,length-.005):.9f}:d=0.005')
    return base+';'+graph+f';{final_label}'+ending


def measure(path):
    result=run_ffmpeg([*INPUT_OPTIONS,'-i',path,'-map','0:a:0','-af','ebur128=peak=true',
                        '-f','null','-'],loglevel='info')
    text=result.stderr.decode(errors='replace').split('Summary:')[-1]
    def value(pattern):
        match=re.search(pattern,text)
        if not match:
            return None
        number=float(match.group(1))
        return number if math.isfinite(number) else None
    return {'integrated_lufs':value(r'I:\s*([-+\d.inf]+)\s*LUFS'),
            'true_peak_dbtp':value(r'Peak:\s*([-+\d.inf]+)\s*dBFS')}


@contextmanager
def prepared_warps(project,tracks,directory,slot_names):
    """Render each local map once, keeping stored source IDs and selections."""
    with tempfile.TemporaryDirectory(prefix='warp-inputs-',dir=directory) as temporary:
        p=copy.deepcopy(project);inventory=copy.deepcopy(tracks)
        # Guides describe original source coordinates, and are metadata only
        # during rendering. Keep them in the returned saved project, not on
        # temporary warped track IDs where they would be invalid evidence.
        p.pop('musical_guides',None)
        slots=p['clips'] if slot_names=='clips' else [p[n] for n in slot_names]
        for i,slot in enumerate(slots):
            points=slot.pop('warp_map',None)
            if not points:continue
            source=inventory[slot['track_id']];length=slot['end']-slot['start']
            output=Path(temporary)/f'warp-{i}.wav'
            render_warp(source['path'],slot['start'],slot['end'],points,output)
            key=f'warped_internal_{i}'
            inventory[key]={**source,'id':key,'path':str(output),'duration':length}
            slot.update(track_id=key,start=0,end=length)
        yield p,inventory


def finish_performance(result,performance,tracks,base,directory,preview,start,length):
    """Apply global events before any preview cut, then remeasure the encoding."""
    output=Path(directory)/'audio.wav'
    info=mix_performance(base,performance,tracks,output)
    if preview:
        cropped=Path(directory)/'preview-crop.wav'
        run_ffmpeg([*INPUT_OPTIONS,'-i',output,'-af',
            f'atrim=start={start:.9f}:duration={length:.9f},asetpts=PTS-STARTPTS,afade=t=in:d=0.005,afade=t=out:st={max(0,length-.02):.9f}:d=0.02',
            '-c:a','pcm_s16le','-y',cropped],timeout=900)
        cropped.replace(output)
    run_ffmpeg([*INPUT_OPTIONS,'-i',output,'-c:a','libmp3lame','-b:a','192k','-y',Path(directory)/'audio.mp3'],timeout=300)
    with wave.open(str(output)) as f:actual=f.getnframes()/f.getframerate()
    expected=length if preview else result['duration']
    if abs(actual-expected)>.15:raise RuntimeError('演奏成品时长不符合计划')
    quality={key:measure(Path(directory)/name) for key,name in [('wav','audio.wav'),('mp3','audio.mp3')]}
    quality['status']='machine_checks_passed' if all(quality[k]['true_peak_dbtp'] is not None and quality[k]['true_peak_dbtp']<=-.5 for k in ('wav','mp3')) else 'review_required'
    quality['limitations']='信号与编码检查不代表主观听感通过。'
    quality['stereo_compatibility']=audit_stereo(output)
    result.update(duration=round(actual,6),preview=bool(preview),preview_start_seconds=start if preview else 0,performance=info,quality=quality)
    return result


def render(project, tracks, output_dir, preview=False):
    original=validate_project(project,tracks);directory=Path(output_dir);directory.mkdir(parents=True,exist_ok=True)
    with prepared_warps(original,tracks,directory,('a','b')) as (prepared,inventory):
        performance=prepared.pop('performance',None)
        if performance and (performance['events'] or performance['drums']):
            with tempfile.TemporaryDirectory(prefix='performance-base-',dir=directory) as base:
                result=_render_base(prepared,inventory,base,preview=False)
                la,_,total=durations(original)
                start=max(0,la-original['transition_seconds']-5) if original['mode']=='transition' else max(0,min(original['b_offset_seconds']-3,total-25))
                window=max(25,original['transition_seconds']+10) if original['mode']=='transition' else 25
                length=min(window,result['duration']-start)
                result=finish_performance(result,performance,tracks,Path(base)/'audio.wav',directory,preview,start,length)
        else:result=_render_base(prepared,inventory,directory,preview)
    result['project']=original
    return attach_render_audit(result,original,directory/'audio.wav')


def _render_base(project, tracks, output_dir, preview=False):
    project=validate_project(project,tracks)
    directory=Path(output_dir);directory.mkdir(parents=True,exist_ok=True)
    with prepared_sidechains([project[n] for n in ('a','b')],tracks,project['target_bpm'],directory) as keys:
        return _render_base_keys(project,tracks,directory,preview,*keys)


def _render_base_keys(project, tracks, output_dir, preview, key_args, key_chains, key_labels):
    project=validate_project(project,tracks)
    directory=Path(output_dir)
    directory.mkdir(parents=True,exist_ok=True)
    la,lb,total=durations(project)
    chains=list(key_chains)+[_slot_filter(i,project[n],project['target_bpm'],
        {j:v for (k,j),v in key_labels.items() if k==i})+f'[{n}]' for i,n in enumerate(('a','b'))]
    if project['mode']=='transition':
        overlap=project['transition_seconds']
        chains.append(f'[a][b]acrossfade=ns={round(overlap*44100)}:c1=qsin:c2=qsin[mix]' if overlap else
                      '[a][b]concat=n=2:v=0:a=1[mix]')
        preview_start=max(0,la-overlap-5)
    else:
        delay=round(project['b_offset_seconds']*44100)
        chains.append(f'[b]adelay={delay}S:all=1[delayed]')
        chains.append('[a][delayed]amix=inputs=2:duration=longest:normalize=0[mix]')
        preview_start=max(0,min(project['b_offset_seconds']-3,total-25))
    finish=[]
    tail=0
    if project['echo']:
        milliseconds=min(1000,30000/(project['target_bpm'] or 120))
        tail=milliseconds/1000
        finish.append(f"aecho=0.8:1:{milliseconds:.4f}:{project['echo']}")
    finish.append('alimiter=limit=0.84:level=false:latency=true')
    duration=total+tail
    # Include a brief terminal fade, preserving echo tails before the final edge.
    finish.append(f'afade=t=out:st={max(0,duration-.02):.6f}:d=0.02')
    if preview:
        window=max(25,project['transition_seconds']+10) if project['mode']=='transition' else 25
        preview_length=min(window,duration-preview_start)
        # Preview cropping creates a new boundary inside otherwise valid audio.
        # Apply ramps after the trim, preserving all samples before these edges.
        finish.extend([f'atrim=start={preview_start:.6f}:duration={preview_length:.6f}','asetpts=PTS-STARTPTS',
                       'afade=t=in:st=0:d=0.005',f'afade=t=out:st={max(0,preview_length-.02):.9f}:d=0.02'])
    chains.append('[mix]'+','.join(finish)+'[out]')
    output=directory/'audio.wav'
    run_ffmpeg([*INPUT_OPTIONS,'-i',tracks[project['a']['track_id']]['path'],
                *INPUT_OPTIONS,'-i',tracks[project['b']['track_id']]['path'],*key_args,
                *(['-filter_complex_threads','1'] if key_args else []),
                '-filter_complex',';'.join(chains),'-map','[out]','-ar','44100',
                '-c:a','pcm_s16le','-y',output],timeout=600)
    run_ffmpeg([*INPUT_OPTIONS,'-i',output,'-c:a','libmp3lame','-b:a','192k',
                '-y',directory/'audio.mp3'],timeout=300)
    with wave.open(str(output)) as audio:
        actual=audio.getnframes()/audio.getframerate()
    if actual<.1 or abs(actual-(preview_length if preview else duration))>.15:
        raise RuntimeError('输出时长不符合计划，未标记为完成')
    quality={'wav':measure(output),'mp3':measure(directory/'audio.mp3')}
    quality['status']='review_required' if any(quality[k]['true_peak_dbtp'] is None or
        quality[k]['true_peak_dbtp']>-.5 for k in ('wav','mp3')) else 'machine_checks_passed'
    quality['limitations']='机器测量不代表听感通过；限幅会影响动态，MP3须按目标用途试听。'
    quality['stereo_compatibility']=audit_stereo(output)
    return {'audio_processed':True,'preview':bool(preview),'duration':round(actual,6),
            'preview_start_seconds':round(preview_start,6) if preview else 0,
            'quality':quality,'files':['audio.wav','audio.mp3'],'project':project}
