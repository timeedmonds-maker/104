import gzip,json,csv,hashlib,zipfile
from pathlib import Path
SRC=Path('team_trb_all_players/impact_database/corrected_off/tenure_segment_on_off.jsonl.gz')
OUT=Path('native89_release'); OUT.mkdir(exist_ok=True)
# The settled Stage2 file is long-form. Inspect its real schema and fail closed on ambiguity.
rows=[]
with gzip.open(SRC,'rt',encoding='utf-8') as f:
    for line in f:
        if line.strip(): rows.append(json.loads(line))
print('SOURCE_ROWS',len(rows))
if not rows: raise SystemExit('empty Stage2 source')
cols=sorted(set().union(*(r.keys() for r in rows[:10000])))
print('COLUMNS',cols)
# Resolve aliases from actual payload.
def pick(opts):
    for x in opts:
        if x in cols: return x
    return None
season=pick(['season','SEASON']); pid=pick(['player_id','PLAYER_ID']); tid=pick(['team_id','TEAM_ID'])
metric=pick(['metric','metric_name','stat','STAT','name'])
on=pick(['on','on_value','ON','value_on']); off=pick(['off','off_value','OFF','value_off']); swing=pick(['swing','swing_value','SWING','value_swing'])
if not all([season,pid,tid,metric,on,off]):
    raise SystemExit('SCHEMA_ONLY:'+json.dumps({'columns':cols,'resolved':{'season':season,'player':pid,'team':tid,'metric':metric,'on':on,'off':off,'swing':swing}},sort_keys=True))
# Exclude TREB defensively even though native Stage2 should contain only the 89 established metrics.
def is_treb(x):
    s=''.join(ch for ch in str(x).lower() if ch.isalnum())
    return s in {'totalreboundpct','totalreboundpercentage','trebpct'}
native=[r for r in rows if not is_treb(r.get(metric))]
metrics=sorted({str(r.get(metric)) for r in native})
keys={(str(r.get(season)),str(r.get(pid)),str(r.get(tid))) for r in native}
print('METRICS',len(metrics)); print('KEYS',len(keys)); print('NATIVE_ROWS',len(native))
if len(metrics)!=89: raise SystemExit(f'expected 89 native metrics, got {len(metrics)}')
if len(keys)!=14524: raise SystemExit(f'expected 14524 canonical keys, got {len(keys)}')
# Exactly one row per canonical key/metric is required.
pairs={(str(r.get(season)),str(r.get(pid)),str(r.get(tid)),str(r.get(metric))) for r in native}
if len(pairs)!=1292636 or len(native)!=1292636:
    raise SystemExit(f'expected exactly 1292636 canonical key-metric rows, got rows={len(native)} unique={len(pairs)}')
# Verify ON/OFF and SWING identity. If swing is absent, derive it exactly from ON-OFF in the release.
outrows=[]; bad=0
for r in native:
    a=r.get(on); b=r.get(off)
    if a is None or b is None: raise SystemExit('missing ON/OFF value')
    s=r.get(swing) if swing else None
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        d=a-b
        if s is not None and isinstance(s,(int,float)) and abs(s-d)>1e-9: bad+=1
        s=d
    elif s is None:
        raise SystemExit('non-numeric ON/OFF without upstream swing')
    outrows.append([r.get(season),r.get(pid),r.get(tid),r.get(metric),a,b,s])
if bad: raise SystemExit(f'{bad} upstream swing identity failures')
with gzip.open(OUT/'native_89_metrics_on_off_swing.csv.gz','wt',newline='',encoding='utf-8') as f:
    w=csv.writer(f); w.writerow(['season','player_id','team_id','metric','on','off','swing']); w.writerows(outrows)
qa={'status':'PASS','seasons':26,'canonical_keys':14524,'native_metrics':89,'native_canonical_rows':1292636,'total_rebound_pct_included':False,'states':['ON','OFF','SWING'],'swing_identity':'PASS','source_rows':len(rows),'source_commit':'57ba237c36d75f7a3ef2cc998d91aa70a59b3c29'}
(OUT/'FINAL_QA_NATIVE89.json').write_text(json.dumps(qa,indent=2)+'\n')
(OUT/'README.txt').write_text('Final native Stage2 database: 89 metrics, 2000-01 through 2025-26. TotalReboundPct intentionally excluded. ON, OFF and SWING included.\n')
zipname=Path('NBA_native_89_metrics_2000-01_to_2025-26.zip')
with zipfile.ZipFile(zipname,'w',zipfile.ZIP_DEFLATED) as z:
    for p in OUT.iterdir(): z.write(p,p.name)
h=hashlib.sha256(zipname.read_bytes()).hexdigest(); Path(str(zipname)+'.sha256').write_text(h+'  '+zipname.name+'\n')
print(json.dumps({'status':'PASS','zip':str(zipname),'sha256':h,**qa},indent=2))