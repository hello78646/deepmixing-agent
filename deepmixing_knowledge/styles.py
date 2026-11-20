"""Inspectable style/feature/task routing over validated recipes, not audio classification."""
import copy
import hashlib
import json
import re

from .library import STATUSES
from .transitions import TransitionOptions
from .musical_review import MusicalContinuityReview

def _key(value):
    if not isinstance(value,str) or not value.strip():raise ValueError('曲风须为非空文本')
    return ''.join(re.findall(r'[a-z0-9\u3400-\u9fff]+',value.casefold()))

def _text_list(value):
    return isinstance(value,list) and bool(value) and all(isinstance(x,str) and x.strip() for x in value)

def _brief_review(value):
    """Keep human intent distinct from measurements and executable locks."""
    if value is None:value={}
    fields={'mood','focus','energy','allow_breath','protected_moments'}
    if not isinstance(value,dict) or set(value)-fields:raise ValueError('创作意图包含不支持的字段')
    if 'mood' in value and (not isinstance(value['mood'],str) or not value['mood'].strip() or len(value['mood'])>200):
        raise ValueError('心境须为1–200字文本')
    if 'focus' in value and value['focus'] not in ('vocal','melody','groove','balanced'):raise ValueError('未知突出主角')
    if 'energy' in value and value['energy'] not in ('steady','rise','fall','wave','contrast','unknown'):raise ValueError('未知能量方向')
    if 'allow_breath' in value and type(value['allow_breath']) is not bool:raise ValueError('是否允许短暂降能量须为布尔值')
    if 'protected_moments' in value:
        moments=value['protected_moments']
        if not isinstance(moments,list) or len(moments)>8 or any(not isinstance(x,str) or not 0<len(x.strip())<=200 for x in moments):
            raise ValueError('必留描述须为最多8条、每条1–200字文本')
    checks=[]
    if 'mood' in value:checks.append('保留用户心境原话；忧郁、愤怒等不能自动推断速度、能量或接歌路线。')
    focus=value.get('focus')
    if focus=='vocal':checks.append('突出主唱：先核实弱起、完整词句与伴奏退让，不默认撤掉所有鼓。')
    elif focus=='melody':checks.append('突出旋律：核实实际同响和声与动机空间，不因全局同调而保留双旋律竞争。')
    elif focus=='groove':checks.append('突出律动：核实局部鼓型、重音和细分口袋；同BPM不够。')
    elif focus=='balanced':checks.append('均衡角色：逐段明确主角，避免所有声部同时推满。')
    if 'energy' in value:checks.append('能量方向是用户目标；分别核对速度、密度、低频、熟悉线索和氛围，未由音频证实。')
    if value.get('allow_breath') is False:checks.append('用户要求不降能量：不选当前会撤伴奏留清唱的呼吸模板。')
    if value.get('protected_moments'):checks.append('必留描述须映射到真实素材时间并核对现有锁；这些文字尚未创建工程锁。')
    return dict(supplied=copy.deepcopy(value),unknown_fields=sorted(fields-set(value)),checks=checks,
                audio_verified=False,project_locks_created=False,
                scope='创作意图只影响方法候选和前提检查，不确认音频特征、不自动渲染，也不替代工程锁。')

class StyleTechniqueRouter:
    def __init__(self,library,*,data=None):
        self.library=library
        self.data=copy.deepcopy(data) if data is not None else json.loads((library.kb.root/'knowledge/style-profiles.json').read_text(encoding='utf-8'))
        self.aliases={};self.profiles={}
        self._validate()
        self.transitions=TransitionOptions(library)
        self.musical_review=MusicalContinuityReview(library)
        payload=dict(library_hash=library.library_hash,rules=self.data,transition_families=self.transitions.data,
                     musical_review=self.musical_review.data,creative_brief_version=1,continuity_review_version=1)
        self.routing_hash=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def _validate(self):
        data=self.data
        if not isinstance(data,dict) or data.get('schema_version')!=1 or not isinstance(data.get('profiles'),list) or not data['profiles']:
            raise ValueError('invalid style routing data')
        def refs(ids,empty=False):
            if not isinstance(ids,list) or (not ids and not empty) or any(not isinstance(i,str) or i not in self.library.by_id for i in ids):
                raise ValueError('invalid routing technique reference')
        for p in data['profiles']:
            if not isinstance(p,dict) or not isinstance(p.get('id'),str) or p['id'] in self.profiles or p['id'] not in self.library.taxonomy['styles']:
                raise ValueError('invalid/duplicate style profile')
            if not isinstance(p.get('label'),str) or not isinstance(p.get('evidence_basis'),str):raise ValueError('missing profile provenance')
            for field in ('aliases','traits','checks'):
                if not _text_list(p.get(field)):raise ValueError('invalid style profile '+field)
            for field in ('priority_techniques','optional_techniques'):refs(p.get(field))
            self.profiles[p['id']]=p
            for alias in [p['id'],p['label'],*p['aliases']]:
                k=_key(alias)
                if k in self.aliases and self.aliases[k]!=p['id']:raise ValueError('ambiguous style alias '+alias)
                self.aliases[k]=p['id']
        for section in ('features','goals'):
            if not isinstance(data.get(section),dict) or not data[section]:raise ValueError('missing routing '+section)
            for rule in data[section].values():
                if not isinstance(rule,dict) or not isinstance(rule.get('label'),str) or not _text_list(rule.get('checks')):raise ValueError('invalid routing rule')
                refs(rule.get('prefer'));refs(rule.get('avoid'),empty=True)
        if not isinstance(data.get('workflow'),dict):raise ValueError('missing routing workflow')
        for phase in ('prepare','creative','delivery'):refs(data['workflow'].get(phase))
        for field in ('universal_checks','cross_style_notes'):
            if not _text_list(data.get(field)):raise ValueError('missing routing '+field)

    def resolve_style(self,value):
        style=self.aliases.get(_key(value))
        if style is None:raise ValueError('未收录该曲风；请用具体类型或open_format，不根据歌名猜测')
        return style

    def catalog(self):return copy.deepcopy(self.data['profiles'])

    def plan(self,styles,*,features=None,goal='transition',query='',status=None,creative_brief=None):
        if not isinstance(styles,list) or not 1<=len(styles)<=3:raise ValueError('请提供1–3个曲风类型，可含live版本属性')
        if features is None:features=[]
        if not isinstance(features,list) or len(features)>14 or any(not isinstance(f,str) or f not in self.data['features'] for f in features):
            raise ValueError('声音特征必须使用已收录标签；未知时不填写，不按曲风猜测')
        if not isinstance(goal,str) or goal not in self.data['goals']:raise ValueError('未知混音目标')
        if status is not None and (not isinstance(status,str) or status not in STATUSES):raise ValueError('未知执行状态')
        if not isinstance(query,str) or len(query)>500:raise ValueError('细化问题须为0–500字文本')
        brief=_brief_review(creative_brief)
        ids=list(dict.fromkeys(self.resolve_style(s) for s in styles));features=list(dict.fromkeys(features))
        selected=[self.profiles[s] for s in ids];scores={};reasons={};excluded={}
        checks=list(self.data['universal_checks'])+brief['checks']
        def add(tid,weight,why):
            scores[tid]=scores.get(tid,0)+weight
            reasons.setdefault(tid,[])
            if why not in reasons[tid]:reasons[tid].append(why)
        for p in selected:
            checks+=p['checks']
            for i,tid in enumerate(p['priority_techniques']):add(tid,20-i*1.5,p['label']+'的优先方法；仍须核对实际片段')
            for tid in p['optional_techniques']:add(tid,4,p['label']+'的可选创意，不是默认执行')
        for feature in features:
            rule=self.data['features'][feature];checks+=rule['checks']
            for i,tid in enumerate(rule['prefer']):add(tid,50-i*6,'声音特征：'+rule['label'])
            for tid in rule['avoid']:excluded.setdefault(tid,[]).append(rule['label']+'：'+rule['checks'][0])
        task=self.data['goals'][goal];checks+=task['checks']
        for i,tid in enumerate(task['prefer']):add(tid,10-i,'当前目标：'+task['label'])
        for tid in task['avoid']:excluded.setdefault(tid,[]).append(task['label']+'：'+task['checks'][0])
        for phase,methods in self.data['workflow'].items():
            for tid in methods:add(tid,3,'流程检查：'+phase)
        focus=brief['supplied'].get('focus')
        for tid in {'vocal':['T65','T37'],'melody':['T07','T55'],'groove':['T66','T13'],'balanced':['T67']}.get(focus,[]):
            add(tid,60,'用户明确要突出的角色；仍须核实素材前提')
        if brief['supplied'].get('allow_breath') is False:
            for tid in ('T62','T63'):
                excluded.setdefault(tid,[]).append('用户不允许短暂降能量；该配方/当前自动候选需要清唱呼吸，不作为本次执行方法。')
        cross_style=len([s for s in ids if s not in ('live','open_format')])>1 or 'open_format' in ids
        if cross_style:
            for tid in ('T29','T08','T37'):add(tid,8,'跨风格交棒需要额外检查')
        allowed={tid for tid in scores if tid not in excluded and (status is None or self.library._execution(self.library.by_id[tid])['status']==status)}
        matches={};query_status='not_requested'
        if query.strip():
            hits=self.library.kb.retrieve(query,k=20,card_ids=allowed)
            # One accidental Chinese bigram (e.g. “芯片”/“切片”) must not
            # manufacture a genre-specific answer to an unrelated question.
            hits=[h for h in hits if h['lexical_support']['query_term_coverage']>=.2 and
                  (h['lexical_support']['matched_query_terms']>=2 or any(tag.casefold() in query.casefold() for tag in h['card']['tags'] if len(tag)>=3))]
            matches={h['card']['id']:h for h in hits};allowed&=matches.keys()
            query_status='lexical_candidates_require_review' if matches else 'no_lexical_match'
            for tid,hit in matches.items():add(tid,hit['score']*40,'细化问题的本地词项命中；分数不是适用置信度')
        ordered=sorted(allowed,key=lambda tid:(-scores[tid],tid))
        candidates=[]
        for tid in ordered[:8]:
            d=self.library.detail(tid)
            candidates.append(dict(id=tid,title=d['card']['title'],category=d['category'],reasons=reasons[tid],
              prerequisites=d['prerequisites'],checks=d['card']['checks'],risks=d['card']['avoid_or_verify'],
              execution=d['execution'],current_alternative=d['current_alternative'],
              sources=[dict(id=s['id'],title=s['title'],url=s.get('url'),observed_on=s['observed_on']) for s in d['sources']],
              audio_processed=False))
        # Workflow lists expose the complete applicable method map, separately
        # from the small ranked shortlist. They are references, never commands.
        workflow={phase:[t for t in methods if t in allowed] for phase,methods in self.data['workflow'].items()}
        creative=[t for p in selected for t in p['priority_techniques']+p['optional_techniques']]+task['prefer']
        workflow['creative']=list(dict.fromkeys(t for t in creative+workflow['creative'] if t in allowed))
        options=self.transitions.plan(features) if goal in ('transition','continuous') else []
        if brief['supplied'].get('allow_breath') is False:
            for row in options:
                if row['id']=='vocal_bridge':
                    row['state']='excluded';row['excluded_by'].append('brief:allow_breath_false')
                if row['id']=='progressive_entry':
                    row['automatic_blocked_by']=['brief:allow_breath_false']
                    row['automatic_scope']+=' 当前自动清唱后分层候选不符合本次不降能量要求；保鼓点的分层需另行设计验证。'
            options.sort(key=lambda r:(r['state']=='excluded',-len(r['matched_features']),r['id']))
        # This is a planning checklist, not DSP enforcement or an audio verdict.
        # Keep it separate from the ranked shortlist so lexical queries and
        # genre-specific priorities cannot hide the continuity review.
        continuity_review=dict(status='not_evaluated',audio_verified=False,renderer_enforced=False,
          required=goal in ('transition','continuous'),fallback='last_user_accepted_reference',
          methods=[dict(id=tid,title=self.library.cards[tid]['title'],execution=self.library.detail(tid)['execution'])
                   for tid in ('T68','T69','T70') if tid in self.library.by_id],
          checks=['检查谁在承载律动与低频，不能只防叠加却忽略两侧同时撤空。',
                  '局部脉冲、强拍、词句和音乐乐句分开确认；未知时不强造Drop。',
                  '使用分轨时检查实际处理后重建、相位和原曲切换音色，不能只看共同BPM。',
                  '新创意逐项局部对照；用户否定不能被时长/峰值/波形变化覆盖。'],
          scope='提供给聊天Agent的方法索引与待核查条件；未检查当前素材，也没有阻断或批准任何渲染。')
        return dict(styles=[dict(id=p['id'],label=p['label'],traits=list(p['traits']),evidence_basis=p['evidence_basis']) for p in selected],
          features=features,goal=goal,classification_source='caller_supplied_not_audio_detection',cross_style=cross_style,
          cross_style_notes=list(self.data['cross_style_notes']) if cross_style else [],
          query_status=query_status,workflow=workflow,candidates=candidates,
          excluded=[dict(id=t,reasons=r) for t,r in sorted(excluded.items())],checks=list(dict.fromkeys(checks)),
          feature_options={k:v['label'] for k,v in self.data['features'].items()},
          transition_options=options,brief_review=brief,continuity_review=continuity_review,
          musical_review=self.musical_review.plan(features,brief['supplied'],goal),
          routing_hash=self.routing_hash,library_hash=self.library.library_hash,audio_processed=False,requires_applicability_review=True,
          limitation='按调用者提供的类型/特征路由，不是自动音频曲风识别；来源覆盖和风格迁移不同。分数不是质量概率，所需控制具备仍须核查素材、用户锁和真实试听。')
