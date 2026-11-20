"""Turn local signal evidence and cited methods into bounded, reversible joins.

These are conservative starting points, not genre recognition or a quality score.
The client still executes preview/render, with its normal history and stale guards.
"""
import copy
import json
import math

from deepmixing_knowledge import KnowledgeBase
from .models import validate_project
from .grid_evidence import grid_review_reasons


def _groove_diagnosis(tools, first, second):
    """Analyze only independent drum stems; full mixes remain unknown."""
    if any(tools.assets.get(c['track_id'],{}).get('stem_role') != 'drums' for c in (first,second)):
        return dict(status='unavailable', compatible=None,
                    reason='两侧没有同时可用的独立鼓组；不从完整混音臆测鼓型或口袋。',
                    limitations=['需要同源或明确对应的drums分轨；主唱和伴奏全混音不能代替鼓组证据。'])
    try:
        from .groove_analysis import analyze_file, compare_grooves
        if any(c.get('warp_map') or c.get('effects') or c.get('gain_envelope') or
               c.get('bass_db') or c.get('semitones') for c in (first,second)):
            raise ValueError('原始鼓轨窗口不代表已处理后的律动')
        first_path = tools.service.track_file(first['track_id'])
        second_path = tools.service.track_file(second['track_id'])
        a = analyze_file(first_path, max(first['start'],first['end']-24), first['end'], first.get('bpm'))
        b = analyze_file(second_path, second['start'], min(second['end'],second['start']+24), second.get('bpm'))
        comparison = compare_grooves(a, b)
        return dict(status=comparison['status'], first=a, second=b,
                    comparison=comparison,
                    evidence_scope='局部鼓分轨包络与16格重音代理，不是鼓件转写、Swing测量或听感评分。')
    except (ValueError, RuntimeError, OSError):
        return dict(status='unavailable', compatible=None,
                    reason='独立鼓组无法读取或分析；保留未知，不自动改鼓。',
                    limitations=['本地分析依赖或素材读取失败；不以失败结果替代听感。'])


def _signature(tools):
    return json.dumps([tools.kind,tools.project],sort_keys=True,ensure_ascii=False,allow_nan=False)


def _finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def _local_hearing_diagnosis(tools, outgoing, incoming):
    """Get fine-grained source-window evidence without inventing stems."""
    try:
        from .local_hearing import analyze_handoff
        left = tools.assets.get(outgoing.get('track_id'))
        right = tools.assets.get(incoming.get('track_id'))
        if not left or not right:
            return {'status':'unavailable','reason':'交接素材不存在，保留未知。'}
        return analyze_handoff(left, right, outgoing_end=outgoing['end'],
                               incoming_start=incoming['start'], window_seconds=4.0)
    except (ValueError, RuntimeError, OSError, TypeError, KeyError):
        return {'status':'unavailable','reason':'细粒度交接窗口分析未完成，保留未知。'}


def diagnose_mix(tools):
    if not tools.project:raise ValueError('请先选素材建立双轨或串烧，再检查接法')
    p=tools.validate(tools.project)
    join=p['preview_join'] if tools.kind=='set' else None
    a,b=(p['clips'][join:join+2] if tools.kind=='set' else (p['a'],p['b']))
    mashup=tools.kind=='pair' and p['mode']=='mashup'
    probe={'mode':'mashup' if mashup else 'transition','a':copy.deepcopy(a),'b':copy.deepcopy(b),
           'target_bpm':p['target_bpm'],'transition_seconds':0,'echo':0}
    findings=[]
    def finding(code,message):findings.append({'code':code,'message':message})
    inspectable=True
    # Outgoing tail/incoming head, rather than incorrectly diagnosing a long
    # song's intro for a transition near its end. Never change the user's trims.
    if mashup:
        sa=p['target_bpm']/a['bpm'] if p['target_bpm'] else 1
        sb=p['target_bpm']/b['bpm'] if p['target_bpm'] else 1
        overlap=min((a['end']-a['start'])/sa-p['b_offset_seconds'],(b['end']-b['start'])/sb)
        window=min(overlap,24/sa,24/sb)
        inspectable=window*min(sa,sb)>=.25
        if inspectable:
            probe['a']['start']=a['start']+p['b_offset_seconds']*sa
            probe['a']['end']=min(a['end'],probe['a']['start']+window*sa)
            probe['b']['end']=min(b['end'],b['start']+window*sb)
        else:
            finding('no_overlap' if overlap<=0 else 'overlap_too_short',
                    '当前人声与伴奏没有同响区域，不能判断叠搭和声。' if overlap<=0 else '同响窗口不足本地分析最短时长；不能据此判断叠搭和声。')
    else:
        probe['a']['start']=max(a['start'],a['end']-24)
        probe['b']['end']=min(b['end'],b['start']+24)
    report={}
    if inspectable:
        probe=validate_project(probe,tools.assets)
        try:
            report=tools.local_call(tools.service.inspect_music,probe)
        except ValueError:
            finding('analysis_unavailable','本地音频分析未完成；以下仅为保守接法，未检测到可用拍网或调性依据。')
    groove = (_groove_diagnosis(tools,probe['a'],probe['b']) if inspectable and
              all(p['target_bpm'] is None or abs(p['target_bpm']/c['bpm']-1)<1e-9 for c in (a,b)) else
              dict(status='unavailable',compatible=None,reason='没有原速且可比的鼓轨窗口，保留未知'))
    from .drum_overlap import review_drum_overlap
    drum_overlap = review_drum_overlap(p, tools.assets, join, resolve_path=getattr(tools.service,'track_file',None))
    from .vocal_overlap import review_vocal_overlap
    vocal_overlap = review_vocal_overlap(p,tools.assets,join,resolve_path=getattr(tools.service,'track_file',None))
    local_hearing = _local_hearing_diagnosis(tools, a, b)
    bpms=[];key_overrides={};boundaries={}
    for s,clip in zip(('a','b'),(a,b)):
        region=report.get(s,{})
        grid=region.get('grid',{})
        grid=copy.deepcopy(grid)
        # Key provenance is explicit. Existing guide BPM has no provenance:
        # inspect() writes estimates there too, so it cannot replace a fresh
        # local tempo estimate even when its broader region covers this probe.
        g=p.get('musical_guides',{}).get(s,{}) if tools.kind=='pair' else {}
        covered=(inspectable and g.get('track_id')==clip['track_id'] and g.get('start',math.inf)<=probe[s]['start']
                 and g.get('end',-1)>=probe[s]['end'])
        value=grid.get('bpm')
        speed=p['target_bpm']/clip['bpm'] if p['target_bpm'] else 1
        bpms.append(value*speed if _finite(value) and value>0 else None)
        if covered and g.get('key_source')=='user':
            key_overrides[s]={'status':'user','tonic':g['key_tonic'],'mode':g['key_mode']}
        review_reasons=grid_review_reasons(region)
        grid_usable=(grid.get('status')=='estimated' and not review_reasons and
                     not (_finite(grid.get('interval_variation')) and grid['interval_variation']>.08))
        boundaries[s]=[{k:v[k] for k in ('time','strength','kind','reason') if k in v}
                       for v in region.get('phrase_changes',[])[:4]
                       if grid_usable and _finite(v.get('time')) and probe[s]['start']<=v['time']<=probe[s]['end']]
        if review_reasons:
            finding('grid_evidence_review',s.upper()+'轨拍相位、速度层或局部拍网仍需复核；暂不据此推荐对拍叠化或乐句切点。')
        if grid.get('status')!='estimated' or bpms[-1] is None:
            finding('grid_unknown',s.upper()+'轨交接窗口的拍网未知；不能保证长时间对拍。')
        if grid.get('diagnostics',{}).get('review_required') or (_finite(grid.get('interval_variation')) and grid['interval_variation']>.08):
            finding('grid_unstable',s.upper()+'轨候选拍点偏离均匀网格，可能来自节奏变化或检测误差；先短接或人工校正。')
        if region.get('key_windows',{}).get('status')=='varying_candidates':
            finding('key_varying',s.upper()+'轨局部调性候选不一致；不等于已经确认转调。')
    gap=None
    if all(v is not None for v in bpms):
        gap=abs(bpms[0]-bpms[1])/max(bpms)
        ratio=max(bpms)/min(bpms)
        if abs(ratio-2)<.06:
            finding('half_double_ambiguity','两处脉冲接近半速/倍速关系，需核实鼓组重音；不能直接当作对拍成功。')
        elif gap>.025:
            finding('tempo_gap',f'两处有效脉冲约{bpms[0]:.1f}/{bpms[1]:.1f} BPM，长叠加容易漂移；本轮不擅自拉速。')
    harmony=report.get('harmony',{'relation':'unknown'})
    if key_overrides:
        from .music_guides import compare_keys
        harmony=compare_keys(key_overrides.get('a',report.get('a',{}).get('key',{})),
                             key_overrides.get('b',report.get('b',{}).get('key',{})),a['semitones'],b['semitones'])
    relation=harmony.get('relation','unknown')
    if relation=='review':finding('key_conflict','交接区域调性候选距离较远；短交接可减少旋律同时重叠，和弦是否冲突仍需试听。')
    elif relation=='unknown':finding('key_unknown','调性依据不足；未知不是不兼容，也不能作为长叠搭已经合适的证据。')
    local_harmony=report.get('local_harmony',{})
    chord_evidence=local_harmony.get('comparison',{}).get('chord_evidence',{}) if isinstance(local_harmony,dict) else {}
    if chord_evidence.get('status')=='review':
        finding('chord_conflict_candidate',
                '局部三和弦候选显示共同音偏少或冲突音偏多；这只是候选证据，建议缩短重叠、轮换旋律主角或先试听有限移调。')
    elif chord_evidence.get('status')=='unknown':
        finding('chord_evidence_insufficient',
                '交接窗口缺少稳定的局部和弦候选；不把未知当作兼容，建议以短接和试听确认。')
    if a['role']=='full_mix' and b['role']=='full_mix':
        finding('vocal_overlap_unverified','两份都是完整混音，尚未确认交接处是否有人声抢句；建议短接，或分轨后明确人声和伴奏角色。')
    if local_hearing.get('status') == 'measured':
        local_flags = set(local_hearing.get('risk_flags', []))
        if 'low_frequency_collision' in local_flags:
            finding('local_low_frequency_collision', '交接两侧退出尾与进入头的低频占比都偏高；中途叠化应先短后长，避免底鼓和贝斯堆积。')
        if 'energy_step' in local_flags:
            finding('local_energy_step', '交接两侧局部响度台阶明显；先做短淡入淡出，避免进入段突然顶上或塌陷。')
        if 'transient_density_mismatch' in local_flags:
            finding('local_transient_mismatch', '交接两侧瞬态密度差异明显；长叠化可能让鼓点边缘发乱，优先短中途交棒。')
        if 'mid_presence_crowding' in local_flags:
            finding('local_mid_presence_crowding', '交接两侧中频/存在感频段同时偏密；完整混音不能据此确认人声，但需警惕主旋律互抢。')
        if 'stereo_phase_review' in local_flags:
            finding('local_stereo_phase_review', '局部立体声相关性出现反相候选；先试听单声道兼容和低频，不自动翻相。')
    if groove.get('comparison', {}).get('compatible') is False:
        for reason in groove['comparison'].get('reasons', []):
            finding('groove_pocket_conflict', reason)
    risky=any(v['code'] in ('analysis_unavailable','grid_unknown','grid_unstable','grid_evidence_review','tempo_gap','half_double_ambiguity',
                            'key_conflict','key_unknown','key_varying','chord_conflict_candidate','chord_evidence_insufficient','vocal_overlap_unverified',
                            'groove_pocket_conflict','local_low_frequency_collision','local_energy_step',
                            'local_transient_mismatch','local_mid_presence_crowding','local_stereo_phase_review') for v in findings)
    from .style_execution import plan_style
    style_plan=plan_style('mashup' if mashup else 'transition')
    query='跨风格 大BPM差 异速 短切 接歌' if risky else '连贯 接歌 拍点 调性 交接'
    if mashup:query='清唱 人声 伴奏 角色替换 调性冲突'
    hits=KnowledgeBase().retrieve(query,k=3)
    # Keep provider context focused; the full archival evidence stays on disk.
    hits=[{'card':{k:h['card'][k] for k in ('id','title','summary','when_to_use','avoid_or_verify','audio_execution_supported','attribution')},
           'sources':[{k:s.get(k) for k in ('id','title','url','observed_on','type')} for s in h['sources']],
           'requires_applicability_review':True} for h in hits]
    # No source claim stronger than the measurements; the adapter deliberately
    # uses only already-supported duration changes, not loops/ramp automation.
    lengths=[(c['end']-c['start'])/(p['target_bpm']/c['bpm'] if p['target_bpm'] else 1) for c in (a,b)]
    short=min(.12,*lengths)
    blend=min(1.2,*lengths)
    if tools.kind=='set':
        # Adjacent overlaps must leave >=.25s of the intermediate clip intact.
        if join>0:blend=min(blend,max(0,lengths[0]-.25-p['joins'][join-1]['seconds']))
        if join+1<len(p['joins']):blend=min(blend,max(0,lengths[1]-.25-p['joins'][join+1]['seconds']))
        short=min(short,blend)
    short=0 if short<1/44100 else short
    blend=0 if blend<1/44100 else blend
    strategies=[]
    if not mashup:
        strategies=[{'id':'short_handoff','seconds':short,'title':'利落交棒',
            'reason':'缩短两首同响的时间，降低跑拍、抢句和和声碰撞的暴露；保留当前选段、速度与音高。',
            'tradeoff':'过渡感较强，不等于无缝长叠搭；切点和尾音需试听。'}]
        if not risky:
            strategies.append({'id':'brief_blend','seconds':blend,'title':'短段顺接',
                'reason':'局部脉冲和调性候选未发现明显冲突，先做有限重叠，验证交接音量和律动。',
                'tradeoff':'候选相近不代表首拍、乐句和和弦正确；当前没有自动乐句对齐。'})
    recommended=None if mashup else 'short_handoff' if risky else 'brief_blend'
    result={'ok':True,'state':'analyzed_not_applied','scope':{'kind':tools.kind,'join_index':join,
        'a':{'start':probe['a']['start'],'end':probe['a']['end']} if inspectable else None,
        'b':{'start':probe['b']['start'],'end':probe['b']['end']} if inspectable else None},
        'findings':findings,'effective_bpm_candidates':bpms,'relative_tempo_gap':gap,'harmony':harmony,
        'boundary_candidates':boundaries,'local_hearing':local_hearing,
        'groove':groove,'drum_overlap':drum_overlap,'vocal_overlap':vocal_overlap,'local_harmony':local_harmony,'chord_evidence':chord_evidence,
        'strategies':strategies,'recommended':recommended,'knowledge_candidates':hits,'style_plan':style_plan,'audio_rendered':False,
        'next_steps':(['当前仍保留人声叠搭，不会擅自改成接歌。可校正拍网，选择不同人声/伴奏片段，或在你同意后试对速/移调。',
                      '若分轨尾音有金属感或残留，先对照独立分轨试听；RAG不能修复分轨缺失的信息。'] if mashup else
                     ['优先从中途出口/入口试听推荐接法；想要更长的叠搭，可继续校正拍网、确认人声角色并选择实际交接片段。',
                      '局部听感代理只负责缩小候选范围，不能替代对歌词、乐句、首拍和尾音的人工试听。']),
        'limitations':['24秒局部分析窗口，不识别曲风、歌词或真正小节首拍；候选不代表听过或听感验收。',
                       '和弦级证据使用局部三和弦模板；无法确认七和弦、转位、挂留、扩展音或完整编曲中的真实和声功能。',
                       '本轮新增的是四段局部频谱、能量、瞬态和立体声代理，未新增变速坡度、循环、动态EQ或鼓组重构。',
                       '热度只提供灵感先验；无法保证任意两首的长叠搭都自然或惊艳。']}
    tools.mix_diagnosis=(_signature(tools),copy.deepcopy(result))
    return result


def apply_mix_strategy(tools,args):
    cached=getattr(tools,'mix_diagnosis',None)
    if not cached or cached[0]!=_signature(tools):raise ValueError('请先检查当前版本的接法；选段或参数变了须重新分析')
    if not isinstance(args['preview'],bool):raise ValueError('试听选项须为布尔值')
    if args['preview'] and any(isinstance(c,dict) and c.get('action')=='separate' for c in tools.commands):
        raise ValueError('请等待分轨完成，再接续混音和试听')
    report=cached[1];strategy=args['strategy']
    if strategy=='recommended':strategy=report['recommended']
    chosen=next((v for v in report['strategies'] if v['id']==strategy),None)
    if chosen is None:raise ValueError('当前诊断不支持该接法；不会自动把人声叠搭改成接歌')
    p=copy.deepcopy(tools.project)
    if tools.kind=='set':p['joins'][p['preview_join']]={'kind':'smooth','seconds':chosen['seconds']}
    else:p['transition_seconds']=chosen['seconds']
    citations=','.join(dict.fromkeys(s['id'] for h in report['knowledge_candidates'] for s in h['sources']))
    reason=chosen['reason']+' '+chosen['tradeoff']+' 方法依据：'+citations+'（本地知识候选与本次信号检查；不是音频听感结论）。'
    answer=tools.commit(p,reason)
    if args['preview'] and 'preview' not in tools.commands:tools.commands.append('preview')
    answer.update(strategy=strategy,preview_queued=args['preview'],audio_rendered=False)
    return answer
