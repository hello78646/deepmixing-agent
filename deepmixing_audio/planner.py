"""An explicit rules agent: measured inputs + limited intents + cited candidates."""
import copy

from deepmixing_knowledge import KnowledgeBase
from .models import validate_project


INTENTS={'柔和':['温柔','舒缓','平静','柔和'], '动感':['燃','动感','有力','运动'],
         '怀旧':['怀旧','回忆'], '原貌':['保持原貌'], '人声不变调':['不变调','不要变调']}


def recommend(tracks, a_id, b_id, mode, prompt, a_role='full_mix', b_role='full_mix', *, arrangement='together', cue=None, styles=None, features=None):
    if not isinstance(prompt,str) or len(prompt)>2000:
        raise ValueError('灵感提示须为不超过2000字的文字')
    if a_id not in tracks or b_id not in tracks:
        raise ValueError('请先选择两份已上传素材')
    if arrangement not in ('together','acappella_drop'):
        raise ValueError('不支持的开场方式')
    drop=arrangement=='acappella_drop'
    if drop and (mode!='mashup' or a_role!='vocal' or b_role!='instrumental'):
        raise ValueError('清唱后切入需要选择叠搭、A为独立人声、B为伴奏')
    intents=[name for name,words in INTENTS.items() if any(word in prompt for word in words)]
    a,b=tracks[a_id],tracks[b_id]
    def slot(track,role):
        return {'track_id':track['id'],'role':role,'start':0,'end':track['duration'],
                'bpm':track['analysis']['bpm_estimate'],'gain_db':-6,'bass_db':0,
                'semitones':0,'preserve_original':'原貌' in intents}
    left,right=slot(a,a_role),slot(b,b_role)
    # An estimate (including a half/double-time ambiguity) is not consent to
    # stretch. Explicit calibrated tempo controls remain available afterwards.
    target=None
    length=min((s['end']-s['start'])/(target/s['bpm'] if target else 1) for s in (left,right))
    base={'version':1,'mode':mode,'a':left,'b':right,'target_bpm':target,
          'transition_seconds':round(min(8,length*.3),4),'b_offset_seconds':0,'echo':0,
          'prompt':prompt,'title':'我的混音作品'}
    variants=[('smooth','顺滑交接','保持连贯，留出较长的交接空间。'),
              ('direct','利落切入','缩短重叠，减少两首人声同时出现的时间。'),
              ('space','空间尾音','增加轻回声，听完再决定是否保留。')]
    if mode=='mashup':
        variants=[('smooth','人声主导','降低伴奏音量，让已有独立人声更突出。'),
                  ('direct','律动主导','提高伴奏的相对存在感，检查是否遮盖歌词。'),
                  ('space','空间叠搭','在现有人声与伴奏上试听轻回声。')]
    if drop:
        if not cue:
            raise ValueError('请先完成这两份素材的旋律切入分析')
        measured=cue['status']=='measured_rise'
        variants=[('recommended','旋律上行切入' if measured else '手动切入起点','先留下清唱空间，再让伴奏进入。'),
                  ('earlier','早点加入律动','少留一点清唱，比较节奏是否更自然。'),
                  ('later','多留一点清唱','延后伴奏进入，比较期待感与空白是否合适。')]
    from .style_execution import plan_style,apply_style
    style_plan=plan_style(mode,styles,features)
    candidates=[]
    for key,title,reason in variants:
        project=copy.deepcopy(base)
        if key=='direct':
            project['transition_seconds']=min(.12,length*.05)
            if mode=='mashup':
                project['b']['gain_db']=-3
        elif key=='space':
            project['echo']=0 if '原貌' in intents else .14
        elif mode=='mashup':
            project['b']['gain_db']=-9
        if '动感' in intents and key=='direct' and '原貌' not in intents:
            project['b']['bass_db']=2
        if drop:
            step=cue.get('pulse_interval_seconds') or .5
            offset=cue['entry_seconds']+(-step if key=='earlier' else step if key=='later' else 0)
            project['b_offset_seconds']=round(max(.1,min(offset,left['end']-.1,60)),6)
            project['b']['start']=cue['b_start_seconds']
            project['a']['gain_db']=-4.5
            project['b']['gain_db']=-8.5
        explanations=[
                '速度依据：'+(f'以A的估计{target} BPM为目标；两轨脉冲估计均需核实。' if target else '保持原速；没有用未经确认的BPM强行变速。'),
                '默认不移调。拍点、乐句及和声未自动检测，须通过选段和试听确认。',
                '该方案只有在你选择并请求试听后才执行，不修改原始素材。']
        if drop:
            seconds=project['b_offset_seconds']
            basis=f"开头24秒内检测到约{cue['rise_semitones']}半音的旋律上行；推荐中心为{cue['entry_seconds']:.2f}秒。" if measured else '未检出可靠旋律上行；这是可修改的手动试听起点，不是识别到高潮。'
            explanations=[basis,f"本方案前{seconds:.2f}秒保留清唱，伴奏从自身第{cue['b_start_seconds']:.2f}秒切入。",
                '三版保持原速、原调；可调整切点、选段和音量，重新试听，也可撤销。',
                *cue.get('warnings',[]),'方案只在你选择并请求试听后执行，原素材保留。']
        base_project=validate_project(project,tracks)
        project,application=apply_style(base_project,tracks,style_plan)
        explanations.extend(dict.fromkeys(v['reason'] for v in application['applied']))
        candidates.append({'id':key,'name':title,'reason':reason,
            'project':project,'base_project':base_project,'style_application':application,
            'explanations':explanations,
            'evidence_status':('measured_candidate' if measured else 'manual_start') if drop else 'rules_start'})
    query='人声整套伴奏替换 叠搭 调性' if mode=='mashup' else '乐句 接歌 低频交换 回声'
    context=KnowledgeBase().agent_context(query,k=4)
    return {'planner':'rules_and_measured_audio','audio_processed':False,'llm_connected':False,
            'recognized_intents':intents,'requires_manual_interpretation':bool(prompt) and not intents,
            'arrangement':arrangement,'cue_analysis':copy.deepcopy(cue) if drop else None,
            'limitations':['本版只识别柔和、动感、怀旧、保持原貌及不变调等少量关键词；其余灵感保留为备注，需手动调整。',
                '怀旧仅记录偏好，没有自动选曲、语义情绪识别、歌词分析或通用对话能力。',
                '知识条目为待核验候选，不是专家对本次素材的试听结论。'],
            'style_plan':style_plan,'knowledge_candidates':context['recommendations'],'candidates':candidates}
