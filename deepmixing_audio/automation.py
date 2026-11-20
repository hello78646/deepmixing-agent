"""Persistent local automatic mixing, with honest recipe coverage and real audio.

Completion here means the requested job produced checked files. It never means
all library recipes are implemented, or that a human has approved the sound.
"""
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

from deepmixing_knowledge import TechniqueLibrary
from deepmixing_knowledge.styles import StyleTechniqueRouter
from .models import validate_project, durations
from .continuous import validate_set, recommend_set, render_set
from .engine import render, measure
from .runtime import run_ffmpeg
from .service import save_json, fingerprint, _identifier
from .style_execution import apply_style


TERMINAL={'succeeded','failed','cancelled'}
OUTPUTS={'audio.wav','audio.mp3','project.json','report.json'}


def _requested(prompt,*terms):
    """Bounded explicit local commands; a nearby negation never enables them."""
    for term in terms:
        for hit in re.finditer(re.escape(term),prompt.casefold()):
            if not re.search(r'不要|不需要|别|不想|禁止|不做',prompt[max(0,hit.start()-10):hit.start()]):return True
    return False


def recipe_coverage(plan,applied_ids=()):
    """All recipes get a decision; missing primitives are never papered over."""
    library=TechniqueLibrary();applied=set(applied_ids)
    routed={c['id'] for c in plan['candidates']}
    routed.update(i for phase in plan['workflow'].values() for i in phase)
    excluded={c['id'] for c in plan['excluded']}
    rows=[];missing=set()
    for row in library.catalog():
        absent=row['execution']['missing'];missing.update(absent)
        state=('excluded' if row['id'] in excluded else
               'bounded_controls_executed' if row['id'] in applied else
               'missing_capability' if absent else
               'conditional' if row['id'] in routed else 'not_selected')
        rows.append(dict(id=row['id'],title=row['title'],state=state,missing=absent,
            sources=row['source_ids'],full_recipe_validated=False))
    return dict(state='incomplete' if missing else 'requires_recipe_validation',recipes=rows,
        missing_capabilities=sorted(missing),library_hash=library.library_hash,
        explanation='全库逐条评估不等于全部配方已实现；本任务只执行列出的控制。')


class AutomationService:
    def __init__(self,owner):
        self.owner=owner;self.root=owner.root/'automation';self.root.mkdir(exist_ok=True)
        self.lock=threading.RLock();self.tasks={};self.events={};self.closed=False
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='deepmixing-auto')
        for file in self.root.glob('*/job.json'):
            record=json.loads(file.read_text('utf-8'))
            if record['status'] not in TERMINAL:
                record.update(status='failed',stage='interrupted',error='上次自动任务中断；原素材保留，可重新发起。')
                save_json(file,record)
            self.tasks[record['id']]=record

    def _canonical(self,body):
        fields={'request_id','kind','project','track_ids','goal','styles','features','policy','prompt','export'}
        if not isinstance(body,dict) or set(body)-fields:raise ValueError('自动混音请求包含不支持的字段')
        request=copy.deepcopy(body)
        request.setdefault('request_id',uuid.uuid4().hex);_identifier(request['request_id'])
        request.setdefault('kind','pair');request.setdefault('goal','transition')
        request.setdefault('policy','creative');request.setdefault('styles',['open_format'])
        request.setdefault('features',[]);request.setdefault('prompt','');request.setdefault('export',True)
        request.setdefault('project',None);request.setdefault('track_ids',[])
        if request['kind'] not in ('pair','set') or request['goal'] not in ('transition','mashup','continuous'):
            raise ValueError('请选择双轨或连续串烧目标')
        if (request['kind']=='set')!=(request['goal']=='continuous'):raise ValueError('作品类型与混音目标不一致')
        if request['policy'] not in ('original','vocal_safe','creative'):raise ValueError('未知自动改造策略')
        if type(request['export']) is not bool:raise ValueError('完整输出选项须为布尔值')
        if not isinstance(request['prompt'],str) or len(request['prompt'])>2000:raise ValueError('灵感描述最多2000字')
        with self.owner.lock:tracks=copy.deepcopy(self.owner.tracks)
        if request['project'] is not None:
            p=(validate_set if request['kind']=='set' else validate_project)(request['project'],tracks)
            if request['kind']=='pair' and p['mode']!=request['goal']:raise ValueError('当前工程与目标不同，请明确更换作品类型')
            project_ids=([c['track_id'] for c in p['clips']] if request['kind']=='set' else [p['a']['track_id'],p['b']['track_id']])
            if request['track_ids'] and request['track_ids']!=project_ids:
                raise ValueError('所选音乐与当前工程不同，请明确新建作品；已有原貌锁仍须保留')
            request['project']=p
            request['track_ids']=project_ids
        ids=request['track_ids']
        if not isinstance(ids,list) or not 2<=len(ids)<=8 or any(not isinstance(i,str) or i not in tracks for i in ids):
            raise ValueError('请先选择2–8份已导入的本机音乐')
        if request['kind']=='pair' and len(ids)!=2:raise ValueError('双轨混音请选择两份素材，更多歌曲请选择自动串烧')
        if request['project'] is not None and request['goal']=='mashup':
            for name,role in [('a','vocal'),('b','instrumental')]:
                actual=tracks[request['project'][name]['track_id']].get('stem_role')
                if actual not in (('vocal',) if role=='vocal' else ('instrumental','other','bass')):
                    raise ValueError('自动叠搭须使用已验证的独立人声/伴奏，请以原曲新建自动叠搭以准备分轨')
        # Validate style/feature enums before creating files or starting work.
        StyleTechniqueRouter(TechniqueLibrary()).plan(request['styles'],features=request['features'],goal=request['goal'])
        return request

    def submit(self,body):
        request=self._canonical(body);digest=fingerprint(request)
        with self.lock:
            if self.closed or self.owner.closed:raise ValueError('服务已关闭')
            for t in self.tasks.values():
                if t['request_id']==request['request_id']:
                    if t['request_hash']!=digest:raise ValueError('同一请求编号不能用于不同自动任务')
                    return copy.deepcopy(t)
            if any(t['status'] not in TERMINAL for t in self.tasks.values()):raise ValueError('已有全自动任务，请等待或取消后继续')
            key=uuid.uuid4().hex;directory=self.root/key;directory.mkdir()
            task=dict(id=key,request_id=request['request_id'],request_hash=digest,status='queued',stage='queued',progress=0,
                kind=request['kind'],created_at=time.time(),human_listening_status='pending',audio_processed=False)
            self.tasks[key]=task;self.events[key]=threading.Event();self._save(task)
            save_json(directory/'request.json',request)
            self.executor.submit(self._execute,key,request)
            return copy.deepcopy(task)

    def _save(self,task):save_json(self.root/task['id']/'job.json',task)

    def job(self,key):
        _identifier(key)
        with self.lock:
            if key not in self.tasks:raise ValueError('自动任务不存在')
            return copy.deepcopy(self.tasks[key])

    def cancel(self,key):
        _identifier(key)
        with self.lock:
            if key not in self.tasks:raise ValueError('自动任务不存在')
            if self.tasks[key]['status'] not in TERMINAL:
                self.events[key].set();self.tasks[key]['stage']='cancelling';self._save(self.tasks[key])
            return copy.deepcopy(self.tasks[key])

    def _check(self,key):
        if self.events[key].is_set():raise InterruptedError('自动任务已取消')

    def _stage(self,key,stage,progress):
        self._check(key)
        with self.lock:
            t=self.tasks[key];t.update(status='running',stage=stage,progress=progress);self._save(t)

    def _inventory(self):
        with self.owner.lock:return copy.deepcopy(self.owner.tracks)

    def _separate(self,key,source,role):
        tracks=self._inventory()
        if tracks[source].get('stem_role')==role:return source
        existing=next((t['id'] for t in tracks.values() if t.get('source_track_id')==source and t.get('stem_role')==role),None)
        if existing:return existing
        if tracks[source].get('stem_role'):
            parent=tracks[source].get('source_track_id')
            if parent in tracks and parent!=source and not tracks[parent].get('stem_role'):
                return self._separate(key,parent,role)
            raise ValueError('需要完整原曲才能准备所需分轨，不能把另一种分轨冒充目标角色')
        service=getattr(self.owner,'separation',None)
        if service is None:raise ValueError('本地分轨服务未就绪，原曲不能冒充独立人声或伴奏')
        return self._wait_stems(key,service,source,all_stems=role in ('drums','bass','other'))[role]['id']

    def _wait_stems(self,key,service,source,all_stems=False):
        with self.owner.lock:
            known=set(service.tasks)
            task=service.submit(source,all_stems=all_stems)
        owned=task['id'] not in known;deadline=time.monotonic()+3600
        try:
            while time.monotonic()<deadline:
                self._check(key);task=service.job(task['id'])
                if task['status']=='succeeded':return task['stems']
                if task['status'] in TERMINAL:raise ValueError('实际分轨未完成，请查看素材分离状态')
                self.events[key].wait(.4)
            raise ValueError('分轨等待超时；已完成素材仍保留')
        except (InterruptedError,ValueError):
            if owned and task['status'] not in TERMINAL:service.cancel(task['id'])
            raise

    def _stem_group(self,key,source,all_stems=False):
        track=self._inventory()[source]
        parent=track.get('source_track_id',source)
        service=getattr(self.owner,'separation',None)
        if service is None:raise ValueError('所需本地分轨服务未就绪')
        return self._wait_stems(key,service,parent,all_stems)

    def _initial(self,key,request):
        if request['project'] is not None:return copy.deepcopy(request['project']),False
        ids=list(request['track_ids'])
        prompt=request['prompt'].casefold()
        if request['policy']=='creative' and '保持原貌' not in prompt and _requested(prompt,'换鼓','鼓组换装','redrum'):
            if request['kind']!='pair':raise ValueError('鼓组换装请选择两首：原旋律歌曲在前，供体鼓组歌曲在后')
            self._stage(key,'separating',.04)
            source=self._stem_group(key,ids[0],True);donor=self._stem_group(key,ids[1],True)
            from .automatic_stems import plan_redrum
            from .planner import recommend
            tracks=self._inventory()
            base=recommend(tracks,*ids,'transition',request['prompt'])['candidates'][0]['base_project']
            proposal=plan_redrum(base,tracks,source,donor)
            self._special_evidence=proposal
            return proposal['project'],False
        if request['policy']=='creative' and '保持原貌' not in prompt and request['kind']=='set' and _requested(prompt,'分轨接力','人声接力','混搭接力'):
            self._stage(key,'separating',.04)
            groups=[self._stem_group(key,i) for i in ids]
            from .automatic_stems import plan_stem_relay
            proposal=plan_stem_relay(groups,self._inventory())
            self._special_evidence=proposal
            return proposal['project'],False
        if request['goal']=='mashup':
            self._stage(key,'separating',.05)
            ids=[self._separate(key,ids[0],'vocal'),self._separate(key,ids[1],'instrumental')]
        tracks=self._inventory()
        if request['kind']=='set':return recommend_set(ids,tracks)['project'],True
        from .planner import recommend
        roles=('vocal','instrumental') if request['goal']=='mashup' else ('full_mix','full_mix')
        self._entry_evidence=None
        proposal=None
        explicit_drop=_requested(prompt,'先清唱','清唱后','人声先','人声起头')
        allow_drop=(request['goal']=='mashup' and request['policy']!='original' and '保持原貌' not in prompt
                    and not _requested(prompt,'一起进','同时进','一开始就叠')
                    and not re.search(r'(不要|别|不需要).{0,5}清唱',prompt))
        if allow_drop and (explicit_drop or request['policy']=='creative'):
            try:
                drop=self.owner.recommend(*ids,request['goal'],request['prompt'],*roles,
                    arrangement='acappella_drop',styles=request['styles'],features=request['features'])
                cue=drop.get('cue_analysis') or {}
                if explicit_drop or cue.get('status')=='measured_rise':
                    proposal=drop;self._entry_evidence=copy.deepcopy(cue)
            except (RuntimeError,ValueError,AttributeError):
                pass
        if proposal is None:
            proposal=recommend(tracks,*ids,request['goal'],request['prompt'],*roles,styles=request['styles'],features=request['features'])
        p=copy.deepcopy(proposal['candidates'][0]['base_project'])
        if allow_drop and explicit_drop and self._entry_evidence is None and p['a']['end']-p['a']['start']>=.5:
            # Explicit request only. No inferred cue: leave a short adjustable lead.
            p['b_offset_seconds']=min(2.,(p['a']['end']-p['a']['start'])*.15)
        return p,True

    def _analyze(self,key,p,tracks,kind):
        from .automatic_analysis import inspect_track, compare_regions
        from .harmonic_audio import analyze_harmony,compare_harmony
        from .semantic_audio import analyze_lyrics,find_wordplay
        def inspect(c,tail=False):
            start=max(c['start'],c['end']-24) if tail else c['start']
            end=min(c['end'],start+24);track=tracks[c['track_id']]
            report=inspect_track(track,start,end)
            self._check(key)
            report['lyrics']=analyze_lyrics(track['path'],start,end)
            return report
        def compare(a,b,slots,seconds=None):
            result=compare_regions(a,b)
            local=[]
            for report,slot,tail in zip((a,b),slots,(seconds is not None,False)):
                speed=p['target_bpm']/slot['bpm'] if p['target_bpm'] else 1
                end=slot['end'];start=slot['start']
                if seconds is not None:
                    if tail:start=max(start,end-seconds*speed)
                    else:end=min(end,start+seconds*speed)
                # Only actual simultaneous source windows are comparable. A
                # 24s tail versus a 24s intro must not stand in for a 120ms join.
                # Original source coordinates do not describe simultaneity
                # after a non-linear warp. Do not suggest a pitch change from
                # unrendered, incorrectly paired windows.
                h=analyze_harmony(tracks[slot['track_id']]['path'],start,end) if end-start>=.25 and not slot.get('warp_map') else {'status':'unknown','start':start,'end':end,'windows':[], 'reason':'窗口过短或存在未分析的局部变速'}
                h['start']/=speed;h['end']/=speed
                for window in h['windows']:window['start']/=speed;window['end']/=speed
                local.append(h)
            relative_shift=slots[1]['semitones']-slots[0]['semitones']
            relative_shift=int((relative_shift+6)%12-6) if relative_shift==int(relative_shift) else None
            result['local_harmony']=compare_harmony(*local,b_shift=relative_shift) if relative_shift is not None else {'status':'unknown','proposed_b_shift':None,'reason':'非整数移调未作半音谐波比较'}
            result['wordplay_candidates']=find_wordplay(a.get('lyrics',{}),b.get('lyrics',{}))
            return result
        if kind=='set':
            joins=[]
            for i in range(len(p['joins'])):
                self._stage(key,'analyzing',.12+.2*i/max(1,len(p['joins'])))
                a=inspect(p['clips'][i],True);b=inspect(p['clips'][i+1])
                joins.append(dict(a=a,b=b,comparison=compare(a,b,p['clips'][i:i+2],p['joins'][i]['seconds'])))
            return dict(joins=joins)
        if p['mode']=='mashup':
            la,lb,_=durations(p);overlap=min(la-p['b_offset_seconds'],lb)
            if overlap<.25:return dict(a={},b={},comparison={},no_overlap=True)
            a=copy.deepcopy(p['a']);b=copy.deepcopy(p['b'])
            sa=p['target_bpm']/a['bpm'] if p['target_bpm'] else 1
            sb=p['target_bpm']/b['bpm'] if p['target_bpm'] else 1
            a['start']+=p['b_offset_seconds']*sa
            seconds=min(24/sa,24/sb,overlap)
            a['end']=min(a['end'],a['start']+seconds*sa);b['end']=min(b['end'],b['start']+seconds*sb)
            ar=inspect(a);br=inspect(b)
        else:ar=inspect(p['a'],True);br=inspect(p['b'])
        return dict(a=ar,b=br,comparison=compare(ar,br,(a,b) if p['mode']=='mashup' else (p['a'],p['b']),None if p['mode']=='mashup' else p['transition_seconds']))

    @staticmethod
    def _risky(report,p):
        # Unknown remains unknown. Fixed-grid estimates are never treated as
        # proof of a true downbeat, lyric boundary or whole-song alignment.
        if p['a']['role']=='full_mix' and p['b']['role']=='full_mix':return True
        for side in ('a','b'):
            grid=report.get(side,{}).get('grid',{})
            if grid.get('status')!='estimated' or grid.get('diagnostics',{}).get('review_required'):return True
        relation=report.get('comparison',{}).get('harmony',{}).get('relation','unknown')
        if relation not in ('same','neighbor','relative'):return True
        return False

    def _plans(self,request,p,fresh,tracks,analysis):
        measured=[]
        reports=([r[s] for r in analysis.get('joins',[]) for s in ('a','b')] if request['kind']=='set' else [analysis.get(s,{}) for s in ('a','b')])
        for r in reports:measured+=r.get('measured_features',[])
        features=list(dict.fromkeys(request['features']+measured))
        route=StyleTechniqueRouter(TechniqueLibrary()).plan(request['styles'],features=features,goal=request['goal'])
        variants=[]
        if request['kind']=='set':
            balanced=copy.deepcopy(p)
            boundary_decisions=[]
            if fresh and request['policy']!='original' and not p.get('performance'):
                from .automatic_timing import locate_boundary
                from .transition_refinement import _guard_trim_candidates
                for i,report in enumerate(analysis['joins']):
                    for index,side,tail in ((i,'a',True),(i+1,'b',False)):
                        c=balanced['clips'][index]
                        if c['preserve_original'] or tracks[c['track_id']].get('preserve_original'):continue
                        lo=max(c['start']+.25,c['end']-3) if tail else c['start']
                        hi=c['end'] if tail else min(c['end']-.25,c['start']+2)
                        choice=locate_boundary(report[side],lo,hi,tail)
                        decision=dict(clip=index,side='end' if tail else 'start',**choice,applied=False)
                        boundary_decisions.append(decision)
                        if choice['available'] and choice['kind']!='feature_change_candidate':
                            # Retain a little consonant/reverb breathing room;
                            # a text timestamp alone isn't a zero-crossing cut.
                            at=choice['time']+(.08 if tail else -.04)
                            field='end' if tail else 'start';current=c[field]
                            at=min(current,max(c['start']+.25,at)) if tail else max(current,min(c['end']-.25,at))
                            safe,guard=_guard_trim_candidates([at],current,report[side],c,tracks[c['track_id']],tail)
                            decision['vocal_guard']=guard
                            if safe:
                                c[field]=safe[0];decision['applied']=abs(c[field]-current)>1e-7
                            else:decision['retained_reason']=guard['reason']
            analysis['boundary_decisions']=boundary_decisions
            for i,report in enumerate(analysis['joins']):
                a,b=balanced['clips'][i:i+2]
                pair=dict(a=a,b=b)
                current=balanced['joins'][i]['seconds']
                if fresh:balanced['joins'][i]=dict(kind='smooth',seconds=min(current,.12 if self._risky(report,pair) else 1.2))
            reasons=['逐处检查交接两侧；未知拍网或主唱重叠时缩短同响。']
            if any(d['applied'] for d in boundary_decisions):reasons.append('在原选段尾部3秒/开头2秒范围内，依据高置信文本端点或有非主唱源分轨依据的实测低活动区细化边界，并保护已识别唱词及短前后空间；不是已确认音乐乐句。')
            if any(d.get('vocal_guard',{}).get('rejected_unknown_candidates') for d in boundary_decisions):reasons.append('唱词证据未知，低活动候选仅供复核，保留现有选段边界。')
            variants=[dict(name='逐处检查的连贯版',project=validate_set(balanced,tracks),techniques=['T27','T35'],reasons=reasons)]
            # A brief join is a fallback, not evidence of creative quality.
            variants[0]['transition_score']=.01
            if fresh and request['policy']!='original' and not p.get('performance'):
                from .transition_refinement import refine_set_transitions
                refined=refine_set_transitions(balanced,tracks,analysis)
                analysis['transition_decisions']=refined['decisions']
                if refined['score']:
                    variants.insert(0,dict(name='拍点与低频交棒版',project=validate_set(refined['project'],tracks),
                        transition_score=refined['score'],techniques=['T35']+refined['techniques'],
                        reasons=[d['reason'] for d in refined['decisions'] if d['applied']]+[
                            '排序依据是有证据支持的交接数量，不是音乐审美评分；仍需听感确认。']))
                    return variants,route
            # Alternative transition duration is explicit, no pretend loop/drum
            # or arbitrary reassignment of actual stem roles.
            alternate=copy.deepcopy(balanced)
            for j in alternate['joins']:j['seconds']=min(j['seconds'],.035)
            if fresh and not p.get('performance') and fingerprint(alternate)!=fingerprint(balanced):variants.append(dict(name='短交棒对照',project=validate_set(alternate,tracks),techniques=['T27','T35','T04'],reasons=reasons+['以更短的交棒比较连续听感；不是长融合或语义乐句匹配。']))
            return variants,route
        balanced=copy.deepcopy(p);reasons=[];ids=['T35']
        if fresh and balanced['mode']=='transition' and _requested(request['prompt'],'歌词接龙','wordplay','接成一句'):
            matches=analysis.get('comparison',{}).get('wordplay_candidates',[])
            accepted=False
            if matches and not any(balanced[s]['preserve_original'] for s in ('a','b')):
                match=matches[0]
                if match['a_end']-balanced['a']['start']>=.25 and balanced['b']['end']-match['b_end']>=.25:
                    balanced['a']['end']=match['a_end'];balanced['b']['start']=match['b_end']
                    balanced['transition_seconds']=.03;accepted=True;ids.append('T45')
                    reasons.append('ASR在两侧边界找到了同一个高置信词：保留A的词尾，B从对应词后接续，避免重复念词；仍需核对识别与句意。')
            analysis['wordplay_decision']=dict(available=accepted,reason='使用实测首尾同词候选' if accepted else '没有可靠且可保留完整选段的首尾同词证据，回退为普通接歌，不编造歌词')
            if not accepted:reasons.append(analysis['wordplay_decision']['reason'])
        if fresh and balanced['mode']=='transition':
            balanced['transition_seconds']=min(balanced['transition_seconds'],.12 if self._risky(analysis,balanced) else 1.2)
            reasons.append('依据实际交接窗口检查，未知或有冲突时先减小同响；不把估计当作首拍或完整句。')
            ids.append('T04' if balanced['transition_seconds']<=.12 else 'T08')
        if request['policy']=='original':application={'applied':[]}
        else:balanced,application=apply_style(balanced,tracks,route)
        ids+=list(dict.fromkeys(i for r in application['applied'] for i in r['technique_ids']))
        reasons+=list(dict.fromkeys(r['reason'] for r in application['applied']))
        variants.append(dict(name='保留结构的自动版',project=balanced,techniques=ids,reasons=reasons,application=application))
        if request['policy']!='original':
            creative=copy.deepcopy(balanced);extra=[];changed=[]
            from .automatic_timing import propose_warp_map
            from .elastic_audio import tempo_ramp_map
            timing={}
            for side in ('a','b'):
                c=creative[side]
                if fresh and not c['preserve_original'] and not c.get('warp_map') and (request['policy']=='creative' or c['role']=='instrumental'):
                    evidence=propose_warp_map(analysis.get(side,{}),c['start'],c['end'],features)
                    timing[side]=evidence
                    if evidence['available']:
                        c['warp_map']=evidence['warp_map'];changed.append('T02')
                        extra.append(side.upper()+'只在实测完整覆盖的选段内作不超过60毫秒的轻微时序修正；未把未知摆动律动量化。')
                    elif _requested(request['prompt'],'速度渐变','渐快','渐慢'):
                        c['warp_map']=tempo_ramp_map(c['end']-c['start'],1.1,.9 if '渐慢' in request['prompt'] else 1.3,segments=8)
                        changed.append('T29');extra.append('按明确的速度渐变要求生成相对速度坡度，整体时长不变；这不是对拍结果。')
            analysis['timing_decisions']=timing
            # Only new work may receive a suggested constant tempo/key change.
            # Re-running an existing project preserves all its prior settings.
            comparison=analysis.get('comparison',{})
            shift=comparison.get('local_harmony',{}).get('proposed_b_shift')
            if fresh and not any(creative[s].get('warp_map') for s in ('a','b')) and not (getattr(self,'_tone_candidate',None) or {}).get('available',False) and isinstance(shift,int) and not isinstance(shift,bool) and 0<abs(shift)<=3 and not creative['b']['preserve_original'] and (request['policy']=='creative' or creative['b']['role']=='instrumental'):
                creative['b']['semitones']+=shift;extra.append('逐窗谐波分布一致支持有限移调，生成对照试听；不是已确认和弦识别。');changed.append('T07')
            if (creative['mode']=='mashup' and not analysis.get('no_overlap')
                    and creative['b_offset_seconds'] > .15 and 'degraded_stems' not in features):
                c=creative['b'];length=durations(creative)[1]
                vocal,backing=(analysis.get(side,{}) for side in ('a','b'))
                bass_overlap=all('bass_dense' in r.get('measured_features',[]) for r in (vocal,backing))
                real_stems=(tracks[creative['a']['track_id']].get('stem_role')=='vocal' and
                            tracks[c['track_id']].get('stem_role')=='instrumental')
                if real_stems and not c['preserve_original'] and not c.get('effects'):
                    fx=[]
                    if bass_overlap and not c['bass_db'] and length>=1:
                        stop=min(length*.4,2.0)
                        fx.append(dict(type='eq',band='low',points=[dict(time=0,gain_db=-3),dict(time=stop,gain_db=0)]))
                        extra.append('两边进入处都有较重的低频，让伴奏起步时少一点低频，再渐渐归还。');changed.append('T03')
                    if length>=2 and request['policy']=='creative' and _requested(request['prompt'],'滤波切入','伴奏渐亮','由暗到明'):
                        fx.append(dict(type='filter',mode='lowpass',points=[dict(time=0,cutoff=3500),dict(time=min(1.,length*.2),cutoff=20000)]))
                        extra.append('按要求让伴奏由暗到明切入，随后恢复全频。');changed.append('T41')
                    if fx:c['effects']=fx
            tone=getattr(self,'_tone_candidate',None)
            if tone and tone.get('available') and creative['a']['role']=='vocal' and tracks[creative['a']['track_id']].get('stem_role')=='vocal' and not creative.get('performance') and not creative['a']['preserve_original'] and not creative['a'].get('gain_envelope'):
                creative['performance']=tone['performance']
                last=max(e['time']+(e['repeats']-1)*e['interval']+e['end']-e['start'] for e in tone['performance']['events'])
                speed=creative['target_bpm']/creative['a']['bpm'] if creative['target_bpm'] else 1
                length=(creative['a']['end']-creative['a']['start'])/speed
                if last+.02<length:
                    creative['a']['gain_envelope']=[dict(time=0,gain=0),dict(time=last,gain=0),dict(time=last+.02,gain=1),dict(time=length,gain=1)]
                    extra.append('用A实测稳定音高的短样本演奏B的真实旋律候选；预告期间抽空A主唱，随后恢复，逐音并非整轨变调。');changed.append('T48')
                else:creative.pop('performance')
            if _requested(request['prompt'],'混响','回声','delay','reverb'):
                c=creative['a'];length=durations(creative)[0]
                if not c['preserve_original'] and not c.get('effects') and length>=1:
                    space=_requested(request['prompt'],'混响','reverb')
                    points=[dict(time=0,wet=0),dict(time=max(.01,length-min(2,length*.5)),wet=0),dict(time=length-.2,wet=.14),dict(time=length-.005,wet=0)]
                    if space:
                        effect=dict(type='reverb',decay=.6,pre_delay_ms=28,wet_highpass_hz=220,wet_lowpass_hz=6500,points=points)
                    else:
                        from .advanced_audio import beat_delay_ms
                        try:
                            if {'tempo_drift','rubato'} & set(features):raise ValueError('No stable tempo')
                            delay_ms=beat_delay_ms(creative.get('target_bpm') or c.get('bpm'),.5)
                            extra.append('半拍回声由工程速度候选换算，不据此确认拍网或首拍。')
                        except ValueError:
                            delay_ms=240
                            extra.append('速度未知或不稳定，短毫秒回声仅作对照，不标为节拍同步。')
                        effect=dict(type='delay',delay_ms=delay_ms,feedback=.3,
                            wet_highpass_hz=240,wet_lowpass_hz=6500,points=points)
                        if _requested(request['prompt'],'左右','乒乓','ping pong','pingpong'):
                            effect['stereo_mode']='pingpong'
                            extra.append('只让湿声左右交替；湿声折单声道，需复核抵消，不移动干声。')
                    c['effects']=[effect]
                    extra.append('在A尾段局部加入'+('预延迟并清理湿声低频的混响反射' if space else '清理湿声频段的短回声')+'并回到干声；效果不延伸出A片段，不代表自动空间匹配。');changed.append('T40' if space else 'T09')
            if fingerprint(creative)!=fingerprint(balanced):
                variants.append(dict(name='创意反差版',project=validate_project(creative,tracks),techniques=ids+changed,reasons=reasons+extra))
        return variants,route

    def _bridge_variants(self,key,request,p,fresh,analysis):
        """Add evidence-gated final-join arrangements to a new creative set.

        Existing projects, locks and prior performance never enter this path.
        Preparation failures keep the already-renderable baseline available.
        """
        if not fresh or request['kind']!='set' or request['policy']!='creative' or p.get('performance') or p['target_bpm'] is not None:
            return []
        from .transition_strategy import select_bridge,plan_drum_bridge
        from .accompaniment_bridge import plan_vocal_bridge,plan_progressive_bridge
        from .bridge_vocal_guard import review_outgoing_vocal
        from .drum_coverage import review_drum_bridge
        report=analysis.get('joins',[])[-1] if analysis.get('joins') else {}
        decision=select_bridge(request['prompt'],report,features=request.get('features',[]))
        decision.update(selected='fallback',join=len(p['joins'])-1,scope='last_join_only',unavailable=[])
        analysis['bridge_decision']=decision
        tracks=self._inventory();a,b=p['clips'][-2:]
        for c in (a,b):
            t=tracks[c['track_id']]
            if c['end']-c['start']<8 or c['role']!='full_mix' or c['preserve_original'] or t.get('preserve_original') or t.get('stem_role') or c['semitones'] or c['bass_db'] or any(c.get(k) for k in ('effects','gain_envelope','warp_map')):
                decision['reason']='选段过短、已有编辑或原貌锁，保留已有候选。'
                return []
        methods=[]
        if decision['preferred']!='fallback':methods.append(decision['preferred'])
        if decision['preferred']=='drum_bridge' and decision['allow_vocal_fallback']:methods.append('vocal_bridge')
        variants=[]
        for method in methods:
            try:
                tracks=self._inventory()
                def known(source,role,separation=None):
                    return next((t for t in tracks.values() if t.get('source_track_id')==source and t.get('stem_role')==role
                        and (separation is None or t.get('separation_id')==separation)),None)
                pair=None
                if method=='drum_bridge':
                    for t in tracks.values():
                        if t.get('source_track_id')==a['track_id'] and t.get('stem_role')=='drums' and t.get('separation_id'):
                            backing=known(a['track_id'],'instrumental',t['separation_id'])
                            if backing:pair=(backing['id'],t['id']);break
                missing=(pair is None if method=='drum_bridge' else known(a['track_id'],'instrumental') is None) or known(b['track_id'],'vocal') is None
                if missing:
                    if getattr(self.owner,'separation',None) is None:raise ValueError('missing separation')
                    self._stage(key,'separating',.37)
                if method=='drum_bridge':
                    if pair is None:
                        group=self._stem_group(key,a['track_id'],True)
                        pair=(group['instrumental']['id'],group['drums']['id'])
                    ai,ad=pair
                else:ai=self._separate(key,a['track_id'],'instrumental')
                bv=self._separate(key,b['track_id'],'vocal')
                # A backing-only role change can erase a phrase even though
                # source endpoints are unchanged. Check the outgoing vocal,
                # separately from pulse and incoming-vocal continuity.
                av=self._separate(key,a['track_id'],'vocal')
                tracks=self._inventory()
                proposal=(plan_drum_bridge(p,tracks,len(p['joins'])-1,ai,ad,bv,report)
                          if method=='drum_bridge' else plan_vocal_bridge(p,tracks,len(p['joins'])-1,ai,bv))
                guard=review_outgoing_vocal(p,proposal['project'],tracks,len(p['joins'])-1,av)
                decision.setdefault('vocal_activity_reviews',[]).append(dict(method=method,**guard))
                if not guard['eligible']:
                    decision['unavailable'].append(dict(method=method,reason=guard['reason']))
                    continue
                proposal['evidence']['outgoing_vocal_activity_guard']=guard
                if method=='drum_bridge':
                    coverage=review_drum_bridge(proposal['project'],tracks,len(p['joins'])-1,ad,report)
                    decision.setdefault('drum_coverage_reviews',[]).append(coverage)
                    if not coverage['eligible']:
                        decision['unavailable'].append(dict(method=method,reason=coverage['reason']))
                        continue
                    proposal['evidence']['drum_coverage_guard']=coverage
                priority=2 if method==decision['preferred'] else 1
                variants.append({**proposal,'arrangement_priority':priority,
                    'reasons':proposal['reasons']+[decision['reason'] if method==decision['preferred'] else '此候选作为允许清唱呼吸时的对照。']})
                if decision['selected']=='fallback':decision['selected']=method
                declines_layers=re.search(r'(?:不要|不用|不需要|不想|别|禁止)[^，,。；;！!？?\n]{0,4}(?:分层|渐进|逐层|鼓轨|鼓组|独立鼓)',request['prompt'])
                if method=='vocal_bridge' and decision['allow_progressive'] and not declines_layers:
                    try:
                        # Keep B independently renderable even if four-source
                        # preparation or the longer incoming intro is unusable.
                        roles=('vocal','other','drums','bass');group=None
                        for candidate in tracks.values():
                            if candidate.get('source_track_id')!=b['track_id'] or candidate.get('stem_role')!='vocal' or not candidate.get('separation_id'):continue
                            bundle={r:known(b['track_id'],r,candidate['separation_id']) for r in roles}
                            if all(bundle.values()):group={r:t['id'] for r,t in bundle.items()};break
                        if group is None:
                            if getattr(self.owner,'separation',None) is None:raise ValueError('四源暂不可用')
                            if b['end']-b['start']<12:raise ValueError('余量不足，保留B')
                            self._stage(key,'separating',.38)
                            bundle=self._stem_group(key,b['track_id'],True)
                            group={r:bundle[r]['id'] for r in roles}
                        layered=plan_progressive_bridge(p,self._inventory(),len(p['joins'])-1,ai,group,report)
                        layered_guard=review_outgoing_vocal(p,layered['project'],self._inventory(),len(p['joins'])-1,av)
                        if not layered_guard['eligible']:raise ValueError('渐进候选未通过前曲人声保护')
                        layered['evidence']['outgoing_vocal_activity_guard']=layered_guard
                        variants.append({**layered,'arrangement_priority':priority+.25,
                            'reasons':layered['reasons']+[decision['progressive_reason']]})
                    except (ValueError,RuntimeError,KeyError,OSError):
                        decision['progressive_unavailable']='后曲四源、长度或一致性条件不足，保留清唱呼吸B；未冒充完成独立分层。'
            except (ValueError,RuntimeError,KeyError,OSError):
                # Do not leak decoder paths or misrepresent missing stems.
                decision['unavailable'].append(dict(method=method,reason='所需真实分轨、可用区间或局部节奏条件未满足；保留原曲候选。'))
        if decision['selected']=='fallback' and decision['unavailable']:
            decision['reason']='铺垫路线条件不足，当前沿用可渲染的原曲候选；没有冒充分轨或宣称接歌听感通过。'
        return variants

    def _execute(self,key,request):
        directory=self.root/key
        try:
            self._stage(key,'preparing',.01)
            self._entry_evidence=None;self._special_evidence=None;self._tone_candidate=None
            p,fresh=self._initial(key,request);tracks=self._inventory()
            if request['policy']=='creative' and request['kind']=='pair' and fresh and _requested(request['prompt'],'tone play','toneplay','逐音','旋律预告'):
                from .melody_events import tone_play_candidate
                self._stage(key,'separating',.08)
                vocal_ids=[self._separate(key,i,'vocal') for i in request['track_ids']]
                tracks=self._inventory()
                self._tone_candidate=tone_play_candidate(tracks[vocal_ids[0]],tracks[vocal_ids[1]],durations(p)[2])
            self._stage(key,'analyzing',.12)
            analysis=self._analyze(key,p,tracks,request['kind'])
            if self._entry_evidence:analysis['entry_evidence']=self._entry_evidence
            if self._tone_candidate:analysis['tone_play']=self._tone_candidate
            self._stage(key,'planning',.36)
            variants,route=self._plans(request,p,fresh,tracks,analysis)
            variants=self._bridge_variants(key,request,p,fresh,analysis)+variants
            tracks=self._inventory()
            if self._special_evidence:
                evidence=self._special_evidence
                analysis['stem_arrangement']={k:v for k,v in evidence.items() if k!='project'}
                for c in variants:
                    c['reasons']+=evidence.get('reasons',[]);c['techniques']+=evidence.get('techniques',[])
            from .transition_refinement import choose_candidate
            tuning = analysis['mix_tuning'] = dict(status='not_applied', applied=[])
            if (request['policy'] != 'original' and '保持原貌' not in request['prompt']
                    and not any(term in request['prompt'] for term in ('不要调音','不需要调音','不改音色'))
                    and not re.search(r'(?:^|[，,。；;])\s*只接歌(?:$|[，,。；;\s])',request['prompt'])
                    and not _requested(request['prompt'], '只做拼接','仅拼接')):
                from .automatic_mix_tuning import tune_project
                preferred=choose_candidate([dict(c,index=i,machine_eligible=True) for i,c in enumerate(variants)],request['policy'])
                probe_reports=[analysis.get(side,{}) for side in ('a','b')]
                degraded='degraded_stems' in request['features'] or any('degraded_stems' in r.get('measured_features',[]) for r in probe_reports)
                try:
                    tuned,edits=tune_project(preferred['project'],tracks,request['project'],
                        allow_full_mix=request['policy']=='creative',degraded_stems=degraded,
                        preview=not request['export'],check=lambda:self._check(key))
                    if edits:
                        validator=validate_set if request['kind']=='set' else validate_project
                        tuned=validator(tuned,tracks)
                        base_index=preferred['index'];base_candidate=variants[base_index]
                        variants[base_index]={**base_candidate,'name':base_candidate['name']+' · 未加局部调音'}
                        variants.append(dict(base_candidate,name=base_candidate['name']+' · 局部调音',
                            project=tuned,mix_refined_from=base_index,mix_edits=edits,
                            techniques=list(dict.fromkeys(base_candidate['techniques']+[i for e in edits for i in e['technique_ids']])),
                            reasons=base_candidate['reasons']+list(dict.fromkeys(e['reason'] for e in edits))))
                        tuning['status']='candidate'
                    else:tuning['status']='no_supported_edit'
                except InterruptedError:raise
                except (OSError,ValueError,RuntimeError,ImportError):
                    tuning['status']='analysis_unavailable'
            results=[]
            for i,c in enumerate(variants):
                self._stage(key,'rendering',.4+.25*i/max(1,len(variants)))
                folder=directory/f'candidate-{i}'
                try:
                    result=(render_set if request['kind']=='set' else render)(c['project'],tracks,folder,preview=not request['export'])
                except InterruptedError:raise
                except (OSError,ValueError,RuntimeError):
                    if 'mix_refined_from' not in c:raise
                    tuning['status']='render_unavailable'
                    continue
                results.append(dict(**c,result=result,index=i))
            self._stage(key,'checking',.72)
            from .delivery_audio import normalize_comparison,delivery_target
            delivery=delivery_target('social' if any(w in request['prompt'] for w in ('短视频','发布','分享')) else 'listening')
            comparison=normalize_comparison([directory/f"candidate-{c['index']}"/'audio.wav' for c in results],directory,
                delivery['target_lufs'],peak_ceiling_dbtp=delivery['peak_ceiling_dbtp'])
            target=comparison['effective_lufs']
            for c,listening in zip(results,comparison['candidates']):
                self._check(key);folder=directory/f"candidate-{c['index']}"
                shutil.copyfile(listening['wav'],folder/'listening.wav')
                shutil.copyfile(listening['mp3'],directory/f"candidate-{c['index']}.mp3")
                c['listening_quality']=listening['quality']
                c['machine_eligible']=listening['status']=='measured'
                c['human_listening_status']='pending'
            eligible=[c for c in results if c['machine_eligible']]
            if not eligible:raise ValueError('候选未通过编码后峰值检查，未交付为合格作品')
            # Preference policy orders machine-eligible candidates. This is not
            # a learned quality score or a replacement for user listening.
            chosen=choose_candidate(results,request['policy'],decision=analysis.get('bridge_decision'))
            if chosen.get('mix_edits'):
                tuning.update(status='applied',applied=chosen['mix_edits'],baseline_candidate=chosen['mix_refined_from'])
            elif tuning['status']=='candidate':tuning['status']='fallback'
            self._stage(key,'delivering',.93)
            shutil.copyfile(directory/f"candidate-{chosen['index']}"/'listening.wav',directory/'audio.wav')
            shutil.copyfile(directory/f"candidate-{chosen['index']}.mp3",directory/'audio.mp3')
            assets=([c['track_id'] for c in chosen['project']['clips']] if request['kind']=='set' else [chosen['project'][s]['track_id'] for s in ('a','b')])
            assets += [e['track_id'] for e in chosen['project'].get('performance',{}).get('events',[])]
            from .dynamics_audio import sidechain_track_ids
            assets += sidechain_track_ids(chosen['project'])
            public=[{k:v for k,v in tracks[i].items() if k!='path'} for i in dict.fromkeys(assets)]
            save_json(directory/'project.json',dict(format='deepmixing-set-v1' if request['kind']=='set' else 'deepmixing-project-v1',project=chosen['project'],assets=public,render_result=chosen['result'],human_listening_status='pending'))
            coverage=recipe_coverage(route,[*chosen['techniques'],'T16','T17'])
            base=f'/automation-outputs/{key}/'
            outputs=dict(wav=base+'audio.wav',mp3=base+'audio.mp3',project=base+'project.json',report=base+'report.json',
                candidates=[dict(name=c['name'],url=base+f"candidate-{c['index']}.mp3") for c in results])
            with self.lock:
                self._check(key)
                task=self.tasks[key]
                task.update(status='succeeded',stage='complete',progress=1,kind=request['kind'],project=chosen['project'],
                    analysis=analysis,candidates=results,selected=chosen['index'],coverage=coverage,outputs=outputs,
                    explanations=chosen['reasons']+([analysis['bridge_decision']['reason']] if analysis.get('bridge_decision',{}).get('selected') in ('fallback','vocal_bridge') else [])+['候选已做等响度比较与编码后峰值检查；自动选择依据是改造策略与机器检查，听感仍等你评价。'],
                    audio_processed=True,full_length=request['export'],target_lufs=round(target,2),delivery_target=delivery,finished_at=time.time())
                save_json(directory/'report.json',task);self._save(task)
        except InterruptedError:
            with self.lock:
                self.tasks[key].update(status='cancelled',stage='cancelled',finished_at=time.time(),audio_processed=False);self._save(self.tasks[key])
        except Exception as exc:
            # Keep filesystem/decoder diagnostics out of both provider context
            # and public tasks. Original inputs and completed stems remain.
            with self.lock:
                failed_stage=self.tasks[key]['stage']
                self.tasks[key].update(status='failed',stage='failed',failed_stage=failed_stage,error_type=type(exc).__name__,finished_at=time.time(),audio_processed=False,
                    error='自动任务未完成：请检查素材、分轨模型或分析依赖；原素材保留，可按原任务状态检查后重试。')
                self._save(self.tasks[key])

    def output_file(self,key,name):
        if name not in OUTPUTS and not re.fullmatch(r'candidate-[0-7]\.mp3',name):raise ValueError('不允许的自动输出文件')
        task=self.job(key)
        if task['status']!='succeeded':raise ValueError('自动任务尚未成功完成')
        if name not in OUTPUTS and not any(c.get('url')==f'/automation-outputs/{key}/{name}' for c in task['outputs'].get('candidates',[])):
            raise ValueError('不存在的候选试听')
        path=self.root/key/name
        if not path.is_file():raise ValueError('自动输出文件不存在')
        return path

    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
            for key,t in self.tasks.items():
                if t['status'] not in TERMINAL:self.events[key].set()
        self.executor.shutdown(wait=True,cancel_futures=False)
