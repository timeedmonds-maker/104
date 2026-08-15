#!/usr/bin/env python3
from __future__ import annotations
import json, os, time, urllib.request, urllib.error
from pathlib import Path

REPO=os.environ['REPO']; TOKEN=os.environ['GH_TOKEN']; BRANCH=os.environ['BRANCH']
BASE=f'https://api.github.com/repos/{REPO}'
STATE=Path('team_trb_all_players/autonomous_supervisor_state.json')
LEDGER=Path('team_trb_all_players/autonomous_strategy_ledger.json')
REPORT=Path('/tmp/treb-loop-report.json')

def api(path, method='GET', payload=None):
    data=None if payload is None else json.dumps(payload).encode()
    req=urllib.request.Request(BASE+path,data=data,method=method,headers={
        'Authorization':f'Bearer {TOKEN}','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'treb-autonomous-loop'})
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            raw=r.read(); return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body=e.read().decode(errors='replace')
        raise RuntimeError(f'GitHub API {method} {path} -> {e.code}: {body[:1000]}')

def load(path, default):
    try:return json.loads(path.read_text())
    except Exception:return default

def save(path,obj): path.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n')

state=load(STATE,{'generation':0,'resolved':8395,'unresolved':1252,'status':'ACTIVE','last_strategy':None})
ledger=load(LEDGER,{'strategies':[]})
runs=api(f'/actions/runs?branch={BRANCH}&per_page=50').get('workflow_runs',[])
active=[]
for r in runs:
    name=(r.get('name') or '').lower(); path=(r.get('path') or '').lower()
    if 'treb' in name or 'treb' in path:
        if r.get('status') in ('queued','in_progress','waiting','pending') and 'autonomous completion loop' not in name:
            active.append({'id':r['id'],'name':r.get('name'),'status':r.get('status')})
report={'time':int(time.time()),'state_before':state,'active_treb_runs':active,'action':None}
if active:
    report['action']='NO_INTERFERENCE_ACTIVE_RUN'
else:
    tried={x.get('name') for x in ledger.get('strategies',[]) if x.get('status') in ('LAUNCHED','COMPLETE','EXHAUSTED')}
    strategy=None
    # Highest-yield first. These are finite and may be replaced/extended by later commits without losing state.
    for name,wf in [
        ('RETAINED_SOURCE_INVENTORY','.github/workflows/treb-residual-retained-source-scan.yml'),
        ('2015_SINGLE_KEY_ISOLATION','.github/workflows/treb-2015-single-key-isolation.yml'),
        ('2019_SOURCE_GAP_WIDE_INVENTORY','.github/workflows/treb-2019-source-gap-wide-inventory.yml'),
        ('2009_SOURCE_GAP_CANARY','.github/workflows/treb-2009-source-gap-canary.yml'),
        ('2015_NONUNIQUE_SPLIT','.github/workflows/treb-2015-nonunique-split.yml')]:
        if name not in tried:
            strategy=(name,wf); break
    if strategy:
        name,wf=strategy
        api(f'/actions/workflows/{wf.split("/")[-1]}/dispatches','POST',{'ref':BRANCH})
        entry={'name':name,'workflow':wf,'status':'LAUNCHED','launched_at':int(time.time()),'generation':state.get('generation',0)+1}
        ledger.setdefault('strategies',[]).append(entry)
        state['generation']=state.get('generation',0)+1; state['last_strategy']=name
        report['action']='DISPATCHED'; report['strategy']=entry
    else:
        state['status']='NEEDS_NEW_STRATEGY_DESIGN'
        report['action']='STRATEGY_QUEUE_EXHAUSTED'
save(STATE,state); save(LEDGER,ledger); REPORT.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
print(json.dumps(report,indent=2,sort_keys=True))
