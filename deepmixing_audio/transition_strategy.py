"""Evidence-gated energy paths, not a learned musical-quality classifier.

The drum bridge borrows the natural outgoing tail exactly once. It does not
claim automatic clean-loop discovery, beat stretching, or full T61 layering.
"""
import re
import math

from .accompaniment_bridge import bridge_inputs,_stem_scale,_stem_gain
from .continuous import set_layout, validate_set
from .transition_refinement import _pulse,_guard_trim_candidates


def _wanted(prompt, *terms):
    # Negation is local to a clause, so “不要清唱，鼓点不断” keeps its intent.
    for clause in re.split(r'[，,。；;！!？?\n]',prompt.casefold()):
        for term in terms:
            for match in re.finditer(re.escape(term),clause):
                if not re.search(r'不要|不用|不需要|不想|别|禁止',clause[:match.start()]):return True
    return False


def select_bridge(prompt,report,features=()):
    rhythm=_wanted(prompt,'鼓点不断','持续律动','不要抽空','保持律动','鼓桥','运动','聚会')
    breath=_wanted(prompt,'人声桥','清唱桥','清唱呼吸','留白','抽空','清唱铺垫')
    # Explicit exclusions are constraints, not positive technique keywords.
    negative=r'(?:不要|不用|不需要|不想|不做|别(?:用)?|禁止)'
    no_drums=bool(re.search(negative+r'[^，,。；;]{0,4}(?:鼓桥|鼓轨|鼓组)',prompt))
    no_vocal=bool(re.search(negative+r'[^，,。；;]{0,4}(?:人声桥|清唱桥|清唱|抽空)',prompt))
    protected=bool(re.search(r'保持原貌|'+negative+r'[^，,。；;]{0,4}(?:分轨|铺垫过渡)',prompt))
    pa,pb=_pulse(report.get('a',{})),_pulse(report.get('b',{}))
    pulse=bool(pa and pb and abs(pb[1]/pa[1]-1)<=.025)
    known=set(features)
    for side in ('a','b'):known.update(report.get(side,{}).get('measured_features',[]))
    irregular=known&{'swung','broken_beat','rubato','tempo_drift','non_four_four'}
    damaged='degraded_stems' in known
    relation=report.get('comparison',{}).get('harmony',{}).get('relation')
    sparse='sparse_arrangement' in known and 'vocal_dense' not in known
    declines_layers=re.search(r'(?:不要|不用|不需要|不想|别|禁止)[^，,。；;！!？?\n]{0,4}(?:分层|渐进|逐层|鼓轨|鼓组|独立鼓)',prompt)
    incoming=set(features)|set(report.get('b',{}).get('measured_features',[]))
    progressive_requested=_wanted(prompt,'渐进','逐层','慢慢展开','后曲突兀','后首突兀','不要突进')
    progressive_fit=bool(incoming&{'short_intro','high_energy','harmonic_dense','bass_dense'})
    progressive=bool((progressive_requested or progressive_fit) and not (declines_layers or damaged or protected))
    if protected:choice,reason='fallback','要求保持原貌或不分轨，沿用原曲候选。'
    elif damaged:choice,reason='fallback','已知分轨有损伤，先比较原曲选段与尾音方案，不继续拆轨强做清唱或分层。'
    elif breath and not no_vocal and not rhythm:choice,reason='vocal_bridge','明确要求清唱呼吸，比较收束与渐入；分层需另有依据，不把这次认可变成通用配方。'
    elif irregular:
        if rhythm or no_vocal:choice,reason='fallback','已知复杂鼓型、自由节奏或拍号条件不足以支持当前原速鼓桥；保留原曲候选，不违背持续律动要求改成清唱。'
        else:choice,reason='vocal_bridge','已知律动或速度条件不适合当前固定原速鼓桥，比较分离节奏主角的呼吸路线；尚未自动改写鼓型。'
    elif sparse and relation in ('same','neighbor','relative') and not (rhythm or breath):
        choice,reason='fallback','稀疏素材与调性候选支持先比较原曲自然交棒，暂不强制拆成鼓桥或清唱；仍须核查实际同响与鼓型。'
    elif 'harmonic_dense' in known and not (rhythm or no_vocal):
        choice,reason='vocal_bridge','已知旋律和声密集，先撤竞争伴奏，用人声连接；并非BPM相近就把整套旋律长叠。'
    elif pulse and not no_drums:choice,reason='drum_bridge','局部脉冲接近，优先保留前曲真实鼓律动，后曲人声预告后再交伴奏。'
    elif not rhythm and not no_vocal:choice,reason='vocal_bridge','局部节奏不足以支持持续鼓桥，比较分离和声角色的清唱桥；不会声称已经对拍。'
    else:choice,reason='fallback','持续律动要求下证据不足或该路线被排除，不擅自改成清唱抽空。'
    return dict(preferred=choice,reason=reason,pulse_compatible=pulse,
                allow_vocal_fallback=not (rhythm or no_vocal or protected or damaged),
                allow_progressive=progressive,
                progressive_reason=('分轨受损，不采用独立分层' if damaged else
                    '用户明确拒绝独立分层' if declines_layers else
                    '原貌或分轨约束排除了改造' if protected else
                    '用户明确要求渐进展开' if progressive_requested else
                    '已知后曲/用户特征提示需要展开空间' if progressive_fit else
                    '没有独立分层依据，保留较简单候选'),
                known_features=sorted(known),rhythm_bridge_eligible=bool(pulse and not irregular and not damaged),
                missing_evidence=['完整鼓型相容性','已确认乐句','片段主唱和情绪的听感评价'],
                selection_scope='bounded_rules_not_musical_quality_prediction',
                inferred_genre=False,human_listening_status='pending')


def plan_drum_bridge(plan,tracks,join,outgoing_instrumental,outgoing_drums,incoming_vocal,report):
    p=bridge_inputs(plan,tracks,join,[(outgoing_instrumental,'instrumental',0),
                   (outgoing_drums,'drums',0),(incoming_vocal,'vocal',1)])
    separation=tracks[outgoing_instrumental].get('separation_id')
    if not separation or tracks[outgoing_drums].get('separation_id')!=separation:
        raise ValueError('伴奏与鼓轨须来自同一次分离，保证共同缩放与源时间一致')
    if abs(_stem_scale(tracks[outgoing_instrumental])-_stem_scale(tracks[outgoing_drums]))>1e-9:
        raise ValueError('同任务伴奏与鼓轨的共同缩放不一致，保留原曲候选')
    if join!=len(p['joins'])-1:raise ValueError('当前鼓桥先处理最后一处交接，避免改变后续手动编排')
    a,b=p['clips'][join:join+2]
    backing_gain=_stem_gain(a,tracks[outgoing_instrumental])
    drum_gain=_stem_gain(a,tracks[outgoing_drums])
    vocal_gain=_stem_gain(b,tracks[incoming_vocal])
    pa,pb=_pulse(report.get('a',{})),_pulse(report.get('b',{}))
    if pa is None or pb is None or abs(pb[1]/pa[1]-1)>.025:
        raise ValueError('局部脉冲不足以支持原速鼓桥，不能只凭歌曲BPM套用')
    # Preserve B's starting consonants. Pick a measured beat ~4 pulses into B
    # as the drum handoff, without truncating its first word to a grid line.
    incoming=[v for v in pb[0] if b['start']+1.5<=v<=min(b['start']+3,b['end']-.5)]
    outgoing=[v for v in pa[0] if a['end']-.25<=v<=a['end']]
    outgoing,guard=_guard_trim_candidates(outgoing,a['end'],report.get('a',{}),a,tracks[a['track_id']],True)
    if not outgoing:raise ValueError('拍点移动会裁掉未知或受保护唱词，保留前曲源边界与原曲候选')
    lyrics=report.get('a',{}).get('lyrics',{})
    if max(outgoing)<a['end']-1e-7:
        # A local word far from this tail is no evidence about unmeasured
        # vocals between the candidate beat and the original source endpoint.
        if not (isinstance(lyrics,dict) and
                all(isinstance(lyrics.get(k),(int,float)) and not isinstance(lyrics[k],bool) and
                    math.isfinite(lyrics[k]) for k in ('start','end')) and
                lyrics['start']<=max(outgoing) and lyrics['end']>=a['end']):
            raise ValueError('待裁尾部超出唱词分析覆盖，保留前曲源边界')
    if not incoming or not outgoing:raise ValueError('素材边界没有可用的节奏交棒位置')
    takeover=min(incoming,key=lambda v:abs(v-b['start']-4*pa[1]))-b['start']
    a['end']=max(outgoing)
    length=a['end']-a['start']
    previous=p['joins'][join-1]['seconds'] if join else 0
    if length-6-previous<.25:raise ValueError('铺垫不能覆盖前一交接')
    p['joins'][join]=dict(kind='smooth',seconds=takeover)
    layout=set_layout(p);boundary=layout['clips'][join]['end'];teaser=boundary-takeover
    a['gain_envelope']=[dict(time=0,gain=1),dict(time=length-6,gain=1),dict(time=length-5,gain=0)]
    # Both signals contain B's same vocal source-time; complementary linear
    # envelopes avoid duplicated vocal gain while the full backing takes over.
    rise_start=takeover-.03;rise_end=takeover+.30
    b['gain_envelope']=[dict(time=0,gain=0),dict(time=rise_start,gain=0),dict(time=rise_end,gain=1)]
    drum_length=takeover+.25
    backing_length=6-takeover
    def event(sid,start,end,time,gain,points):
        # Use the exact subtraction used by validation. Decimal beat times can
        # otherwise put a mathematically equal endpoint a few ulps past the cue.
        points[-1]['time']=end-start
        return dict(track_id=sid,start=start,end=end,time=time,gain_db=gain,semitones=0,gain_envelope=points)
    p['performance']=dict(events=[
        event(outgoing_instrumental,a['end']-6,a['end']-takeover,boundary-6,backing_gain,[
            dict(time=0,gain=0),dict(time=1,gain=1),dict(time=backing_length-.25,gain=1),dict(time=backing_length,gain=0)]),
        event(outgoing_drums,a['end']-drum_length,a['end'],teaser-.25,drum_gain,[
            dict(time=0,gain=0),dict(time=.25,gain=1),dict(time=drum_length-.03,gain=1),dict(time=drum_length,gain=0)]),
        event(incoming_vocal,b['start'],b['start']+rise_end,teaser,vocal_gain,[
            dict(time=0,gain=1),dict(time=rise_start,gain=1),dict(time=rise_end,gain=0)])
    ],drums=[])
    p=validate_set(p,tracks)
    return dict(project=p,name='鼓律动延续 · 人声预告 · 伴奏接管',techniques=['T59','T61','T37','T02'],
        reasons=['先将前曲从整轨退至真实伴奏，再退掉旋律与贝斯，仅保留前曲自己的真实鼓尾段。',
            '后曲人声在鼓轨上先出现，鼓桥不清唱抽空；局部脉冲接近时，在实测拍点附近让新伴奏接管。',
            '鼓段取自前曲同一源时间且只播放一次，没有声称已自动发现无缝循环；后曲人声与整轨按同一源时间互补交接。',
            '分轨按保存时的共同缩放补偿，避免主唱、伴奏或鼓在切换到同位置原曲时因保存降幅出现音量跳变。',
            '这是分层接管的局部实现，尚未确认音乐乐句、歌词语义或完整鼓型相容性；试听仍可返工。'],
        evidence=dict(join=join,method='drums_under_vocal_teaser',preparation_start=boundary-6,
            vocal_teaser_start=teaser,drum_handoff=boundary,backing_rebuild_end=boundary+.30,
            solo_vocal_seconds=0,loop_used=False,confirmed_phrase=False,vocal_source_continuity=True,
            outgoing_boundary_guard=guard,shared_scale_compensated=True,
            stem_gains=[dict(track_id=sid,scale=_stem_scale(tracks[sid]),gain_db=gain)
                        for sid,gain in ((outgoing_instrumental,backing_gain),(outgoing_drums,drum_gain),(incoming_vocal,vocal_gain))],
            pulse_periods=[pa[1],pb[1]],source_roles=[dict(track_id=sid,role=role,source_track_id=tracks[sid]['source_track_id'])
                for sid,role in [(outgoing_instrumental,'instrumental'),(outgoing_drums,'drums'),(incoming_vocal,'vocal')]]))
