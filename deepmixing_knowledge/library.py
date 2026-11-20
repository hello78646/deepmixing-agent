"""Validated, source-backed DJ recipes layered over the existing sparse RAG.

This module is read-only: recipe steps are descriptive data, never tool calls.
Capability availability describes primitives, not a guarantee for user material.
"""
import copy
from collections import Counter
from datetime import date
import hashlib
import json

from .retrieval import KnowledgeBase,_tokens,EXPANSIONS

EVIDENCE_TYPES={'professional_tutorial','public_tutorial','peer_reviewed_study',
                'research_correction','official_platform_guidance',
                'expert_public_commentary','artist_setup_commentary'}
STATUSES={'supported','partial','planned'}


def _strings(value):
    return isinstance(value,list) and bool(value) and all(isinstance(s,str) and s.strip() for s in value)


class TechniqueLibrary:
    def __init__(self,root=None,*,kb=None,entries=None,taxonomy=None):
        self.kb=kb if kb is not None else KnowledgeBase(root)
        data=self.kb.root/'knowledge'
        self.entries=copy.deepcopy(entries) if entries is not None else json.loads((data/'techniques.json').read_text(encoding='utf-8'))
        self.taxonomy=copy.deepcopy(taxonomy) if taxonomy is not None else json.loads((data/'taxonomy.json').read_text(encoding='utf-8'))
        self.cards={c['id']:c for c in self.kb.cards}
        self._validate()
        self.by_id={e['id']:e for e in self.entries}
        payload=json.dumps(dict(corpus_hash=self.kb.corpus_hash,entries=self.entries,taxonomy=self.taxonomy),ensure_ascii=False,sort_keys=True)
        self.library_hash=hashlib.sha256(payload.encode()).hexdigest()

    def _validate(self):
        tx=self.taxonomy
        if not isinstance(tx,dict) or tx.get('schema_version')!=1:
            raise ValueError('unsupported library taxonomy version')
        for key in ('categories','styles','capabilities'):
            if not isinstance(tx.get(key),dict) or not tx[key]:raise ValueError('missing taxonomy '+key)
        for name,cap in tx['capabilities'].items():
            if not isinstance(cap,dict) or type(cap.get('available')) is not bool or not isinstance(cap.get('tools'),list):
                raise ValueError('invalid capability '+name)
            if not isinstance(cap.get('extent'),str) or not cap['extent'].strip():raise ValueError('missing capability extent '+name)
            if cap['available'] and not _strings(cap['tools']):raise ValueError('available capability needs tools '+name)
            if not cap['available'] and cap['tools']:raise ValueError('planned capability cannot have executable tools '+name)
        if not isinstance(self.entries,list) or not self.entries:raise ValueError('empty technique library')
        seen=set()
        for e in self.entries:
            if not isinstance(e,dict) or not isinstance(e.get('id'),str):raise ValueError('invalid technique record')
            cid=e['id'];card=self.cards.get(cid)
            if cid in seen:raise ValueError('duplicate technique '+cid)
            seen.add(cid)
            if not card:raise ValueError('missing technique card '+cid)
            if e.get('schema_version')!=1:raise ValueError('unsupported technique version '+cid)
            try:date.fromisoformat(e['reviewed_on'])
            except (KeyError,TypeError,ValueError):raise ValueError('invalid review date '+cid) from None
            if e.get('category') not in tx['categories']:raise ValueError('invalid category '+cid)
            for field in ('styles','prerequisites','steps','required_capabilities'):
                if not _strings(e.get(field)):raise ValueError('missing '+field+' on '+cid)
            if set(e['styles'])-set(tx['styles']):raise ValueError('invalid styles '+cid)
            if set(e['required_capabilities'])-set(tx['capabilities']):raise ValueError('unknown capability '+cid)
            for field in ('when_to_use','avoid_or_verify','checks'):
                if not _strings(card.get(field)):raise ValueError('incomplete card '+field+' on '+cid)
            if not isinstance(e.get('current_alternative'),str) or not e['current_alternative'].strip():
                raise ValueError('missing current alternative '+cid)
            if not isinstance(e.get('parameters'),list) or not e['parameters']:raise ValueError('missing parameters '+cid)
            for p in e['parameters']:
                if not isinstance(p,dict) or not all(isinstance(p.get(k),str) and p[k].strip() for k in ('name','guidance')):
                    raise ValueError('incomplete parameter '+cid)
            if not isinstance(e.get('evidence'),list) or not e['evidence']:raise ValueError('missing evidence '+cid)
            for ev in e['evidence']:
                if not isinstance(ev,dict):raise ValueError('invalid evidence '+cid)
                source=self.kb.sources.get(ev.get('source_id'))
                if not source or ev['source_id'] not in card['source_ids'] or source.get('access')!='read' or source.get('type') not in EVIDENCE_TYPES:
                    raise ValueError('unverified technique evidence '+cid)
                if not isinstance(ev.get('locator'),str) or not ev['locator'].strip() or ev.get('basis') not in ('public_teaching','design_adaptation','research_informed'):
                    raise ValueError('missing evidence basis/locator '+cid)

    def _execution(self,e):
        caps=self.taxonomy['capabilities']
        available=[name for name in e['required_capabilities'] if caps[name]['available']]
        missing=[name for name in e['required_capabilities'] if not caps[name]['available']]
        status='supported' if not missing else 'partial' if available else 'planned'
        return dict(status=status,available=available,missing=missing,full_recipe_supported=not missing,
          meaning='仅表示配方需要的控制是否具备，不代表本次素材已验证或音频已处理；需核查原貌锁、授权与真实工具结果。')

    def detail(self,technique_id):
        if not isinstance(technique_id,str) or technique_id not in self.by_id:raise ValueError('未收录该技巧ID')
        e=self.by_id[technique_id];card=self.cards[technique_id]
        return copy.deepcopy({**e,'card':card,'sources':[self.kb.sources[s] for s in card['source_ids']],
          'execution':self._execution(e),
          'capability_details':{c:self.taxonomy['capabilities'][c] for c in e['required_capabilities']},
          'library_hash':self.library_hash,'requires_applicability_review':True,'audio_processed':False})

    def _select(self,category,style,status):
        for name,value,choices in [('category',category,self.taxonomy['categories']),('style',style,self.taxonomy['styles']),('status',status,STATUSES)]:
            if value is not None and (not isinstance(value,str) or value not in choices):raise ValueError('unknown library '+name)
        return [e for e in self.entries if (category is None or e['category']==category)
                and (style is None or style in e['styles'] or 'any' in e['styles'])
                and (status is None or self._execution(e)['status']==status)]

    def search(self,query,k=3,*,category=None,style=None,status=None):
        selected=self._select(category,style,status)
        hits=self.kb.retrieve(query,k=k,card_ids={e['id'] for e in selected})
        results=[]
        for hit in hits:
            # An incidental word in a warning is not evidence that this recipe
            # teaches the requested operation. Prefer primary topic support.
            support=hit['lexical_support']['matched_query_terms']
            primary=set(_tokens(hit['card']['title']+' '+' '.join(hit['card']['tags'])))
            if support<2 and not (set(_tokens(query))&primary) and not any(p in query.casefold() for p in EXPANSIONS):continue
            e=self.by_id[hit['card']['id']]
            results.append({**hit,'technique':copy.deepcopy({**e,'execution':self._execution(e),
                'requires_applicability_review':True,'audio_processed':False})})
        return results

    def catalog(self,*,category=None,style=None,status=None):
        return [dict(id=e['id'],title=self.cards[e['id']]['title'],category=e['category'],styles=list(e['styles']),
                     execution=self._execution(e),source_ids=list(self.cards[e['id']]['source_ids']))
                for e in self._select(category,style,status)]

    def stats(self):
        return dict(techniques=len(self.entries),knowledge_cards=len(self.kb.cards),
                    supporting_records=len(self.kb.cards)-len(self.entries),
                    categories=dict(Counter(e['category'] for e in self.entries)),
                    execution=dict(Counter(self._execution(e)['status'] for e in self.entries)),
                    version=self.taxonomy['version'],library_hash=self.library_hash)
