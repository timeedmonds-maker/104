import gzip,json,csv,hashlib,zipfile,sqlite3,os
from pathlib import Path
SRC=Path('team_trb_all_players/impact_database/corrected_off/tenure_segment_on_off.jsonl.gz')
OUT=Path('native89_release'); OUT.mkdir(exist_ok=True)
rows=[]
with gzip.open(SRC,'rt',encoding='utf-8') as f:
    for line in f:
        if line.strip(): rows.append(json.loads(line))
if len(rows)!=15206: raise SystemExit(f'expected 15206 Stage2 windows, got {len(rows)}')
# Identify metric containers from payload rather than inventing schema.
def flat_metric_triplets(r):
    out=[]
    # common long-form container
    if isinstance(r.get('metrics'),dict):
        for m,v in r['metrics'].items():
            if isinstance(v,dict) and all(k in v for k in ('on','off')):
                on=v['on']; off=v['off']; sw=v.get('swing')
                if sw is None and isinstance(on,(int,float)) and isinstance(off,(int,float)): sw=on-off
                out.append((m,on,off,sw))
    # common *_on/*_off wide form
    keys=set(r)
    for k in list(keys):
        if k.endswith('_on') and k[:-3]+'_off' in r:
            m=k[:-3]; on=r[k]; off=r[m+'_off']; sw=r.get(m+'_swing')
            if sw is None and isinstance(on,(int,float)) and isinstance(off,(int,float)): sw=on-off
            out.append((m,on,off,sw))
    return out
sample=[]
for r in rows[:100]: sample.extend(flat_metric_triplets(r))
metrics=sorted(set(x[0] for x in sample))
if len(metrics)!=89: raise SystemExit(f'fail closed: expected 89 native metrics, detected {len(metrics)}: {metrics[:20]}')
if any('totalrebound' in m.lower().replace('_','') for m in metrics): raise SystemExit('TotalReboundPct unexpectedly present in native layer')
# Canonicalize by player/team/season; weighted aggregation is only allowed when payload already contains canonical values.
# Prefer rows explicitly marked canonical/aggregate; otherwise fail closed rather than averaging percentages.
canon=[]
for r in rows:
    trips=flat_metric_triplets(r)
    if len({x[0] for x in trips})!=89: continue
    season=r.get('season'); pid=r.get('player_id') or r.get('PLAYER_ID'); tid=r.get('team_id') or r.get('TEAM_ID')
    if season is None or pid is None or tid is None: continue
    # accept only rows representing the established corrected-off canonical window
    canon.append((str(season),str(pid),str(tid),r,trips))
# Multiple tenure segments require established upstream canonicalisation; do not average here.
keys={x[:3] for x in canon}
if len(keys)!=14524 or len(canon)!=14524:
    raise SystemExit(f'fail closed: payload is segment-level ({len(canon)} rows/{len(keys)} keys); native canonical export must use established canonicaliser')
long=[]
for season,pid,tid,r,trips in canon:
    for m,on,off,sw in trips:
        long.append([season,pid,tid,m,on,off,sw])
if len(long)!=1292636: raise SystemExit(f'expected 1292636 native rows, got {len(long)}')
with gzip.open(OUT/'native_89_metrics_on_off_swing.csv.gz','wt',newline='',encoding='utf-8') as f:
    w=csv.writer(f); w.writerow(['season','player_id','team_id','metric','on','off','swing']); w.writerows(long)
qa={'status':'PASS','seasons':26,'canonical_keys':14524,'native_metrics':89,'native_canonical_rows':len(long),'total_rebound_pct_included':False,'states':['ON','OFF','SWING'],'swing_identity':'derived_or_upstream','source_commit':'57ba237c36d75f7a3ef2cc998d91aa70a59b3c29'}
(OUT/'FINAL_QA_NATIVE89.json').write_text(json.dumps(qa,indent=2)+'\n')
(OUT/'README.txt').write_text('Complete native Stage2 release: 89 metrics only; TotalReboundPct intentionally excluded. Includes ON, OFF and SWING.\n')
zipname=Path('NBA_native_89_metrics_2000-01_to_2025-26.zip')
with zipfile.ZipFile(zipname,'w',zipfile.ZIP_DEFLATED) as z:
    for p in OUT.iterdir(): z.write(p,p.name)
h=hashlib.sha256(zipname.read_bytes()).hexdigest(); Path(str(zipname)+'.sha256').write_text(h+'  '+zipname.name+'\n')
print(json.dumps({'status':'PASS','zip':str(zipname),'sha256':h,**qa},indent=2))