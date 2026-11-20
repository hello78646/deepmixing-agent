"""Execute a small, auditable subset of style recipes on validated pair plans."""
import copy

from deepmixing_knowledge import TechniqueLibrary
from deepmixing_knowledge.styles import StyleTechniqueRouter
from .models import validate_project, durations


def plan_style(mode,styles=None,features=None):
    return StyleTechniqueRouter(TechniqueLibrary()).plan(
        ['open_format'] if styles is None else styles,
        features=features,goal='mashup' if mode=='mashup' else 'transition')


def apply_style(project,tracks,plan):
    """No inferred genre, lyric detection, time stretch, pitch shift or new trims.

    Recipes are data, never executable instructions. This fixed allowlist maps
    only a few reviewed controls, and reports the exact numeric edits made.
    Existing envelopes and source locks always win over a new suggestion.
    """
    p=validate_project(project,tracks)
    library=TechniqueLibrary()
    eligible={i for phase in plan['workflow'].values() for i in phase}
    eligible.update(c['id'] for c in plan['candidates'])
    eligible-={c['id'] for c in plan['excluded']}
    features=set(plan['features']);applied=[];skipped=[]
    def record(path,before,after,ids,reason):
        if before==after:return
        sources={s['id']:dict(id=s['id'],title=s['title'],url=s.get('url'))
                 for tid in ids for s in library.detail(tid)['sources']}
        applied.append(dict(path=path,before=copy.deepcopy(before),after=copy.deepcopy(after),
            technique_ids=ids,sources=list(sources.values()),reason=reason,
            scope='bounded_controls_only_not_full_recipe_validation'))
    la,lb,_=durations(p)
    overlap=min(la-p['b_offset_seconds'],lb)
    mashup=p['mode']=='mashup'
    real_stems=(tracks[p['a']['track_id']].get('stem_role')=='vocal' and
                tracks[p['b']['track_id']].get('stem_role')=='instrumental')
    if mashup and not real_stems:
        skipped.append('素材没有真实独立分轨角色证据；不执行人声让位编排。')
    if mashup and real_stems:
        for slot,length in [('a',la),('b',lb)]:
            c=p[slot]
            if c['preserve_original'] or 'gain_envelope' in c:
                skipped.append(slot.upper()+'保留原貌锁或已有音量编排。');continue
            tail='long_tail' in features and 'T51' in eligible
            entrance=slot=='b' and overlap>0 and 'T31' in eligible
            if not tail and not entrance:continue
            ids=(['T31'] if entrance else [])+(['T51'] if tail else [])
            retreat=.82 if 'vocal_dense' in features else .92
            points=[]
            # On very short clips, keep each knot at least 5ms apart and
            # reserve most of the excerpt for a sustained musical level.
            sustain=1
            if entrance:
                rise=min(.012,length*.04);settle=min(.32,length*.2)
                points=[dict(time=0,gain=0),dict(time=rise,gain=1),dict(time=settle,gain=retreat)]
                sustain=retreat
            else:points=[dict(time=0,gain=1)]
            fade=min((.25 if slot=='a' else 1.2) if tail else .45,length*.25)
            points.extend([dict(time=length-fade,gain=sustain),dict(time=length,gain=0)])
            c['gain_envelope']=points
            record(slot+'.gain_envelope',None,points,ids,
                   ('伴奏短斜坡进入后退让主唱；' if entrance else '')+
                   '仅对当前选段末端做平滑收尾；不识别词句，也不恢复已缺失尾音。')
    if not mashup and features.intersection({'tempo_drift','rubato','non_four_four'}):
        methods=[i for i in ('T33','T54','T55') if i in eligible]
        if methods:
            before=p['transition_seconds'];p['transition_seconds']=min(before,.12)
            record('transition_seconds',before,p['transition_seconds'],methods,
                   '根据明确提供的自由节拍/漂移特征缩短同响；不做弹性校拍，也不强行变速。')
    return validate_project(p,tracks),dict(applied=applied,skipped=skipped,
        routing_hash=plan['routing_hash'],library_hash=plan['library_hash'],audio_rendered=False,
        limitation='这里只执行列出的参数变化；拍号、词句、和弦及听感仍需复核，其他配方未自动执行。')
