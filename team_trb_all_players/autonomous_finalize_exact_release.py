#!/usr/bin/env python3
from __future__ import annotations
import argparse, gzip, hashlib, json, math, sqlite3
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

K=['season','team_id','player_id']
EXPECTED_KEYS=14524
EXPECTED_NATIVE_METRICS=89
EXPECTED_NATIVE_ROWS=EXPECTED_KEYS*EXPECTED_NATIVE_METRICS
EXPECTED_PARTIAL=4877
EXPECTED_FULL=9647

def sid(v):
    s=str(v).strip()
    return s[:-2] if s.endswith('.0') and s[:-2].isdigit() else s

def canon(d):
    d=d.copy(); d['season']=d['season'].astype(str).str.replace('_','-',regex=False)
    d['team_id']=pd.to_numeric(d['team_id'],errors='raise').astype('int64'); d['player_id']=d['player_id'].map(sid)
    return d

def pct(s):
    x=pd.to_numeric(s,errors='coerce'); return x.where(x<=1.5,x/100.0)

def wavg(g,val,w):
    v=pd.to_numeric(g[val],errors='coerce'); wt=pd.to_numeric(g[w],errors='coerce')
    m=v.notna() & wt.notna() & (wt>0)
    return float(np.average(v[m],weights=wt[m])) if m.any() else math.nan

def career(detail):
    rows=[]
    for (pid,metric),g in detail.groupby(['player_id','metric'],sort=True):
        on=wavg(g,'on','minutes_on'); off=wavg(g,'off','minutes_off')
        names=[str(x) for x in g.get('player',pd.Series(dtype=str)).dropna() if str(x).strip()]
        rows.append({'player_id':pid,'player':names[0] if names else '','metric':metric,'player_team_seasons':int(len(g)),'season_count':int(g.season.nunique()),'team_count':int(g.team_id.nunique()),'minutes_on':float(pd.to_numeric(g.minutes_on,errors='coerce').fillna(0).sum()),'minutes_off':float(pd.to_numeric(g.minutes_off,errors='coerce').fillna(0).sum()),'on':on,'off':off,'swing':on-off if np.isfinite(on) and np.isfinite(off) else math.nan})
    d=pd.DataFrame(rows); d['qualifies_10000_minutes']=pd.to_numeric(d.minutes_on,errors='coerce')>=10000
    return d

def wide(detail,keys,meta):
    p=detail.pivot(index=keys,columns='metric',values=['on','off','swing'])
    p.columns=[f'{m}__{f}' for f,m in p.columns]; p=p.reset_index()
    present=[c for c in meta if c in detail.columns and c not in keys]
    if present:
        m=detail.groupby(keys,as_index=False)[present].first(); p=m.merge(p,on=keys,how='left',validate='one_to_one')
    return p

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--targets',required=True); ap.add_argument('--native',required=True); ap.add_argument('--partial',required=True); ap.add_argument('--fullcore',required=True); ap.add_argument('--out',required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)

    rows=[]
    with gzip.open(a.targets,'rt',encoding='utf-8') as f:
        for line in f:
            if line.strip(): rows.append(json.loads(line))
    targets=canon(pd.DataFrame(rows)); assert len(targets)==EXPECTED_KEYS and not targets.duplicated(K).any()
    partial_keys=targets[~targets.full_core_reuse.astype(bool)][K]; full_keys=targets[targets.full_core_reuse.astype(bool)][K]
    assert len(partial_keys)==EXPECTED_PARTIAL and len(full_keys)==EXPECTED_FULL

    native=canon(pd.read_parquet(a.native)); native['metric']=native.metric.astype(str)
    if 'off' not in native.columns: native['off']=pd.to_numeric(native['off_corrected'],errors='coerce')
    if 'swing' not in native.columns: native['swing']=pd.to_numeric(native.get('on_minus_off_corrected',pd.NA),errors='coerce')
    native['on']=pd.to_numeric(native['on'],errors='coerce'); native['off']=pd.to_numeric(native['off'],errors='coerce')
    native['swing']=native['on']-native['off']
    if 'player' not in native.columns: native['player']=native.get('subject_player','')
    names=sorted(native.metric.unique().tolist()); km=native.groupby(K).metric.nunique()
    assert len(native)==EXPECTED_NATIVE_ROWS and len(names)==89 and 'TotalReboundPct' not in names and len(km)==EXPECTED_KEYS and km.min()==89 and km.max()==89
    assert 'OReb%' in names and 'DReb%' in names and not native.duplicated(K+['metric']).any()
    meta=native.groupby(K,as_index=False).agg(player=('player','first'),minutes_on=('minutes_on','first'),minutes_off=('minutes_off','first'))

    partial=canon(pd.read_parquet(a.partial)); assert len(partial)==EXPECTED_PARTIAL and not partial.duplicated(K).any()
    pchk=partial_keys.merge(partial[K],on=K,how='outer',indicator=True); assert len(pchk)==EXPECTED_PARTIAL and pchk._merge.eq('both').all()
    full=canon(pd.read_csv(a.fullcore,dtype={'player_id':str},low_memory=False)); assert len(full)==EXPECTED_FULL and not full.duplicated(K).any()
    fchk=full_keys.merge(full[K],on=K,how='outer',indicator=True); assert len(fchk)==EXPECTED_FULL and fchk._merge.eq('both').all()

    p=partial[K].copy(); p['on']=pct(partial['treb_on']); p['off']=pct(partial['treb_off']); p['value_source']='EXACT_PARTIAL_INTEGER_COUNTS'
    f=full[K].copy(); f['on']=pct(full['direct_treb_on']); f['off']=pct(full['direct_treb_off']); f['value_source']=full['source'].astype(str)
    treb=pd.concat([p,f],ignore_index=True).merge(meta,on=K,how='left',validate='one_to_one'); treb['metric']='TotalReboundPct'; treb['swing']=treb['on']-treb['off']
    assert len(treb)==EXPECTED_KEYS and not treb.duplicated(K+['metric']).any() and not treb[['on','off','swing','minutes_on','minutes_off']].isna().any().any()

    allcols=sorted(set(native.columns)|set(treb.columns))
    for d in (native,treb):
        for c in allcols:
            if c not in d.columns: d[c]=pd.NA
    all90=pd.concat([native[allcols],treb[allcols]],ignore_index=True,sort=False)
    assert len(all90)==EXPECTED_KEYS*90 and all90.metric.nunique()==90 and not all90.duplicated(K+['metric']).any() and all90.groupby(K).metric.nunique().eq(90).all()
    all90.to_parquet(out/'all_metrics_player_team_season.parquet',index=False,compression='zstd'); all90.to_csv(out/'all_metrics_player_team_season.csv.gz',index=False,compression='gzip')
    treb.to_parquet(out/'treb_overlay_player_team_season_long.parquet',index=False,compression='zstd'); treb.to_csv(out/'treb_overlay_player_team_season_long.csv.gz',index=False,compression='gzip')

    c90=career(all90); assert c90.metric.nunique()==90 and not c90.duplicated(['player_id','metric']).any() and c90.groupby('player_id').metric.nunique().eq(90).all()
    ctreb=c90[c90.metric.eq('TotalReboundPct')].copy()
    c90.to_parquet(out/'all_metrics_career.parquet',index=False,compression='zstd'); c90.to_csv(out/'all_metrics_career.csv.gz',index=False,compression='gzip')
    ctreb.to_parquet(out/'career_treb_summary.parquet',index=False,compression='zstd'); ctreb.to_csv(out/'career_treb_summary.csv',index=False)
    treb.to_csv(out/'career_treb_detail.csv',index=False)

    w=wide(all90,K,['player','team_abbr','minutes_on','minutes_off']); cw=wide(c90,['player_id'],['player','minutes_on','minutes_off'])
    w.to_parquet(out/'final_combined_player_team_season_wide.parquet',index=False,compression='zstd'); w.to_csv(out/'final_combined_player_team_season_wide.csv.gz',index=False,compression='gzip')
    cw.to_parquet(out/'final_combined_career_wide.parquet',index=False,compression='zstd'); cw.to_csv(out/'final_combined_career_wide.csv.gz',index=False,compression='gzip')

    md=pd.DataFrame({'metric':sorted(all90.metric.astype(str).unique())}); md['family']=md.metric.map(lambda x:'canonical_total_rebound_pct' if x=='TotalReboundPct' else 'native_stage2_89')
    md.to_parquet(out/'metric_dictionary.parquet',index=False); md.to_csv(out/'metric_dictionary.csv',index=False)

    sqlite_path=out/'TREB_all_metrics.sqlite'; sqlite_path.unlink(missing_ok=True)
    con=sqlite3.connect(sqlite_path); all90.to_sql('all_metrics_player_team_season',con,index=False); c90.to_sql('all_metrics_career',con,index=False); treb.to_sql('treb_overlay_player_team_season',con,index=False); ctreb.to_sql('career_treb_summary',con,index=False); con.close()
    duck_path=out/'TREB_all_metrics.duckdb'; duck_path.unlink(missing_ok=True); db=duckdb.connect(str(duck_path)); db.register('a',all90); db.execute('create table all_metrics_player_team_season as select * from a'); db.unregister('a'); db.register('c',c90); db.execute('create table all_metrics_career as select * from c'); db.unregister('c'); db.register('t',treb); db.execute('create table treb_overlay_player_team_season as select * from t'); db.unregister('t'); db.close()

    ad=ctreb[ctreb.player_id.astype(str).eq('203500')]; assert len(ad)==1
    ad_minutes=float(ad.minutes_on.iloc[0]); q10=int(ctreb[pd.to_numeric(ctreb.minutes_on,errors='coerce').ge(10000)].player_id.nunique())
    qa={'status':'PASS','canonical_keys':EXPECTED_KEYS,'seasons':int(targets.season.nunique()),'native_metrics':89,'total_metrics':90,'native_canonical_rows':EXPECTED_NATIVE_ROWS,'partial_treb':int(len(p)),'full_core_treb':int(len(f)),'total_rebound_pct_rows':int(len(treb)),'duplicate_total_rebound_pct':int(treb.duplicated(K+['metric']).sum()),'missing_total_rebound_pct':int(treb[['on','off','swing']].isna().any(axis=1).sum()),'steven_adams_player_id':'203500','steven_adams_career_minutes':ad_minutes,'steven_adams_career_total_rebound_pct_rows':int(len(ad)),'career_10000_min_total_rebound_pct_population':q10,'rounded_percentage_backsolve':False,'opponent_rebound_inference':False,'partial_tenure_whole_team_subtraction':False,'empirical_model_used':False}
    assert qa['seasons']==26 and 20460<=ad_minutes<=20485 and 540<=q10<=565
    (out/'FINAL_QA.json').write_text(json.dumps(qa,indent=2,sort_keys=True)+'\n')
    provenance={'stage2_sha':'57ba237c36d75f7a3ef2cc998d91aa70a59b3c29','immutable_base_artifact_id':9211673158,'canonical_23_key_overlay':'team_trb_all_players/final_integrity_rebuild/canonical_23_key_combined_overlay/targeted_23_metric_overlay.jsonl.gz','partial_treb_source':'exact tenure-segment integer rebound counts','full_core_source_counts':{str(k):int(v) for k,v in full.source.value_counts().to_dict().items()},'architecture':'89 native Stage2 metrics + separate canonical TotalReboundPct = 90','trade_policy':'settled roster-tenure v2 policy','rounded_percentage_backsolve':False,'opponent_rebound_inference':False,'partial_tenure_whole_team_subtraction':False,'empirical_model_used':False}
    (out/'PROVENANCE.json').write_text(json.dumps(provenance,indent=2,sort_keys=True)+'\n')
    (out/'README.md').write_text('# TREB Final Database 2000-01 to 2025-26\n\nCanonical universe: 14,524 player-team-season keys, 26 regular seasons, 89 native Stage2 metrics plus canonical TotalReboundPct = 90 metrics. TotalReboundPct includes ON, OFF and SWING. Career/multi-season values aggregate ON and OFF separately using minutes exposure; SWING is aggregated ON minus aggregated OFF. No rounded percentage backsolve, opponent rebound inference, partial-tenure whole-team subtraction, or rejected empirical residual model is used.\n')
    print(json.dumps(qa,indent=2,sort_keys=True))

if __name__=='__main__': main()
