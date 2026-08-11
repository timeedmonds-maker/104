#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    years=[]; cases=[]
    for p in sorted(a.root.rglob('exception_diagnostic.json')):
        d=json.loads(p.read_text(encoding='utf-8'))
        years.append(int(d['year']))
        for row in d.get('cases',[]):
            cases.append({'year':int(d['year']),**row})
    status=Counter(c.get('current_status','UNKNOWN') for c in cases)
    failures=[c for c in cases if c.get('current_status')=='FAIL']
    passes=[c for c in cases if c.get('current_status')=='PASS']
    source=[c for c in cases if 'SOURCE_MISSING' in c.get('current_status','')]
    failure_signatures=Counter()
    for c in failures:
        err=c.get('current_error','')
        if 'unresolved starters' in err or 'unresolved carried starters' in err: key='starter_unresolved'
        elif 'substitution outgoing player absent' in err: key='sub_out_absent'
        elif 'lineup size' in err or 'ten-player' in err: key='lineup_size'
        else: key='other'
        failure_signatures[key]+=1
    payload={
        'years_found':sorted(set(years)),
        'year_count':len(set(years)),
        'case_count':len(cases),
        'status_counts':dict(status),
        'failure_signature_counts':dict(failure_signatures),
        'passed_games':[{'year':c['year'],'game_id':c['game_id'],'repairs':c.get('repairs',[])} for c in passes],
        'source_missing_games':[{'year':c['year'],'game_id':c['game_id'],'status':c.get('current_status'),'v3_rows':c.get('v3_rows'),'first_sweep_error':c.get('first_sweep_error')} for c in source],
        'remaining_failures':failures,
        'all_cases':cases,
    }
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(payload,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in payload.items() if k not in {'remaining_failures','all_cases','passed_games'}},indent=2))
    print('PASSED_GAMES',[(x['year'],x['game_id']) for x in payload['passed_games']])
    for c in failures:
        print('FAIL',c['year'],c['game_id'],c.get('current_error'))
    return 0
if __name__=='__main__': raise SystemExit(main())
