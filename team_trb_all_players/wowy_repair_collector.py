from __future__ import annotations
import argparse, gzip, json, math, random, time
from datetime import datetime, timezone
from pathlib import Path
import requests

BASE=Path(__file__).resolve().parent
OLD=BASE/'impact_database'/'corrected_off'/'cache'
OUT=BASE/'impact_database'/'corrected_off_wowy'
CACHE=OUT/'cache'
SUMMARY=OUT/'wowy_collection_summary.json'
TRIGGER=OUT/'trigger.json'
URL='https://api.pbpstats.com/get-wowy-stats/nba'
TRANSIENT={429,500,502,503,504}

def now(): return datetime.now(timezone.utc).isoformat()
def read_gz(p):
    with gzip.open(p,'rt',encoding='utf-8') as f:return json.load(f)
def write_gz(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.tmp')
    with gzip.open(tmp,'wt',encoding='utf-8') as f: json.dump(obj,f,separators=(',',':'))
    tmp.replace(p)
def write_json(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True); tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,indent=2),encoding='utf-8'); tmp.replace(p)

def request_json(session, params, attempts=5, interval=.5):
    hist=[]; errs=[]; started=time.monotonic()
    for a in range(1,attempts+1):
        if a>1: time.sleep(min(2**(a-2),8)+random.random())
        elif interval: time.sleep(interval)
        try:
            r=session.get(URL,params=params,timeout=60)
            hist.append(r.status_code)
            if r.status_code==200:
                d=r.json()
                return d, {'ok':True,'attempt':a,'status_history':hist,'elapsed_seconds':round(time.monotonic()-started,3),'url':r.url}
            if r.status_code not in TRANSIENT:
                errs.append(f'HTTP {r.status_code}: {r.text[:300]!r}'); break
            errs.append(f'HTTP {r.status_code}: {r.text[:200]!r}')
        except Exception as e:
            errs.append(repr(e))
    return None, {'ok':False,'attempt':len(hist) or attempts,'status_history':hist,'elapsed_seconds':round(time.monotonic()-started,3),'errors':errs[-5:]}

def row_from(payload):
    if not isinstance(payload,dict): return {}
    x=payload.get('single_row_table_data')
    return x if isinstance(x,dict) else {}

def minutes(row):
    for k in ('Minutes','minutes'):
        try:
            v=float(row.get(k))
            if math.isfinite(v): return v
        except: pass
    for k in ('SecondsPlayed','seconds_played'):
        try:
            v=float(row.get(k))/60
            if math.isfinite(v): return v
        except: pass
    return 0.0

def cache_name(meta):
    return f"{meta['season']}__{meta['team_id']}__{meta['player_id']}__{meta['query_start_date']}__{meta['query_end_date']}.json.gz"

def query_window(session, meta, attempts, interval):
    common={'Season':str(meta['season']),'SeasonType':'Regular Season','TeamId':str(meta['team_id']),
            'FromDate':str(meta['query_start_date']),'ToDate':str(meta['query_end_date'])}
    rows={}; reqs={}
    for typ in ('Team','Opponent'):
        for state,param in (('on','0Exactly1OnFloor'),('off','0Exactly1OffFloor')):
            p={**common,'Type':typ,param:str(meta['player_id'])}
            payload, rm=request_json(session,p,attempts=attempts,interval=interval)
            reqs[f'{typ.lower()}_{state}']=rm
            if not rm['ok']: raise RuntimeError(f"{typ} {state} request failed: {rm}")
            rows[f'{typ.lower()}_{state}']=row_from(payload)
    ton=minutes(rows['team_on']); toff=minutes(rows['team_off']); total=ton+toff
    oon=minutes(rows['opponent_on']); ooff=minutes(rows['opponent_off']); ototal=oon+ooff
    games=int(meta.get('team_games_in_window') or 0)
    expected=games*48.0
    max_ok=max(60.0,games*65.0)
    min_ok=0.0 if games==0 else games*30.0
    plausible=(min_ok <= total <= max_ok) and (abs(total-ototal) <= max(3.0,0.03*max(total,ototal,1)))
    rebound_keys=sorted({k for r in rows.values() for k in r if 'rebound' in k.lower()})
    return {
      'complete': bool(plausible), 'method':'PBP Stats get-wowy-stats exact roster-date window',
      'season':meta['season'],'team_id':int(meta['team_id']),'team_abbr':meta.get('team_abbr'),
      'player_id':str(meta['player_id']),'player':meta.get('player'),
      'query_start_date':meta['query_start_date'],'query_end_date':meta['query_end_date'],
      'team_games_in_window':games,'minutes_on':ton,'minutes_off':toff,'total_team_minutes':total,
      'opponent_minutes_on':oon,'opponent_minutes_off':ooff,'total_opponent_minutes':ototal,
      'expected_regulation_team_minutes':expected,'plausibility_max_minutes':max_ok,
      'date_window_plausible':bool(plausible),'rebound_keys':rebound_keys,
      'rows':rows,'requests':reqs,'generated_utc':now()
    }

def targets(): return sorted(OLD.glob('*.json.gz'))
def completed_set(): return {p.name for p in CACHE.glob('*.json.gz') if p.is_file()}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--batch-size',type=int,default=25); ap.add_argument('--attempts',type=int,default=5); ap.add_argument('--interval',type=float,default=.5); ap.add_argument('--self-test',action='store_true'); a=ap.parse_args()
    if a.self_test:
        assert minutes({'Minutes':12})==12; assert minutes({'SecondsPlayed':120})==2
        print('WOWY_REPAIR_SELF_TEST=PASS'); return
    OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)
    all_t=targets(); done=completed_set(); pending=[p for p in all_t if p.name not in done]
    bog='2016-17__1610612739__101106__2017-03-03__2017-03-13.json.gz'
    pending.sort(key=lambda p:(0 if p.name==bog else 1,p.name))
    selected=pending[:a.batch_size]
    s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 TREB-WOWY-repair/1.0','Accept':'application/json'})
    ok=0; errs=[]; started=time.monotonic()
    for i,p in enumerate(selected,1):
        meta=read_gz(p); outp=CACHE/cache_name(meta)
        try:
            result=query_window(s,meta,a.attempts,a.interval)
            if not result['complete']:
                raise RuntimeError(f"date-window plausibility failed: total={result['total_team_minutes']} games={result['team_games_in_window']}")
            if p.name==bog:
                proof={k:result[k] for k in ('season','team_id','player_id','player','query_start_date','query_end_date','team_games_in_window','minutes_on','minutes_off','total_team_minutes','date_window_plausible','rebound_keys')}
                if not (200 <= result['total_team_minutes'] <= 400): raise RuntimeError(f'Bogut proof failed: {proof}')
                write_json(OUT/'wowy_bogut_date_proof.json',proof)
            write_gz(outp,result); ok+=1
            print(f'WOWY_REPAIR {i}/{len(selected)} OK {p.name} on={result["minutes_on"]:.1f} off={result["minutes_off"]:.1f} total={result["total_team_minutes"]:.1f} games={result["team_games_in_window"]}',flush=True)
        except Exception as e:
            errs.append({'file':p.name,'error':repr(e),'utc':now()}); print(f'WOWY_REPAIR ERROR {p.name}: {e!r}',flush=True)
    done2=completed_set(); remaining=len(all_t)-len(done2)
    summary={'generated_utc':now(),'total_targets':len(all_t),'complete_windows':len(done2),'remaining_windows':remaining,
             'batch_requested':len(selected),'batch_successes':ok,'batch_errors':len(errs),'batch_elapsed_seconds':round(time.monotonic()-started,3),
             'method':'get-wowy-stats with FromDate/ToDate; Team+Opponent ON/OFF','old_get_on_off_partial_cache_invalid':True,
             'errors':errs[-25:],'all_complete':remaining==0}
    write_json(SUMMARY,summary)
    write_json(TRIGGER,{'generated_utc':now(),'remaining_windows':remaining,'complete_windows':len(done2),'all_complete':remaining==0})
    print(json.dumps(summary,indent=2),flush=True)
    if errs: raise SystemExit(2)

if __name__=='__main__': main()
