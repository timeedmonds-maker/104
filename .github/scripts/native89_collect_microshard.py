import gzip,json,math,os,pathlib,random,time
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests

BASE=int(os.environ['BASE_SHARD']); MICRO=int(os.environ['MICRO'])
S=BASE*4+MICRO; N=1024
ROOT=pathlib.Path('team_trb_all_players/impact_database')
src=ROOT/'corrected_off/tenure_segment_on_off.jsonl.gz'
out=pathlib.Path(f'raw_out_{MICRO}'); out.mkdir(exist_ok=True)
seen={}; metric_rows=0
with gzip.open(src,'rt',encoding='utf-8') as f:
    for line in f:
        if not line.strip(): continue
        r=json.loads(line); metric_rows+=1
        k=(str(r['season']),str(r['team_id']),str(r['player_id']),str(r['query_start_date']),str(r['query_end_date']))
        if k not in seen:
            seen[k]={x:r.get(x) for x in ['season','team_id','team_abbr','player_id','player','segment_index','segment_count','query_start_date','query_end_date','minutes_on','minutes_off']}
targets=[seen[k] for k in sorted(seen)]
selected=[m for i,m in enumerate(targets) if i%N==S]
assert metric_rows==1353334 and len(targets)==15206
url='https://api.pbpstats.com/get-wowy-stats/nba'; transient={429,500,502,503,504}

def num(x):
    try:
        v=float(x); return v if math.isfinite(v) else None
    except: return None

def mins(r):
    v=num(r.get('Minutes'))
    if v is not None: return v
    v=num(r.get('SecondsPlayed')); return v/60 if v is not None else None

def get(meta,typ,state):
    p={'Season':str(meta['season']),'SeasonType':'Regular Season','TeamId':str(meta['team_id']),
       'FromDate':str(meta['query_start_date']),'ToDate':str(meta['query_end_date']),'Type':typ,
       ('0Exactly1OnFloor' if state=='on' else '0Exactly1OffFloor'):str(meta['player_id'])}
    hist=[]
    for a in range(1,4):
        if a>1: time.sleep(a+random.random())
        try:
            rr=requests.get(url,params=p,timeout=25); hist.append(rr.status_code)
            if rr.status_code==200:
                x=rr.json().get('single_row_table_data') or {}
                if isinstance(x,dict) and x: return x
            if rr.status_code not in transient: break
        except Exception as e:
            hist.append(type(e).__name__)
    raise RuntimeError(f'{typ}/{state} failed {hist}')

def one(m):
    req=[('Team','on'),('Team','off'),('Opponent','on'),('Opponent','off')]; rows={}
    with ThreadPoolExecutor(max_workers=4) as ex:
        fs={ex.submit(get,m,*q):q for q in req}
        for f in as_completed(fs):
            typ,state=fs[f]; rows[f'{typ.lower()}_{state}']=f.result()
    ton,toff=mins(rows['team_on']),mins(rows['team_off']); con,coff=num(m['minutes_on']),num(m['minutes_off'])
    don=None if ton is None or con is None else ton-con; doff=None if toff is None or coff is None else toff-coff
    if ton is None or toff is None or (don is not None and abs(don)>max(8,.05*max(con,1))) or (doff is not None and abs(doff)>max(8,.05*max(coff,1))):
        raise RuntimeError(f'minute mismatch raw={ton}/{toff} canonical={con}/{coff}')
    return {**m,'raw_minutes_on':ton,'raw_minutes_off':toff,'minutes_on_delta':don,'minutes_off_delta':doff,
            'rows':rows,'source':'PBP Stats exact canonical tenure date window'}

errors=[]; ok=0; p=out/f'raw_tenure_shard_{S:04d}.jsonl.gz'; started=time.time()
with gzip.open(p,'wt',encoding='utf-8') as f:
    for i,m in enumerate(selected,1):
        t=time.time()
        try:
            f.write(json.dumps(one(m),separators=(',',':'))+'\n'); ok+=1
        except Exception as e:
            errors.append({'target':m,'error':repr(e)})
        print(f'MICROSHARD={S} progress={i}/{len(selected)} ok={ok} errors={len(errors)} row_elapsed={time.time()-t:.1f}s total_elapsed={time.time()-started:.1f}s',flush=True)
rep={'base_shard':BASE,'micro':MICRO,'shard':S,'shards':N,'selected':len(selected),'successes':ok,'errors':len(errors),'elapsed_seconds':round(time.time()-started,1),'error_rows':errors}
(out/f'raw_tenure_shard_{S:04d}_REPORT.json').write_text(json.dumps(rep,indent=2)+'\n')
print(json.dumps({k:v for k,v in rep.items() if k!='error_rows'},indent=2),flush=True)
if errors or ok!=len(selected): raise SystemExit(2)
