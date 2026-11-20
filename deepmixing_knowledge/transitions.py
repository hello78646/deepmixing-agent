"""Conditional alternatives for chat; labels never manufacture audio evidence."""
import copy
import json


class TransitionOptions:
    def __init__(self,library):
        self.data=json.loads((library.kb.root/'knowledge/transition-families.json').read_text('utf8'))
        if not isinstance(self.data,dict) or self.data.get('schema_version')!=1 or self.data.get('default_family') is not None:raise ValueError('invalid transition families')
        families=self.data.get('families')
        if not isinstance(families,list) or not families:raise ValueError('empty transition families')
        features=set(json.loads((library.kb.root/'knowledge/style-profiles.json').read_text('utf8'))['features'])
        def strings(value,empty=False):
            return isinstance(value,list) and (empty or bool(value)) and all(isinstance(s,str) and s.strip() for s in value)
        seen=set()
        for r in families:
            if not isinstance(r,dict) or not isinstance(r.get('id'),str) or not r['id']:raise ValueError('invalid transition family')
            if r['id'] in seen:raise ValueError('duplicate transition family')
            seen.add(r['id'])
            for field in ('techniques','source_ids'):
                if not strings(r.get(field)):raise ValueError('missing transition reference')
            if not set(r['techniques'])<=set(library.by_id) or not set(r['source_ids'])<=set(library.kb.sources):raise ValueError('unknown transition reference')
            for field in ('fit_features','avoid_features'):
                if not strings(r.get(field),empty=True) or not set(r[field])<=features:raise ValueError('invalid transition features')
            for field in ('requires','switch_when'):
                if not isinstance(r.get(field),list) or not r[field] or any(not isinstance(v,str) or not v for v in r[field]):raise ValueError('missing family conditions')
            for field in ('label','outgoing','incoming','automatic_scope'):
                if not isinstance(r.get(field),str) or not r[field]:raise ValueError('missing family explanation')

    def plan(self,features):
        features=set(features);rows=[]
        for family in self.data['families']:
            fit=features&set(family['fit_features']);avoid=features&set(family['avoid_features'])
            rows.append(dict(copy.deepcopy(family),state='excluded' if avoid else 'candidate_if_verified' if fit else 'conditional',
                matched_features=sorted(fit),excluded_by=sorted(avoid),missing_conditions=list(family['requires']),
                audio_processed=False,requires_listening=True))
        return sorted(rows,key=lambda r:(r['state']=='excluded',-len(r['matched_features']),r['id']))
