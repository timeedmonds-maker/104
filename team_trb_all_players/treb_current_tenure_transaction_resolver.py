#!/usr/bin/env python3
"""Resolve current NO_EXACT_TENURE_IDENTITY rows from retained exact transaction state + exact team schedule.
Fail-closed diagnostic only; never mutates production.
"""
from __future__ import annotations
import argparse,csv,gzip,json,re
from pathlib import Path
from collections import defaultdict
from datetime import datetime,date

PID_KEYS=('player_id','person_id','personid','playerid')
TID_KEYS=('team_id','teamid')
GID_KEYS=('game_id','gameid')
SEA_KEYS=('season','season_year')
DATE_KEYS=('game_date','date','game_date_est','game_date_utc')

def norm(x): return str(x or '').strip()
def col(cols,keys):
    low={str(c).lower():c for c in cols}
    return next((low[k] for k in keys if k in low),None)
def pdate(s):
    s=norm(s)
    if not s:return None
    for f in ('%Y-%m-%d','%Y/%m/%d','%m/%d/%Y','%B %d, %Y','%b %d, %Y','%Y-%m-%dT%H:%M:%S','%Y-%m-%dT%H:%M:%SZ'):
        try:return datetime.strptime(s[:19] if 'T' in f else s,f).date()
        except:pass
    m=re.match(r'(\d{4}-\d{2}-\d{2})',s)
    if m:
        try:return datetime.strptime(m.group(1),'%Y-%m-%d').date()
        except:pass
    return None

def load_targets(p):
    out=[]
    for r in csv.DictReader(Path(p).open(encoding='utf-8-sig')):
        try: exp=int(float(r.get('expected_team_games') or 0))
        except: exp=0
        out.append({'season':norm(r['season']),'player_id':norm(r['player_id']),'team_id':norm(r['team_id']),'expected':exp})
    return out

def schedule_sources(root,targets):
    wanted={(t['season'],t['team_id']) for t in targets}; per=defaultdict(dict)
    for p in root.rglob('*.csv'):
        ps=str(p).lower()
        if 'treb_recovery_status' in ps or p.stat().st_size>500_000_000: continue
        try:
            with p.open(encoding='utf-8-sig',errors='replace',newline='') as f:
                rd=csv.DictReader(f); cols=rd.fieldnames or []
                tc=col(cols,TID_KEYS); gc=col(cols,GID_KEYS); sc=col(cols,SEA_KEYS); dc=col(cols,DATE_KEYS)
                if not (tc and gc and sc and dc): continue
                tmp=defaultdict(dict)
                for r in rd:
                    k=(norm(r.get(sc)),norm(r.get(tc)))
                    if k not in wanted: continue
                    gid=norm(r.get(gc)); d=pdate(r.get(dc))
                    if gid and d: tmp[k][gid]=d
                rel=str(p.relative_to(root))
                for k,v in tmp.items():
                    if v: per[k][rel]=v
        except Exception: pass
    return per

def tx_events(root,wanted_ids):
    path=root/'team_trb_all_players/impact_database/roster_tenure/normalized_transactions.jsonl.gz'
    out=defaultdict(list)
    if not path.exists(): return out
    with gzip.open(path,'rt',encoding='utf-8',errors='replace') as f:
        for line in f:
            try:o=json.loads(line)
            except:continue
            pid=norm(o.get('player_id'))
            if pid not in wanted_ids: continue
            d=pdate(o.get('exact_date'))
            if not d: continue
            try: src=norm(int(o['source_team_id'])) if o.get('source_team_id') is not None else ''
            except: src=norm(o.get('source_team_id'))
            try: dst=norm(int(o['destination_team_id'])) if o.get('destination_team_id') is not None else ''
            except: dst=norm(o.get('destination_team_id'))
            out[pid].append({'date':d,'event_type':norm(o.get('event_type')).lower(),'src':src,'dst':dst,'confidence':norm(o.get('confidence')).lower(),'raw':norm(o.get('raw_text')),'system':norm(o.get('source_system'))})
    for pid in out: out[pid].sort(key=lambda x:x['date'])
    return out

def status_on(events,tid,d):
    state=None; proof=[]
    for e in events:
        if e['date']>d: break
        touches=e['src']==tid or e['dst']==tid
        if not touches: continue
        # Only retained high-confidence identity/team resolved records may change state.
        if e['confidence'] and e['confidence']!='high': continue
        if e['dst']==tid and e['src']!=tid:
            state=True; proof.append(e)
        if e['src']==tid and e['dst']!=tid:
            state=False; proof.append(e)
    return state,proof

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo-root',required=True); ap.add_argument('--targets',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
    root=Path(a.repo_root); od=Path(a.out_dir); od.mkdir(parents=True,exist_ok=True)
    tg=load_targets(a.targets); sched=schedule_sources(root,tg); tx=tx_events(root,{t['player_id'] for t in tg})
    rows=[]
    for t in tg:
        k=(t['season'],t['team_id']); candidates=[]
        for src,gmap in sched.get(k,{}).items():
            if len(gmap)==t['expected']: candidates.append((src,gmap))
        # Require exact agreement of game-id/date map among all exact-count schedule sources if >1.
        canonical=None; srcs=[]
        for src,g in candidates:
            sig=tuple(sorted((gid,d.isoformat()) for gid,d in g.items()))
            if canonical is None: canonical=sig; srcs=[src]
            elif sig==canonical: srcs.append(src)
        verdict='UNRESOLVED'; reason=''; accepted=[]; proof_events=[]
        if canonical is None:
            reason=f'no_retained_schedule_source_with_exact_{t["expected"]}_games'
        else:
            games=[(gid,pdate(ds)) for gid,ds in canonical]
            unknown=[]; outside=[]
            for gid,d in games:
                st,pf=status_on(tx.get(t['player_id'],[]),t['team_id'],d)
                if st is None: unknown.append(gid)
                elif not st: outside.append(gid)
                proof_events.extend(pf[-1:])
            if unknown:
                reason=f'transaction_state_unknown_on_{len(unknown)}_games'
            elif outside:
                reason=f'transaction_state_outside_team_on_{len(outside)}_games'
            else:
                verdict='EXACT_TRANSACTION_STATE_SCHEDULE_TENURE_IDENTITY'; accepted=[g for g,_ in games]; reason='high_confidence_transaction_state_in_team_for_every_exact_schedule_game'
        # dedup compact proof
        uniq=[]; seen=set()
        for e in proof_events:
            q=(e['date'].isoformat(),e['event_type'],e['src'],e['dst'],e['raw'])
            if q not in seen: seen.add(q); uniq.append(q)
        rows.append({**t,'schedule_sources':'|'.join(srcs),'schedule_games':len(canonical or []),'verdict':verdict,'accepted_game_ids':'|'.join(accepted),'reason':reason,'transaction_proof':json.dumps(uniq,ensure_ascii=False)})
    fields=list(rows[0]);
    with (od/'TREB_CURRENT_21_TRANSACTION_RESOLUTION.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    exact=sum(r['verdict'].startswith('EXACT_') for r in rows)
    summary={'targets':len(rows),'exact_resolved':exact,'unresolved':len(rows)-exact,'verdict_counts':dict(__import__('collections').Counter(r['verdict'] for r in rows)),'reasons':dict(__import__('collections').Counter(r['reason'] for r in rows))}
    (od/'TREB_CURRENT_21_TRANSACTION_RESOLUTION_SUMMARY.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
