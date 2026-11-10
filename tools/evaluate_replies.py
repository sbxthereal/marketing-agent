"""Bounded quality evaluation. Input cases are never ingested into the live queue."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pilot.core import Store, load_config, load_catalog, validate_post
from pilot.gateway import Gateway, analyze
from pilot.workflow import shortlist


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cases',required=True,type=Path)
    parser.add_argument('--env-file',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    config=load_config(root,'config.browser.example.json');catalog=load_catalog(root,config)
    cases=json.loads(args.cases.read_text(encoding='utf-8-sig'))
    if len(cases)>12:raise SystemExit('At most 12 cases per explicit evaluation')
    gateway=Gateway(args.env_file);results=[]
    with tempfile.TemporaryDirectory() as tmp:
        store=Store(Path(tmp)/'evaluation.sqlite3')
        for case in cases:
            post=validate_post(case['post'],config);start=time.monotonic()
            result=analyze(gateway,post,shortlist(post,catalog),config,store)
            row={'label':case['label'],'synthetic':case.get('synthetic',False),'url':post['url'],
                 'title':post['title'],'expected':case['expected'],'result':result,
                 'decision_passed':bool(result and result['decision'] in case['expected']),
                 'seconds':round(time.monotonic()-start,2)}
            results.append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
        report={'mode':'Bounded evaluation: stored real discussions plus explicitly synthetic edge cases. No Reddit publication.',
                'model':config['model'],'results':results,'usage':store.summary()['usage'],
                'passed':sum(r['decision_passed'] for r in results),'total':len(results)}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'total':report['total'],'usage':report['usage']}))


if __name__=='__main__':main()
