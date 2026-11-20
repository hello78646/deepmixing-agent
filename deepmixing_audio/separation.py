"""Persistent local source-separation tasks with transactional derived assets."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import threading
import time
import uuid
import wave

import numpy as np

from .analysis import analyze
from .service import save_json,_identifier
from .separation_runtime import MODEL,PROCESSING_REVISION,model_status,run_separation
from .stem_audio import validate_stems

TERMINAL={'succeeded','failed','cancelled'}
PAIR_STEMS=[('vocal','vocals.wav','人声'),('instrumental','instrumental.wav','伴奏')]
EXTRA_STEMS=[('drums','drums.wav','鼓组'),('bass','bass.wav','贝斯'),('other','other.wav','其他乐器')]


def _cleanup_worker_files(directory):
    for _,name,_ in PAIR_STEMS+EXTRA_STEMS:
        (directory/name).unlink(missing_ok=True)


def _validate_extra_stems(directory,quality):
    """Verify every real extra file and its shared-scale backing reconstruction.

    This checks file integrity and arithmetic alignment, never instrument purity
    or neural separation quality. Integer quantisation permits a few PCM steps.
    """
    roles=['instrumental','drums','bass','other'];frames=quality['frames']
    peaks={role:0. for role in roles[1:]};signal=error=0.;count=0;maximum=0.
    try:
        with ExitStack() as stack:
            streams=[stack.enter_context(wave.open(str(directory/(role+'.wav')),'rb')) for role in roles]
            for stream in streams:
                if (stream.getnchannels(),stream.getframerate(),stream.getsampwidth(),stream.getnframes())!=(2,44100,2,frames):
                    raise ValueError('四源分轨的长度、声道或采样格式与原曲不匹配')
            while True:
                chunks=[stream.readframes(65536) for stream in streams]
                if not chunks[0]:break
                if any(len(chunk)!=len(chunks[0]) for chunk in chunks):
                    raise ValueError('四源分轨文件存在截断数据')
                backing,*instruments=[np.frombuffer(chunk,dtype='<i2').astype(np.float64)/32768 for chunk in chunks]
                residual=backing-sum(instruments)
                maximum=max(maximum,float(np.max(np.abs(residual))))
                signal+=float(np.dot(backing,backing));error+=float(np.dot(residual,residual));count+=len(backing)
                for role,audio in zip(roles[1:],instruments):peaks[role]=max(peaks[role],float(np.max(np.abs(audio))))
            if count!=frames*2:raise ValueError('四源分轨文件的实际采样数不完整')
    except (wave.Error,EOFError,OSError) as exc:
        raise ValueError('无法读取完整的drums、bass、other分轨文件') from exc
    if maximum>4/32768:
        raise ValueError('四源分轨与伴奏的共同缩放或重构不一致，未发布素材')
    return {'extra_stems_validated':roles[1:],'instrumental_reconstruction_error_db':round(10*math.log10(max(error,1e-20)/max(signal,1e-20)),3),
            'extra_sample_peaks_dbfs':{role:round(20*math.log10(max(peak,1e-12)),3) for role,peak in peaks.items()}}


class SeparationService:
    def __init__(self,owner):
        self.owner=owner
        self.root=owner.root/'separations';self.root.mkdir(exist_ok=True)
        self.tasks={};self.events={};self.closed=False
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='deepmixing-stems')
        for path in self.root.glob('*/job.json'):
            record=json.loads(path.read_text(encoding='utf-8'))
            if record['status'] not in TERMINAL:
                record.update(status='failed',error='上次分轨已中断，请重新处理',finished_at=time.time())
                save_json(path,record)
            self.tasks[record['id']]=record

    def job(self,identifier):
        _identifier(identifier)
        with self.owner.lock:
            if identifier not in self.tasks:
                raise ValueError('分轨任务不存在')
            return copy.deepcopy(self.tasks[identifier])

    def submit(self,track_id,all_stems=False):
        _identifier(track_id)
        if type(all_stems) is not bool:
            raise ValueError('all_stems须为布尔值')
        with self.owner.lock:
            if self.closed or self.owner.closed:
                raise ValueError('服务已关闭')
            if track_id not in self.owner.tracks:
                raise ValueError('请选择已上传的原曲')
            track=self.owner.tracks[track_id]
            if track.get('origin_kind')=='separated_stem':
                raise ValueError('该素材已经是分轨，请选择原曲')
            for job in self.tasks.values():
                if (job['source_track_id']==track_id and job['model']==MODEL and job.get('processing_revision')==PROCESSING_REVISION
                        and job.get('all_stems',False) is all_stems):
                    roles={item[0] for item in PAIR_STEMS+(EXTRA_STEMS if all_stems else [])}
                    if job['status'] not in TERMINAL or job['status']=='succeeded' and set(job.get('stems',{}))==roles and all(
                        item['id'] in self.owner.tracks and self.owner.tracks[item['id']].get('stem_role')==role
                        and Path(self.owner.tracks[item['id']]['path']).is_file() for role,item in job['stems'].items()):
                        return copy.deepcopy(job)
            state=model_status()
            if not state['ready']:
                raise ValueError(state['message'])
            if any(job['status'] not in TERMINAL for job in self.tasks.values()):
                raise ValueError('已有一个分轨任务，请完成或取消后再提交')
            identifier=uuid.uuid4().hex;directory=self.root/identifier;directory.mkdir()
            task={'id':identifier,'source_track_id':track_id,'source_name':track['name'],'model':MODEL,'processing_revision':PROCESSING_REVISION,
                  'status':'queued','stage':'queued','progress':0,'created_at':time.time(),'audio_processed':False,'all_stems':all_stems}
            save_json(directory/'job.json',task)
            self.tasks[identifier]=task;self.events[identifier]=threading.Event()
            self.executor.submit(self._execute,identifier,copy.deepcopy(track))
            return copy.deepcopy(task)

    def cancel(self,identifier):
        _identifier(identifier)
        with self.owner.lock:
            if identifier not in self.tasks:
                raise ValueError('分轨任务不存在')
            task=self.tasks[identifier]
            if task['status'] in TERMINAL:
                return copy.deepcopy(task)
            self.events[identifier].set();task['stage']='cancelling'
            save_json(self.root/identifier/'job.json',task)
            return copy.deepcopy(task)

    def _execute(self,identifier,source):
        directory=self.root/identifier;event=self.events[identifier];created=[]
        def progress(stage,amount):
            with self.owner.lock:
                if not event.is_set():
                    task['stage']=stage;task['progress']=max(task['progress'],min(.99,max(0,amount)))
        with self.owner.lock:
            task=self.tasks[identifier];task.update(status='running',stage='loading')
            save_json(directory/'job.json',task)
        try:
            if event.is_set():
                raise InterruptedError('已取消分轨')
            if task.get('all_stems',False):
                result=run_separation(Path(source['path']),directory,event,progress,all_stems=True)
            else:
                # Keep the default runner call compatible with existing workers
                # and two-stem contract fixtures that accept four arguments.
                result=run_separation(Path(source['path']),directory,event,progress)
            quality=validate_stems(source['path'],directory/'vocals.wav',directory/'instrumental.wav',result['scale'])
            if task.get('all_stems',False):quality.update(_validate_extra_stems(directory,quality))
            records={}
            for role,filename,label in PAIR_STEMS+(EXTRA_STEMS if task.get('all_stems',False) else []):
                if event.is_set():
                    raise InterruptedError('已取消分轨')
                asset_id=uuid.uuid4().hex;asset_dir=self.owner.root/'tracks'/asset_id
                asset_dir.mkdir();created.append(asset_dir)
                audio=asset_dir/'audio.wav';shutil.copyfile(directory/filename,audio)
                with audio.open('rb') as stream:
                    digest=hashlib.file_digest(stream,'sha256').hexdigest()
                track={'id':asset_id,'name':source['name'][:180]+' · '+label+'.wav',
                       'duration':quality['frames']/44100,'sha256':digest,'path':str(audio),
                       'analysis':analyze(audio),'uploaded_at':time.time(),'origin_kind':'separated_stem',
                       'source_track_id':source['id'],'stem_role':role,'separation_id':identifier,
                       'model':MODEL,'processing_revision':PROCESSING_REVISION,'model_sha256':result.get('model_sha256'),'separation_quality':quality}
                save_json(asset_dir/'track.json',self.owner.public_track(track));records[role]=track
            _cleanup_worker_files(directory)
            with self.owner.lock:
                if event.is_set():
                    raise InterruptedError('已取消分轨')
                task.update(status='succeeded',stage='complete',progress=1,audio_processed=True,
                    quality=quality,model_result=result,stems={k:self.owner.public_track(v) for k,v in records.items()},finished_at=time.time())
                # This single commit record gates every derived asset on restart.
                save_json(directory/'job.json',task)
                self.owner.tracks.update({track['id']:track for track in records.values()})
        except Exception as exc:
            for path in created:
                if path.parent==self.owner.root/'tracks':
                    shutil.rmtree(path,ignore_errors=True)
            _cleanup_worker_files(directory)
            with self.owner.lock:
                task.update(status='cancelled' if isinstance(exc,InterruptedError) else 'failed',
                    stage='cancelled' if isinstance(exc,InterruptedError) else 'failed',
                    error=str(exc),audio_processed=False,finished_at=time.time())
                task.pop('stems',None);save_json(directory/'job.json',task)
        finally:
            # Partial worker files belong only to this freshly allocated task.
            _cleanup_worker_files(directory)

    def close(self):
        with self.owner.lock:
            if self.closed:
                return
            self.closed=True
            for identifier,event in self.events.items():
                if self.tasks[identifier]['status'] not in TERMINAL:
                    event.set()
        self.executor.shutdown(wait=True,cancel_futures=False)
