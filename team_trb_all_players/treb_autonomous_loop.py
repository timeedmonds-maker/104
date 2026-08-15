#!/usr/bin/env python3
from __future__ import annotations
import json, os, time, urllib.request, urllib.error
from pathlib import Path

REPO=os.environ['REPO']; TOKEN=os.environ['GH_TOKEN']; BRANCH=os.environ['BRANCH']
BASE=f'https://api.github.com/repos/{REPO}'
STATE=Path('team_trb_all_players/autonomous_supervisor_state.json')
LEDGER=Path('team_trb_all_players/autonomous_strategy_ledger.json')
REPORT=Path('/tmp/treb-loop-report.json')
MAX_SECONDS=45*60
POLL_SECONDS=60

STRATEGIES=[
 ('RETAINED_SOURCE_INVENTORY','treb-residual-retained-source-scan.yml'),
 ('2015_SINGLE_KEY_ISOLATION','treb-2015-single-key-isolation.yml'),
 ('2019_SOURCE_GAP_WIDE_INVENTORY','treb-2019-source-gap-wide-inventory.yml'),
 ('2009_SOURCE_GAP_CANARY','treb-2009-source-gap-canary.yml'),
 ('2015_NONUNIQUE_SPLIT','treb-2015-nonunique-split.yml'),
]

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

def all_runs():
    return api(f'/actions/runs?branch={BRANCH}&per_page=100').get('workflow_runs',[])

def active_non_controller(runs):
    out=[]
    for r in runs:
        name=(r.get('name') or '').lower(); path=(r.get('path') or '').lower()
        if ('treb' in name or 'treb' in path) and 'autonomous completion loop' not in name:
            if r.get('status') in ('queued','in_progress','waiting','pending'):
                out.append({'id':r['id'],'name':r.get('name'),'path':r.get('path'),'status':r.get('status')})
    return out

def latest_for_workflow(runs, workflow_file):
    target='/'+workflow_file.lower()
    matches=[r for r in runs if (r.get('path') or '').lower().endswith(target)]
    return max(matches,key=lambda r:r.get('created_at','')) if matches else None

def persist(state,ledger,report):
    save(STATE,state); save(LEDGER,ledger); REPORT.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')

state=load(STATE,{'generation':0,'resolved':8395,'unresolved':1252,'status':'ACTIVE','last_strategy':None})
ledger=load(LEDGER,{'strategies':[]})
started=time.time(); events=[]

while time.time()-started < MAX_SECONDS:
    runs=all_runs()
    active=active_non_controller(runs)

    # Reconcile launched ledger entries against authoritative workflow conclusions.
    for entry in ledger.get('strategies',[]):
        if entry.get('status')!='LAUNCHED':
            continue
        r=latest_for_workflow(runs,entry.get('workflow',''))
        if not r: continue
        if r.get('status')=='completed':
            entry['run_id']=r['id']; entry['conclusion']=r.get('conclusion'); entry['completed_at']=int(time.time())
            entry['status']='COMPLETE' if r.get('conclusion')=='success' else 'EXHAUSTED'
            events.append({'event':'STRATEGY_FINISHED','name':entry.get('name'),'run_id':r['id'],'conclusion':r.get('conclusion')})

    persist(state,ledger,{'time':int(time.time()),'events':events[-30:],'active_treb_runs':active,'state':state,'ledger':ledger})

    if active:
        events.append({'event':'WAIT_ACTIVE','runs':active})
        time.sleep(POLL_SECONDS)
        continue

    tried={x.get('name') for x in ledger.get('strategies',[]) if x.get('status') in ('LAUNCHED','COMPLETE','EXHAUSTED')}
    strategy=next(((n,w) for n,w in STRATEGIES if n not in tried),None)
    if not strategy:
        state['status']='NEEDS_NEW_STRATEGY_DESIGN'
        events.append({'event':'STRATEGY_QUEUE_EXHAUSTED'})
        persist(state,ledger,{'time':int(time.time()),'events':events[-30:],'active_treb_runs':[],'state':state,'ledger':ledger})
        break

    name,wf=strategy
    api(f'/actions/workflows/{wf}/dispatches','POST',{'ref':BRANCH})
    entry={'name':name,'workflow':wf,'status':'LAUNCHED','launched_at':int(time.time()),'generation':state.get('generation',0)+1}
    ledger.setdefault('strategies',[]).append(entry)
    state['generation']=state.get('generation',0)+1; state['last_strategy']=name; state['status']='ACTIVE'
    events.append({'event':'DISPATCHED','strategy':entry})
    persist(state,ledger,{'time':int(time.time()),'events':events[-30:],'active_treb_runs':[],'state':state,'ledger':ledger})
    time.sleep(15)

# If the finite controller window ends while work remains, enqueue the next controller generation.
if state.get('status')=='ACTIVE':
    try:
        api('/actions/workflows/treb-autonomous-completion-loop.yml/dispatches','POST',{'ref':BRANCH})
        events.append({'event':'SELF_REDISPATCHED'})
    except Exception as exc:
        events.append({'event':'SELF_REDISPATCH_FAILED','error':str(exc)})

persist(state,ledger,{'time':int(time.time()),'events':events[-50:],'state':state,'ledger':ledger})
print(json.dumps({'state':state,'events':events[-20:]},indent=2,sort_keys=True))
