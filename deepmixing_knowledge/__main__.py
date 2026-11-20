"""CLI for the independent knowledge module; no external requests are made."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from . import KnowledgeBase, TechniqueLibrary, threshold_status


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('stats')
    build=commands.add_parser('build-index')
    build.add_argument('--output',type=Path)
    query=commands.add_parser('query')
    query.add_argument('text')
    query.add_argument('--top-k',type=int,default=4)
    query.add_argument('--context',action='store_true')
    query.add_argument('--output',type=Path)
    query.add_argument('--techniques',action='store_true')
    library=commands.add_parser('library')
    library.add_argument('--output',type=Path)
    detail=commands.add_parser('technique')
    detail.add_argument('id')
    detail.add_argument('--output',type=Path)
    for command in (query,library):
        command.add_argument('--category')
        command.add_argument('--style')
        command.add_argument('--status')
    evaluate=commands.add_parser('evaluate')
    evaluate.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    try:
        kb=KnowledgeBase(args.root)
        if args.command=='stats':
            sources=list(kb.sources.values())
            videos=[s for s in sources if s['type']=='performance_case' and s['access']=='read']
            result={'cards':len(kb.cards),'external_sources_read':sum(s['access']=='read' for s in sources),
                'blocked_sources':sum(s['access']=='blocked' for s in sources),
                'source_records_total':len(sources),'card_types':dict(Counter(c['kind'] for c in kb.cards)),
                'videos_all_three_over_500k':sum(threshold_status(s.get('metrics',{}))=='pass' for s in videos),
                'videos_any_metric_over_500k':sum(threshold_status(s.get('metrics',{}),mode='any')=='pass' for s in videos),
                'videos_views_over_500k':sum(threshold_status(s.get('metrics',{}),metrics_to_check=('views',))=='pass' for s in videos),
                'video_metrics':[{'id':s['id'],'url':s['url'],'displayed_metrics':s.get('metrics',{}),
                    'observed_on':s['observed_on'],'all_three':threshold_status(s.get('metrics',{})),
                    'any_metric':threshold_status(s.get('metrics',{}),mode='any'),
                    'views':threshold_status(s.get('metrics',{}),metrics_to_check=('views',))} for s in videos],
                'corpus_hash':kb.corpus_hash,'scope':'local_knowledge_corpus_only; runtime connections are reported by the workbench'}
        elif args.command=='build-index':
            path=kb.write_index(args.output or kb.root/'knowledge/index.json')
            result={'path':str(path),'corpus_hash':kb.corpus_hash,'documents':len(kb.cards)}
        elif args.command=='library':
            lib=TechniqueLibrary(kb=kb)
            result={'stats':lib.stats(),'categories':lib.taxonomy['categories'],'styles':lib.taxonomy['styles'],
                    'techniques':lib.catalog(category=args.category,style=args.style,status=args.status)}
        elif args.command=='technique':
            result=TechniqueLibrary(kb=kb).detail(args.id)
        elif args.command=='query':
            if args.techniques or any((args.category,args.style,args.status)):
                if args.context:raise ValueError('--context is for the original general knowledge query; use --techniques for recipe details')
                result=TechniqueLibrary(kb=kb).search(args.text,k=args.top_k,category=args.category,style=args.style,status=args.status)
            else:
                result=kb.agent_context(args.text,k=args.top_k) if args.context else kb.retrieve(args.text,k=args.top_k)
        else:
            cases=json.loads((kb.root/'knowledge/evaluation-queries.json').read_text(encoding='utf-8'))
            rows=[]
            for case in cases:
                actual=[hit['card']['id'] for hit in kb.retrieve(case['query'],k=3)]
                passed=bool(set(actual).intersection(case['expected'])) if case['expected'] else not actual
                rows.append({**case,'retrieved':actual,'passed':passed})
            result={'cases':len(rows),'positive_cases':sum(bool(r['expected']) for r in rows),
                'positive_top3_hits':sum(r['passed'] and bool(r['expected']) for r in rows),
                'out_of_domain_cases':sum(not r['expected'] for r in rows),
                'out_of_domain_correct':sum(r['passed'] and not r['expected'] for r in rows),
                'corpus_hash':kb.corpus_hash,'results':rows,
                'limitation':'Small predeclared regression set, not an independent audience study, semantic benchmark or audio quality evaluation.'}
        output=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
        target=getattr(args,'output',None)
        if target and args.command!='build-index':
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(output,encoding='utf-8')
        if hasattr(sys.stdout,'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        print(output,end='')
        return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        parser.error(str(exc))


if __name__=='__main__':
    raise SystemExit(main())
