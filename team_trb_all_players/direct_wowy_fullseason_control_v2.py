#!/usr/bin/env python3
import csv,gzip,json,math,pathlib,random,time,collections,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests

exact_dir=pathlib.Path(sys.argv[1]); out=pathlib.Path(sys.argv[2]); out.mkdir(parents=True,exist_ok=True)
gate=60.0; tol=1e-7
base=pathlib.Path('team_trb_all_players/impact_database')
targets_path=base/'roster_tenure_v2/player_team_season_targets.jsonl.gz'
controls=list(csv.DictReader(open(exact_dir/'TREB_CUMULATIVE_EXACT_PROMOTED.csv',newline='')))
def pid(x):
    s=str(x).strip(); return s[:-2] if s.endswith('.0') and s[:-2].isdigit() else s
def tid(x): return str(int(float(x)))
def key(r): return (str(r['season']),tid(r['team_id']),pid(r['player_id']))
def num(x):
    try:
        v=float(x); return v if math.isfinite(v) else None
    except:return None
ck={key(r):r for r in controls}
all_targets={}; team_max=collections.defaultdict(int)
with gzip.open(targets_path,'rt',encoding='utf-8') as f:
    for line in f:
        if not line.strip(): continue
        r=json.loads(line)
        try:k=key(r); n=int(float(r.get('team_games_in_tenure') or 0))
        except:continue
        team_max[(k[0],k[1])]=max(team_max[(k[0],k[1])],n)
        if k in ck: all_targets[k]=r
by_season=collections.defaultdict(list)
for k,r in all_targets.items():
    n=int(float(r.get('team_games_in_tenure') or 0))
    if n and n==team_max[(k[0],k[1])]: by_season[k[0]].append(k)
candidates=[]
for s in sorted(by_season): candidates.extend(sorted(by_season[s])[:4])
if len(candidates)<100:
    seen=set(candidates)
    for s in sorted(by_season):
        for k in sorted(by_season[s]):
            if k not in seen: candidates.append(k); seen.add(k)
            if len(candidates)>=100: break
        if len(candidates)>=100: break
candidates=candidates[:100]
print(json.dumps({'candidate_controls':len(candidates),'seasons':len({k[0] for k in candidates})}),flush=True)
url='https://api.pbpstats.com/get-wowy-stats/nba'; transient={429,500,502,503,504}
def mins(row):
    v=num(row.get('Minutes'))
    if v is not None:return v
    v=num(row.get('SecondsPlayed')); return None if v is None else v/60.0
def fetch(meta,typ,state):
    p={'Season':str(meta['season']),'SeasonType':'Regular Season','TeamId':tid(meta['team_id']),'Type':typ,
       ('0Exactly1OnFloor' if state=='on' else '0Exactly1OffFloor'):pid(meta['player_id'])}
    hist=[]
    for a in range(1,5):
        if a>1: time.sleep(min(8,2**(a-1))+random.random())
        try:
            rr=requests.get(url,params=p,timeout=35); hist.append(rr.status_code)
            if rr.status_code==200:
                obj=rr.json(); row=obj.get('single_row_table_data') or {}
                if isinstance(row,dict) and row:return row
                hist.append('EMPTY:'+','.join(sorted(obj.keys())[:10]))
            if rr.status_code not in transient:break
        except Exception as e:hist.append(type(e).__name__+':'+str(e)[:120])
    raise RuntimeError(f'{typ}/{state} failed {hist}')
def calc(rows):
    ans=[]
    for state in ('on','off'):
        t=rows['team_'+state]; o=rows['opponent_'+state]
        vals=[num(t.get('OffRebounds')),num(t.get('DefRebounds')),num(o.get('OffRebounds')),num(o.get('DefRebounds'))]
        if any(v is None or v<0 or abs(v-round(v))>1e-9 for v in vals): raise RuntimeError(f'nonexact rebound counts {state} {vals}')
        tr=vals[0]+vals[1]; ore=vals[2]+vals[3]
        if tr+ore<=0:raise RuntimeError('zero denominator')
        ans.append(100.0*tr/(tr+ore))
    return ans[0],ans[1],ans[0]-ans[1]
results=[]
for n,k in enumerate(candidates,1):
    meta=all_targets[k]; ref=ck[k]; rec={'season':k[0],'team_id':k[1],'player_id':k[2]}
    try:
        req=[('Team','on'),('Team','off'),('Opponent','on'),('Opponent','off')]; rows={}
        with ThreadPoolExecutor(max_workers=4) as ex:
            fs={ex.submit(fetch,meta,*q):q for q in req}
            for fut in as_completed(fs):
                typ,state=fs[fut]; rows[typ.lower()+'_'+state]=fut.result()
        raw_on=mins(rows['team_on']); target_sec=num(meta.get('seconds_on'))
        if target_sec is None:
            tm=num(meta.get('minutes_on')); target_sec=None if tm is None else tm*60
        if raw_on is None or target_sec is None:raise RuntimeError('missing minutes evidence')
        delta=raw_on*60-target_sec
        if abs(delta)>gate+1e-9:raise RuntimeError(f'minutes gate {delta}')
        on,off,sw=calc(rows)
        diffs=[abs(on-float(ref['on'])),abs(off-float(ref['off_corrected'])),abs(sw-float(ref['on_minus_off_corrected']))]
        rec.update(status='PASS' if max(diffs)<=tol else 'MISMATCH',raw_on=on,raw_off=off,raw_swing=sw,ref_on=float(ref['on']),ref_off=float(ref['off_corrected']),ref_swing=float(ref['on_minus_off_corrected']),max_abs_diff_pp=max(diffs),minutes_delta_seconds=delta)
    except Exception as e:rec.update(status='ERROR',error=repr(e))
    results.append(rec); print(json.dumps({'progress':f'{n}/{len(candidates)}',**rec}),flush=True)
passed=[r for r in results if r['status']=='PASS']; mm=[r for r in results if r['status']=='MISMATCH']; err=[r for r in results if r['status']=='ERROR']
seasons={r['season'] for r in passed}; maxdiff=max([float(r['max_abs_diff_pp']) for r in passed if r.get('max_abs_diff_pp') is not None] or [0])
qa={'status':'PASS' if len(passed)>=50 and len(seasons)>=10 and not mm else 'FAIL','controls_total':len(results),'controls_pass':len(passed),'control_mismatches':len(mm),'control_errors':len(err),'seasons_passed':len(seasons),'max_pass_diff_pp':maxdiff,'required_min_pass':50,'required_min_seasons':10,'zero_mismatches_required':True,'full_team_season_controls_only':True}
cols=sorted({c for r in results for c in r})
with open(out/'CONTROL_RESULTS.csv','w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); w.writerows(results)
(out/'CONTROL_GATE.json').write_text(json.dumps(qa,indent=2)+'\n')
print(json.dumps(qa,indent=2),flush=True)
