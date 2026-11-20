"""Local persistent assets and bounded real rendering jobs."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
import time
import uuid

from .analysis import canonicalize, analyze
from .engine import render
from .models import validate_project
from . import planner
from .imports import MEDIA_SUFFIXES,VIDEO_SUFFIXES,classify_link,download_media

MAX_UPLOAD=80*1024*1024
SUFFIXES=MEDIA_SUFFIXES


def save_json(path,data):
    path=Path(path)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def fingerprint(project):
    return hashlib.sha256(json.dumps(project,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _identifier(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):
        raise ValueError('无效的素材或任务ID')
    return value


class MixingService:
    def __init__(self,root):
        self.root=Path(root).resolve()
        for folder in ('tracks','jobs','incoming'):
            (self.root/folder).mkdir(parents=True,exist_ok=True)
        self.tracks={}
        self.jobs={}
        self.lock=threading.RLock()
        self.ingest_lock=threading.BoundedSemaphore(1)
        self.remote_lock=threading.BoundedSemaphore(1)
        self.cue_lock=threading.BoundedSemaphore(1)
        self.cue_cache={}
        self.music_cache={}
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='deepmixing-render')
        self.closed=False
        for record in (self.root/'tracks').glob('*/track.json'):
            track=json.loads(record.read_text(encoding='utf-8'))
            track['path']=str(record.parent/'audio.wav')
            if track.get('origin_kind')=='separated_stem':
                commit=self.root/'separations'/_identifier(track['separation_id'])/'job.json'
                try:
                    transaction=json.loads(commit.read_text(encoding='utf-8'))
                    if transaction['status']!='succeeded' or not any(item['id']==track['id'] for item in transaction['stems'].values()):
                        continue
                except (OSError,ValueError,KeyError):
                    continue
            if Path(track['path']).is_file():
                self.tracks[track['id']]=track
        for record in (self.root/'jobs').glob('*/job.json'):
            job=json.loads(record.read_text(encoding='utf-8'))
            if job['status'] in ('queued','running'):
                job.update(status='failed',error='上次运行已中断，请重新试听或导出')
                save_json(record,job)
            self.jobs[job['id']]=job
        from .separation import SeparationService
        self.separation=SeparationService(self)
        from .automation import AutomationService
        self.automation=AutomationService(self)
        from .voice import VoiceService
        self.voice=VoiceService(self.root)

    @staticmethod
    def public_track(track):
        return {key:copy.deepcopy(value) for key,value in track.items() if key!='path'}

    def list_tracks(self):
        with self.lock:
            return [self.public_track(track) for track in self.tracks.values()]

    def ingest(self,path,name):
        path=Path(path)
        if not isinstance(name,str) or not name or len(name)>240 or Path(name).suffix.lower() not in SUFFIXES:
            raise ValueError('请选择音频文件，或MP4、MOV、MKV、WebM视频提取音轨')
        if not 0<path.stat().st_size<=MAX_UPLOAD:
            raise ValueError('文件为空或超过80MiB')
        if not self.ingest_lock.acquire(blocking=False):
            raise ValueError('正在分析另一份素材，请稍后再上传')
        identifier=uuid.uuid4().hex
        directory=self.root/'tracks'/identifier
        directory.mkdir()
        try:
            original=directory/('original'+Path(name).suffix.lower())
            shutil.copyfile(path,original)
            with original.open('rb') as uploaded:
                digest=hashlib.file_digest(uploaded,'sha256').hexdigest() if hasattr(hashlib,'file_digest') else hashlib.sha256(uploaded.read()).hexdigest()
            duration=canonicalize(original,directory/'audio.wav')
            analysis=analyze(directory/'audio.wav')
            record={'id':identifier,'name':name,'duration':duration,'sha256':digest,
                    'origin_kind':'video' if Path(name).suffix.lower() in VIDEO_SUFFIXES else 'audio',
                    'analysis':analysis,'uploaded_at':time.time(),'path':str(directory/'audio.wav')}
            save_json(directory/'track.json',self.public_track(record))
            with self.lock:
                self.tracks[identifier]=record
            return self.public_track(record)
        except Exception:
            # Only this newly allocated, verified child directory is removed.
            if directory.parent==self.root/'tracks':
                shutil.rmtree(directory)
            raise
        finally:
            self.ingest_lock.release()

    def search_tracks(self,query):
        if not isinstance(query,str) or len(query)>200:
            raise ValueError('请输入不超过200字的歌名或素材名')
        query=query.strip().casefold()
        return [track for track in self.list_tracks() if query in track['name'].casefold()][:30]

    def import_link(self,text):
        candidate=classify_link(text)
        if candidate['status']!='direct_media_candidate':
            return candidate
        if not self.remote_lock.acquire(blocking=False):
            raise ValueError('正在导入另一个链接，请稍后重试')
        temporary=self.root/'incoming'/uuid.uuid4().hex
        try:
            remote=download_media(candidate['url'],temporary)
            track=self.ingest(temporary,remote['name'])
            with self.lock:
                self.tracks[track['id']].update(origin_kind='remote_media',source_url=remote['source_url'])
                track=self.public_track(self.tracks[track['id']])
                save_json(self.root/'tracks'/track['id']/'track.json',track)
            return {'status':'imported','audio_imported':True,'track':track,
                    'message':'已下载并解码为本地音轨，可用于混音。'}
        finally:
            temporary.unlink(missing_ok=True)
            self.remote_lock.release()

    def track_file(self,identifier):
        _identifier(identifier)
        with self.lock:
            if identifier not in self.tracks:
                raise ValueError('素材不存在')
            return Path(self.tracks[identifier]['path'])

    def inspect_music(self,project):
        from .music_guides import ANALYSIS_VERSION, analyze_region, compare_keys
        from .harmonic_audio import analyze_harmony, compare_harmony
        with self.lock:
            if self.closed:
                raise ValueError('服务已关闭')
            plan=validate_project(project,self.tracks)
            tracks=copy.deepcopy(self.tracks)
        if not self.cue_lock.acquire(blocking=False):
            raise ValueError('正在分析另一组音乐，请稍后再试')
        try:
            result={'audio_processed':False}
            keys=[]
            for slot in ('a','b'):
                part=plan[slot]; track=tracks[part['track_id']]
                start=part['start']; end=min(part['end'],start+60)
                cache_key=(track['id'],start,end,ANALYSIS_VERSION)
                if cache_key not in self.music_cache:
                    report=analyze_region(track['path'],start,end)
                    report.update(track_id=track['id'],sha256=track['sha256'])
                    if len(self.music_cache)>=16:
                        self.music_cache.pop(next(iter(self.music_cache)))
                    self.music_cache[cache_key]=report
                result[slot]=copy.deepcopy(self.music_cache[cache_key])
                keys.append(result[slot]['key'])
            result['harmony']=compare_keys(*keys,plan['a']['semitones'],plan['b']['semitones'])
            local_reports=[]
            for slot in ('a','b'):
                part=plan[slot]; track=tracks[part['track_id']]
                start=part['start']; end=min(part['end'],start+24)
                cache_key=('local_harmony',track['id'],start,end)
                if cache_key not in self.music_cache:
                    report=analyze_harmony(track['path'],start,end)
                    report.update(track_id=track['id'],sha256=track['sha256'])
                    if len(self.music_cache)>=16:
                        self.music_cache.pop(next(iter(self.music_cache)))
                    self.music_cache[cache_key]=report
                local_reports.append(copy.deepcopy(self.music_cache[cache_key]))
            # Harmonic pitch classes repeat every octave.  Normalize the
            # project's signed semitone offsets to the nearest [-6, 5]
            # representative accepted by the conservative local comparator.
            delta=int(round(plan['b']['semitones']-plan['a']['semitones']))
            delta=((delta+6)%12)-6
            result['local_harmony']={'a':local_reports[0],'b':local_reports[1],
                                     'comparison':compare_harmony(local_reports[0],local_reports[1],delta)}
            return result
        finally:
            self.cue_lock.release()

    def recommend(self,a_id,b_id,mode,prompt,a_role='full_mix',b_role='full_mix',arrangement='together',styles=None,features=None):
        with self.lock:
            if self.closed:
                raise ValueError('服务已关闭')
            if a_id not in self.tracks or b_id not in self.tracks:
                raise ValueError('请先选择两份已上传素材')
            tracks=copy.deepcopy(self.tracks)
        if arrangement!='acappella_drop':
            return planner.recommend(tracks,a_id,b_id,mode,prompt,a_role,b_role,arrangement=arrangement,styles=styles,features=features)
        if mode!='mashup' or a_role!='vocal' or b_role!='instrumental':
            raise ValueError('清唱后切入需要选择叠搭、A为独立人声、B为伴奏')
        if not isinstance(prompt,str) or len(prompt)>2000:
            raise ValueError('灵感提示须为不超过2000字的文字')
        if not self.cue_lock.acquire(blocking=False):
            raise ValueError('正在分析另一组切入点，请稍后再试')
        try:
            key=(a_id,b_id)
            cue=self.cue_cache.get(key)
            if cue is None:
                from .cue_analysis import analyze_cue
                a,b=tracks[a_id],tracks[b_id]
                parent=tracks.get(a.get('source_track_id')) if a.get('stem_role')=='vocal' else None
                cue=analyze_cue(a['path'],b['path'],parent['path'] if parent else None)
                cue.update(a_id=a_id,b_id=b_id,a_sha256=a['sha256'],b_sha256=b['sha256'])
                if len(self.cue_cache)>=16:
                    self.cue_cache.pop(next(iter(self.cue_cache)))
                self.cue_cache[key]=cue
            return planner.recommend(tracks,a_id,b_id,mode,prompt,a_role,b_role,
                arrangement=arrangement,cue=cue,styles=styles,features=features)
        finally:
            self.cue_lock.release()

    def submit(self,project,preview=False,approved_preview_id=None,request_id=None):
        return self._submit(project,preview,approved_preview_id,validate_project,request_id)

    def recommend_set(self,track_ids,style='journey'):
        from .continuous import recommend_set
        with self.lock:
            if self.closed:raise ValueError('服务已关闭')
            return recommend_set(track_ids,self.tracks,style)

    def submit_set(self,project,preview=False,approved_preview_id=None,request_id=None):
        from .continuous import validate_set
        return self._submit(project,preview,approved_preview_id,validate_set,request_id)

    def _submit(self,project,preview,approved_preview_id,validator,request_id=None):
        if not isinstance(preview,bool):
            raise ValueError('preview须为布尔值')
        if request_id is not None:
            _identifier(request_id)
        with self.lock:
            if self.closed:
                raise ValueError('服务已关闭')
            canonical=validator(project,self.tracks)
            digest=fingerprint(canonical)
            if not preview:
                previous=self.jobs.get(approved_preview_id) if isinstance(approved_preview_id,str) else None
                if not previous or previous['status']!='succeeded' or not previous['preview'] or previous['plan_hash']!=digest:
                    raise ValueError('请先生成并试听当前参数的预览，再确认导出；参数变化须重新试听')
            if request_id is not None:
                for existing in self.jobs.values():
                    if existing.get('request_id')==request_id:
                        if existing['plan_hash']!=digest or existing['preview']!=preview:
                            raise ValueError('相同请求编号不能用于不同工程或任务类型')
                        return copy.deepcopy(existing)
            if sum(job['status'] in ('queued','running') for job in self.jobs.values())>=2:
                raise ValueError('已有两个音频任务，请完成后再提交')
            identifier=uuid.uuid4().hex
            directory=self.root/'jobs'/identifier
            directory.mkdir()
            job={'id':identifier,'status':'queued','preview':preview,'plan_hash':digest,'created_at':time.time(),
                 'project':canonical,'audio_processed':False}
            if request_id is not None:job['request_id']=request_id
            self.jobs[identifier]=job
            save_json(directory/'job.json',job)
            self.executor.submit(self._execute,identifier,copy.deepcopy(self.tracks))
            return copy.deepcopy(job)

    def _execute(self,identifier,tracks):
        directory=self.root/'jobs'/identifier
        with self.lock:
            job=self.jobs[identifier]
            job['status']='running'
            save_json(directory/'job.json',job)
        try:
            is_set=job['project'].get('kind')=='continuous_set'
            if is_set:
                from .continuous import render_set
                result=render_set(job['project'],tracks,directory,preview=job['preview'])
                ids=list(dict.fromkeys(c['track_id'] for c in job['project']['clips']))
            else:
                result=render(job['project'],tracks,directory,preview=job['preview'])
                ids=[job['project'][name]['track_id'] for name in ('a','b')]
            ids+= [e['track_id'] for e in job['project'].get('performance',{}).get('events',[])]
            from .dynamics_audio import sidechain_track_ids
            ids+=sidechain_track_ids(job['project'])
            assets=[self.public_track(tracks[k]) for k in dict.fromkeys(ids)]
            bundle={'format':'deepmixing-set-v1' if is_set else 'deepmixing-project-v1','project':job['project'],'assets':assets,
                    'render_result':result,'portability':'素材保存在本机；在其他设备使用需要重新提供原素材。'}
            save_json(directory/'project.json',bundle)
            with self.lock:
                job.update(status='succeeded',audio_processed=True,result=result)
        except Exception as exc:
            with self.lock:
                job.update(status='failed',error=str(exc),audio_processed=False)
        finally:
            with self.lock:
                job['finished_at']=time.time()
                save_json(directory/'job.json',job)

    def job(self,identifier):
        _identifier(identifier)
        with self.lock:
            if identifier not in self.jobs:
                raise ValueError('任务不存在')
            return copy.deepcopy(self.jobs[identifier])

    def output_file(self,identifier,name):
        if name not in ('audio.wav','audio.mp3','project.json'):
            raise ValueError('不允许的输出文件')
        if self.job(identifier)['status']!='succeeded':
            raise ValueError('任务未成功完成')
        path=self.root/'jobs'/identifier/name
        if not path.is_file():
            raise ValueError('输出文件不存在')
        return path

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed=True
        self.automation.close()
        self.separation.close()
        self.voice.close()
        self.executor.shutdown(wait=True,cancel_futures=False)
