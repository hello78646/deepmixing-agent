"""Optional bounded processors; parameters never certify musical suitability."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import wave

from .analysis import INPUT_OPTIONS
from .runtime import run_ffmpeg

PROCESSORS={'compressor','deesser','sidechain','saturation','stereo','declip'}


def validate_processor(effect,length,finite,points,tracks=None):
    kind=effect['type']
    fields={
        'compressor':{'threshold_db','ratio','attack_ms','release_ms','makeup_db'},
        'sidechain':{'threshold_db','ratio','attack_ms','release_ms','makeup_db',
                     'key_track_id','key_start','key_end','key_time','key_gain_db'},
        'deesser':{'intensity','amount','frequency','monitor'},
        'saturation':{'drive_db','output_db'},
        'stereo':{'width','mid_db'},
        'declip':{'window_ms','threshold'},
    }[kind]
    if set(effect)!={'type','points'}|fields:raise ValueError('动态/声道处理字段无效')
    result={'type':kind,'points':points(effect['points'],length,'wet',0,1,'处理混合量')}
    limits={
        'threshold_db':(-60,0),'ratio':(1,20),'attack_ms':(.01,2000),'release_ms':(1,9000),
        'makeup_db':(0,12),'intensity':(0,1),'amount':(0,1),'frequency':(0,1),
        'drive_db':(0,18),'output_db':(-24,0),'width':(0,2),'mid_db':(-12,6),
        'window_ms':(10,20),'threshold':(1,100),'key_gain_db':(-48,24),
    }
    for name in fields&limits.keys():result[name]=finite(effect[name],name,*limits[name])
    if kind=='deesser':
        if effect['monitor'] not in ('processed','sibilance'):raise ValueError('去齿音监听模式无效')
        result['monitor']=effect['monitor']
    if kind=='sidechain':
        key=effect['key_track_id']
        if not isinstance(tracks,dict) or not isinstance(key,str) or key not in tracks:
            raise ValueError('侧链触发源须引用已导入素材')
        duration=finite(tracks[key].get('duration'),'触发源时长',.01,86400)
        start=finite(effect['key_start'],'触发源开始',0,duration)
        end=finite(effect['key_end'],'触发源结束',0,duration)
        at=finite(effect['key_time'],'触发源局部位置',0,length)
        if end-start<.01 or at+end-start>length+1e-9:
            raise ValueError('侧链触发选段须至少10毫秒且完整位于目标片段内')
        result.update(key_track_id=key,key_start=start,key_end=end,key_time=at)
    return result


def processor_filter(effect):
    kind=effect['type']
    if kind in ('compressor','sidechain'):
        name='acompressor' if kind=='compressor' else 'sidechaincompress'
        return (f'{name}=threshold={10**(effect["threshold_db"]/20):.12f}:ratio={effect["ratio"]:.9f}'
                f':attack={effect["attack_ms"]:.9f}:release={effect["release_ms"]:.9f}'
                f':makeup={10**(effect["makeup_db"]/20):.12f}:knee=1:link=maximum:detection=peak')
    if kind=='deesser':
        monitor='e' if effect['monitor']=='sibilance' else 'o'
        return f'deesser=i={effect["intensity"]:.9f}:m={effect["amount"]:.9f}:f={effect["frequency"]:.9f}:s={monitor}'
    if kind=='saturation':
        return (f'volume={effect["drive_db"]:.9f}dB,asoftclip=type=tanh:oversample=4,'
                f'volume={effect["output_db"]:.9f}dB')
    if kind=='stereo':
        mid=10**(effect['mid_db']/20)/2;side=effect['width']/2
        return (f'pan=stereo|c0={mid+side:.12f}*c0+{mid-side:.12f}*c1'
                f'|c1={mid-side:.12f}*c0+{mid+side:.12f}*c1')
    return f'adeclip=window={effect["window_ms"]:.9f}:threshold={effect["threshold"]:.9f}:arorder=2:overlap=50'


def sidechain_track_ids(project):
    slots=project.get('clips',[]) if project.get('kind')=='continuous_set' else [project.get(n,{}) for n in ('a','b')]
    return list(dict.fromkeys(e['key_track_id'] for s in slots for e in s.get('effects',[]) if e['type']=='sidechain'))


@contextmanager
def prepared_sidechains(slots,tracks,target,directory):
    """Decode complete native-speed keys, never mix the key into the output.

    Each key is explicitly positioned on the processed target clip's local
    clock. It does not inherit a source BPM, warp, gain envelope or effects.
    """
    with tempfile.TemporaryDirectory(prefix='sidechain-keys-',dir=directory) as temporary:
        args=[];chains=[];labels={}
        for i,slot in enumerate(slots):
            speed=target/slot['bpm'] if target else 1
            length=(slot['end']-slot['start'])/speed;samples=round(length*44100)+44100
            for j,e in enumerate(slot.get('effects',[])):
                if e['type']!='sidechain':continue
                track=tracks[e['key_track_id']];file=Path(temporary)/f'{i}-{j}.wav'
                count=round((e['key_end']-e['key_start'])*44100)
                run_ffmpeg([*INPUT_OPTIONS,'-ss',f'{e["key_start"]:.9f}','-i',track['path'],
                    '-t',f'{e["key_end"]-e["key_start"]:.9f}','-ar','44100','-ac','2','-c:a','pcm_s16le','-y',file])
                with wave.open(str(file)) as f:
                    if (f.getframerate(),f.getnchannels(),f.getsampwidth())!=(44100,2,2) or abs(f.getnframes()-count)>2:
                        raise ValueError('侧链源解码不完整，未提交成功音频')
                index=len(slots)+len(labels)
                args.extend(['-i',str(file)])
                label=f'sckey{i}_{j}';labels[i,j]=f'[{label}]'
                # Keep the detector alive beyond the main EOF. Equal-length
                # streams can race at their final frames in sidechaincompress,
                # truncating the main branch nondeterministically on reload.
                chains.append(f'[{index}:a:0]volume={10**(e["key_gain_db"]/20):.12f},'
                    f'adelay={round(e["key_time"]*44100)}S:all=1,apad=whole_len={samples},atrim=end_sample={samples},'
                    f'asetnsamples=n=1024:p=1[{label}]')
        yield args,chains,labels
