"""Conditional music checks: caller context prioritizes review, not audio facts."""
import copy
import json


def _texts(value):
    return isinstance(value,list) and bool(value) and all(isinstance(x,str) and x.strip() for x in value)


class MusicalContinuityReview:
    def __init__(self,library,*,data=None):
        self.library=library
        self.data=copy.deepcopy(data) if data is not None else json.loads(
            (library.kb.root/'knowledge/musical-continuity.json').read_text('utf8'))
        self._validate()

    def _validate(self):
        if not isinstance(self.data,dict) or self.data.get('schema_version')!=1:
            raise ValueError('invalid musical review schema')
        topics=self.data.get('topics')
        if not isinstance(topics,list) or not topics:raise ValueError('empty musical review topics')
        seen=set();branches=set()
        for topic in topics:
            if not isinstance(topic,dict) or not isinstance(topic.get('id'),str) or not topic['id'] or topic['id'] in seen:
                raise ValueError('invalid or duplicate musical review topic')
            seen.add(topic['id'])
            if not isinstance(topic.get('label'),str) or not topic['label'].strip():raise ValueError('missing review label')
            for key in ('symptoms','observations_needed','switch_when','techniques','source_ids'):
                if not _texts(topic.get(key)):raise ValueError('missing musical review '+key)
            for key in ('focus','features'):
                if not isinstance(topic.get(key),list) or any(not isinstance(x,str) for x in topic[key]):
                    raise ValueError('invalid musical review '+key)
            if set(topic['focus'])-{'vocal','melody','groove','balanced'}:raise ValueError('unknown review focus')
            if set(topic['techniques'])-self.library.by_id.keys():raise ValueError('unknown review technique')
            for sid in topic['source_ids']:
                source=self.library.kb.sources.get(sid)
                if not source or source.get('access')!='read':raise ValueError('unverified musical review source')
            if not isinstance(topic.get('alternatives'),list) or not topic['alternatives']:
                raise ValueError('empty musical alternatives')
            for branch in topic['alternatives']:
                if not isinstance(branch,dict) or not isinstance(branch.get('id'),str) or not branch['id'] or branch['id'] in branches:
                    raise ValueError('invalid or duplicate musical alternative')
                branches.add(branch['id'])
                if not isinstance(branch.get('label'),str) or not branch['label'].strip():raise ValueError('missing alternative label')
                if not _texts(branch.get('techniques')) or set(branch['techniques'])-self.library.by_id.keys():
                    raise ValueError('unknown alternative technique')
                if not _texts(branch.get('requires')):raise ValueError('missing alternative conditions')
                if any(type(branch.get(key)) is not bool for key in ('needs_breath','needs_stems')):
                    raise ValueError('invalid alternative requirements')

    def plan(self,features,brief,goal):
        features=set(features);focus=brief.get('focus');topics=[]
        for topic in self.data['topics']:
            row=copy.deepcopy(topic)
            matched=sorted(features&set(topic['features']))
            row['review_priority']=20*int(focus in topic['focus'])+5*len(matched)
            row['matched_caller_features']=matched
            row['methods']=[dict(id=tid,execution=self.library.detail(tid)['execution'],
                                current_alternative=self.library.by_id[tid]['current_alternative'])
                            for tid in topic['techniques']]
            for branch in row['alternatives']:
                excluded=[]
                if branch['needs_breath'] and brief.get('allow_breath') is False:
                    excluded.append('brief:allow_breath_false')
                if branch['needs_stems'] and 'degraded_stems' in features:
                    excluded.append('feature:degraded_stems')
                branch.update(state='excluded' if excluded else 'conditional',excluded_by=excluded)
            topics.append(row)
        topics.sort(key=lambda row:-row['review_priority'])
        return dict(status='not_evaluated',audio_verified=False,renderer_enforced=False,
            required=goal in ('transition','continuous','mashup'),caller_features=sorted(features),caller_focus=focus,
            evidence_origin='caller_supplied_not_audio_detection',topics=topics,
            case_feedback_source=self.data['feedback_source'],
            feedback_scope='本机历史对照未观察到明显改善；不代表当前用户偏好，也不能排除某个根因。',
            scope='条件化检查与方法索引，不选择接法、不确认音符/强拍/鼓件、不自动渲染。所需参数须由真实素材与当前工具能力核对。')
