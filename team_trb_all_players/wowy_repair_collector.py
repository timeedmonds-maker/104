from __future__ import annotations
import argparse, gzip, json, math, random, time
from datetime import datetime, timezone
from pathlib import Path
import requests

BASE=Path(__file__).resolve().parent
TARGET_FILE=BASE/'impact_database'/'roster_tenure_v2'/'wowy_partial_segments.jsonl.gz'
OUT=BASE/'impact_database'/'corrected_off_wowy'
CACHE=OUT/'cache'
SUMMARY=OUT/'wowy_collection_summary.json'
TRIGGER=OUT/'trigger.json'
URL='https://api.pbpstats.com/get-wowy-stats/nba'
TRANSIENT={429,500,502,503,504}

def now(): return datetime.now(timezone.utc).isoformat()
def write_gz(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.tmp')
    with gzip.open(tmp,'wt',encoding='utf-8') as f: json.dump(obj,f,separators=(',',':'))
    tmp.replace(p)
def write_json(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True); tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,indent=2),encoding='utf-8'); tmp.replace(p)
def read_targets():
    if not TARGET_FILE.exists(): raise RuntimeError(f'missing V2 target file: {TARGET_FILE}')
    out=[]
    with gzip.open(TARGET_FILE,'rt',encoding='utf-8') as f:
        for line in f:
            if line.strip(): out.append(json.loads(line))
    return out

def request_json(session, params, attempts=5, interval=.5):
    hist=[]; errs=[]; started=time.monotonic()
    for a in range(1,attempts+1):
        if a>1: time.sleep(min(2**(a-2),8)+random.random())
        elif interval: time.sleep(interval)
        try:
            r=session.get(URL,params=params,timeout=60)
            hist.append(r.status_code)
            if r.status_code==200:
                return r.json(), {'ok':True,'attempt':a,'status_history':hist,'elapsed_seconds':round(time.monotonic()-started,3),'url':r.url}
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
    needs_on=bool(meta.get('needs_on'))
    common={'Season':str(meta['season']),'SeasonType':'Regular Season','TeamId':str(meta['team_id']),
            'FromDate':str(meta['query_start_date']),'ToDate':str(meta['query_end_date'])}
    rows={}; reqs={}; states=[('off','0Exactly1OffFloor')]
    if needs_on: states.insert(0,('on','0Exactly1OnFloor'))
    for typ in ('Team','Opponent'):
        for state,param in states:
            p={**common,'Type':typ,param:str(meta['player_id'])}
            payload,rm=request_json(session,p,attempts=attempts,interval=interval)
            reqs[f'{typ.lower()}_{state}']=rm
            if not rm['ok']: raise RuntimeError(f"{typ} {state} request failed: {rm}")
            rows[f'{typ.lower()}_{state}']=row_from(payload)
    old_on=float(meta.get('minutes_on') or 0)
    ton=minutes(rows.get('team_on',{})) if needs_on else old_on
    toff=minutes(rows['team_off']); total=ton+toff
    oon=minutes(rows.get('opponent_on',{})) if needs_on else old_on
    ooff=minutes(rows['opponent_off']); ototal=oon+ooff
    games=int(meta.get('team_games_in_window') or 0)
    max_ok=max(65.0,games*65.0)
    min_ok=0.0 if games==0 else games*25.0
    plausible=(min_ok <= total <= max_ok) and (abs(toff-ooff) <= max(3.0,0.03*max(toff,ooff,1)))
    rebound_keys=sorted({k for r in rows.values() for k in r if 'rebound' in k.lower()})
    return {
      'complete':bool(plausible),'method':'PBP Stats get-wowy-stats exact V2 roster-date window',
      'roster_target_version':'v2','season':meta['season'],'team_id':int(meta['team_id']),'team_abbr':meta.get('team_abbr'),
      'player_id':str(meta['player_id']),'player':meta.get('player'),'segment_index':int(meta.get('segment_index') or 1),
      'segment_count':int(meta.get('segment_count') or 1),'query_start_date':meta['query_start_date'],'query_end_date':meta['query_end_date'],
      'team_games_in_window':games,'minutes_on':ton,'minutes_off':toff,'total_team_minutes':total,
      'opponent_minutes_on':oon,'opponent_minutes_off':ooff,'total_opponent_minutes':ototal,
      'on_query_mode':'exact_date_wowy' if needs_on else 'reused season/team ON; exact because one game-bearing roster stint',
      'plausibility_max_minutes':max_ok,'date_window_plausible':bool(plausible),'rebound_keys':rebound_keys,
      'rows':rows,'requests':reqs,'generated_utc':now()
    }

def completed_set(): return {p.name for p in CACHE.glob('*.json.gz') if p.is_file()}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--batch-size',type=int,default=25); ap.add_argument('--attempts',type=int,default=5); ap.add_argument('--interval',type=float,default=.5); ap.add_argument('--self-test',action='store_true'); a=ap.parse_args()
    if a.self_test:
        assert minutes({'Minutes':12})==12; assert minutes({'SecondsPlayed':120})==2
        print('WOWY_REPAIR_V2_SELF_TEST=PASS'); return
    OUT.mkdir(parents=True,exist_ok=True); CACHE.mkdir(parents=True,exist_ok=True)
    all_t=read_targets(); assert len(all_t)==5199, len(all_t)
    done=completed_set(); pending=[m for m in all_t if cache_name(m) not in done]
    pending.sort(key=lambda m:(0 if (m['season']=='2016-17' and str(m['player_id'])=='101106' and int(m['team_id'])==1610612739) else 1,cache_name(m)))
    selected=pending[:a.batch_size]
    s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 TREB-WOWY-repair-v2/1.0','Accept':'application/json'})
    ok=0; errs=[]; started=time.monotonic()
    for i,meta in enumerate(selected,1):
        name=cache_name(meta); outp=CACHE/name
        try:
            result=query_window(s,meta,a.attempts,a.interval)
            if not result['complete']: raise RuntimeError(f"date-window plausibility failed: total={result['total_team_minutes']} games={result['team_games_in_window']}")
            if meta['season']=='2016-17' and str(meta['player_id'])=='101106' and int(meta['team_id'])==1610612739:
                proof={k:result[k] for k in ('season','team_id','player_id','player','query_start_date','query_end_date','team_games_in_window','minutes_on','minutes_off','total_team_minutes','date_window_plausible','rebound_keys')}
                if not (200 <= result['total_team_minutes'] <= 400): raise RuntimeError(f'Bogut V2 proof failed: {proof}')
                write_json(OUT/'wowy_bogut_date_proof.json',proof)
            write_gz(outp,result); ok+=1
            print(f'WOWY_V2 {i}/{len(selected)} OK {name} on={result["minutes_on"]:.1f} off={result["minutes_off"]:.1f} total={result["total_team_minutes"]:.1f} games={result["team_games_in_window"]}',flush=True)
        except Exception as e:
            errs.append({'file':name,'error':repr(e),'utc':now()}); print(f'WOWY_V2 ERROR {name}: {e!r}',flush=True)
    done2=completed_set(); remaining=len(all_t)-len(done2)
    summary={'generated_utc':now(),'target_version':'roster_repair_v2','total_targets':len(all_t),'complete_windows':len(done2),'remaining_windows':remaining,
             'batch_requested':len(selected),'batch_successes':ok,'batch_errors':len(errs),'batch_elapsed_seconds':round(time.monotonic()-started,3),
             'method':'get-wowy-stats with FromDate/ToDate against validated V2 roster targets','old_get_on_off_partial_cache_invalid':True,
             'errors':errs[-25:],'all_complete':remaining==0}
    write_json(SUMMARY,summary); write_json(TRIGGER,{'generated_utc':now(),'remaining_windows':remaining,'complete_windows':len(done2),'all_complete':remaining==0})
    print(json.dumps(summary,indent=2),flush=True)
    if errs: raise SystemExit(2)

if __name__=='__main__': main()
