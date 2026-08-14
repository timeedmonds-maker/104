#!/usr/bin/env python3
from __future__ import annotations
import argparse, gzip, json, re
from pathlib import Path
import numpy as np
import pandas as pd

KEYS=['season','team_id','player_id']
SEASON_RE=re.compile(r'20\d{2}-\d{2}')

def norm(x): return re.sub(r'[^a-z0-9]+','',str(x).lower())
def pid(x):
    s=str(x).strip(); return s[:-2] if s.endswith('.0') and s[:-2].isdigit() else s

def canon(df):
    x=df.copy(); x['season']=x['season'].astype(str)
    x['team_id']=pd.to_numeric(x['team_id'],errors='raise').astype('int64')
    x['player_id']=x['player_id'].map(pid).astype('string'); return x

def read_canonical(p):
    rows=[]
    with gzip.open(p,'rt',encoding='utf-8') as f:
        for line in f:
            if line.strip(): rows.append(json.loads(line))
    return canon(pd.DataFrame(rows))[KEYS].drop_duplicates()

def read_any(p, nrows=None):
    s=str(p)
    if s.endswith('.parquet'): return pd.read_parquet(p)
    return pd.read_csv(p,nrows=nrows,low_memory=False)

def schema(p):
    try:
        if str(p).endswith('.parquet'):
            import pyarrow.parquet as pq; return list(pq.read_schema(p).names)
        return list(pd.read_csv(p,nrows=2,low_memory=False).columns)
    except Exception: return []

def pick(cols, exact=(), contains=()):
    m={norm(c):c for c in cols}
    for e in exact:
        if norm(e) in m: return m[norm(e)]
    for c in cols:
        n=norm(c)
        if all(t in n for t in contains): return c
    return None

def compare(a,b):
    a=pd.to_numeric(a,errors='coerce'); b=pd.to_numeric(b,errors='coerce'); m=a.notna()&b.notna()
    if not m.any(): return {'n':0}
    d=(a[m]-b[m]).abs(); den=np.maximum(np.abs(b[m]),1.0)
    return {'n':int(m.sum()),'exact_match_rate':float((d<=1e-9).mean()),'within_1_rate':float((d<=1).mean()),'median_abs_diff':float(d.median()),'p95_abs_diff':float(d.quantile(.95)),'max_abs_diff':float(d.max()),'median_relative_diff':float((d/den).median()),'corr':None if m.sum()<2 else float(np.corrcoef(a[m],b[m])[0,1])}

def family(path, root):
    rel=str(path.relative_to(root)); return SEASON_RE.sub('{season}',rel)

def attempt_team_game(path, root):
    cols=schema(path)
    game=pick(cols, exact=('game_id','gameid'), contains=('game','id'))
    team=pick(cols, exact=('team_id','teamid'), contains=('team','id'))
    treb=pick(cols, exact=('rebounds','team_rebounds','total_rebounds'))
    oreb=pick(cols, exact=('offrebounds','off_rebounds','oreb','team_oreb','team_off_rebounds'))
    dreb=pick(cols, exact=('defrebounds','def_rebounds','dreb','team_dreb','team_def_rebounds'))
    if not game or not team or not (treb or (oreb and dreb)): return None
    try: d=read_any(path)
    except Exception as e: return {'family':family(path,root),'path':str(path),'error':repr(e)}
    season_col=pick(cols,exact=('season',))
    if season_col: d['_season']=d[season_col].astype(str)
    else:
        mm=SEASON_RE.search(str(path)); d['_season']=mm.group(0) if mm else None
    d['_game']=d[game].astype(str); d['_team']=pd.to_numeric(d[team],errors='coerce')
    ent=pick(cols,exact=('entityid','entity_id'))
    if ent:
        ev=pd.to_numeric(d[ent],errors='coerce'); tv=pd.to_numeric(d[team],errors='coerce'); eq=ev.eq(tv)
        if eq.any(): d=d[eq].copy()
    if treb:
        d['_reb']=pd.to_numeric(d[treb],errors='coerce')
    else:
        d['_reb']=pd.to_numeric(d[oreb],errors='coerce')+pd.to_numeric(d[dreb],errors='coerce')
    base=d[['_season','_game','_team','_reb']].dropna()
    if base.empty: return {'family':family(path,root),'path':str(path),'rows':0,'usable':False}
    nun=base.groupby(['_season','_game','_team'])['_reb'].nunique(dropna=True)
    conflicting=int((nun>1).sum())
    unique=base.groupby(['_season','_game','_team'],as_index=False)['_reb'].first()
    return {'family':family(path,root),'path':str(path),'rows':int(len(base)),'unique_game_team':int(len(unique)),'conflicting_game_team_values':conflicting,'game_col':game,'team_col':team,'reb_col':treb,'oreb_col':oreb,'dreb_col':dreb,'usable':bool(len(unique)>0 and conflicting==0),'data':unique}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--canonical',required=True); ap.add_argument('--exact-detail',required=True); ap.add_argument('--root',required=True); ap.add_argument('--out',required=True)
    a=ap.parse_args(); root=Path(a.root); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    canonical=read_canonical(a.canonical); assert len(canonical)==14524
    exact=canon(pd.read_csv(a.exact_detail,low_memory=False)); exact_keys=exact[KEYS].drop_duplicates(); assert len(exact_keys)==4877
    complement=canonical.merge(exact_keys.assign(_e=1),on=KEYS,how='left').query('_e != 1'); assert len(complement)==9647

    frames=[]
    for y in range(2000,2026):
        s=f'{y}-{(y+1)%100:02d}'; p=root/'impact_database/outputs'/s/'team_rebound_derived.csv.gz'; d=pd.read_csv(p,low_memory=False)
        if 'season' not in d: d['season']=s
        frames.append(d)
    derived=canon(pd.concat(frames,ignore_index=True,sort=False)); derived=canonical.merge(derived,on=KEYS,how='left',validate='one_to_one')

    sums=['team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on','seconds_on']
    e=exact.groupby(KEYS,as_index=False)[sums].sum()
    e['team_rebounds_on']=e.team_oreb_on+e.team_dreb_on; e['opponent_rebounds_on']=e.opponent_oreb_on+e.opponent_dreb_on; e['minutes_on']=e.seconds_on/60
    z=derived.merge(e,on=KEYS,how='inner')
    comparisons={}
    for dc,ec in [('team_off_rebounds','team_oreb_on'),('team_def_rebounds','team_dreb_on'),('team_rebounds','team_rebounds_on'),('opponent_rebounds_exact','opponent_rebounds_on'),('seconds','seconds_on'),('minutes','minutes_on')]:
        if dc in z and ec in z: comparisons[f'{dc}__vs__{ec}']=compare(z[dc],z[ec])

    needles=['team_rebound_derived','opponent_rebounds_exact','team_off_rebounds','team_def_rebounds','off_rebound_pct_displayed']
    code=[]
    for p in root.rglob('*.py'):
        try: lines=p.read_text(errors='ignore').splitlines()
        except Exception: continue
        hits=[]
        for i,line in enumerate(lines):
            if any(n in line for n in needles): hits.append({'line':i+1,'context':lines[max(0,i-6):min(len(lines),i+7)]})
        if hits: code.append({'path':str(p),'hits':hits[:50]})

    candidate_paths=[]
    for p in root.rglob('*'):
        if not p.is_file() or not (str(p).endswith('.csv') or str(p).endswith('.csv.gz') or str(p).endswith('.parquet')): continue
        low=str(p).lower()
        if not any(k in low for k in ('game','box','team','rebound','fact','total')): continue
        cols=schema(p); n=[norm(c) for c in cols]
        if any('gameid' in x for x in n) and any('teamid' in x for x in n) and any(('reb' in x or 'rebound' in x) and not any(q in x for q in ('pct','rate','candidate')) for x in n): candidate_paths.append(p)
    attempts=[]
    for p in candidate_paths:
        r=attempt_team_game(p,root)
        if r: attempts.append(r)

    fam={}
    for r in attempts:
        if not r.get('usable') or 'data' not in r: continue
        fam.setdefault(r['family'],[]).append(r)
    family_results=[]; best=None
    for f,rs in fam.items():
        d=pd.concat([r['data'] for r in rs],ignore_index=True)
        n=d.groupby(['_season','_game','_team'])['_reb'].nunique(); conflicts=int((n>1).sum())
        d=d.groupby(['_season','_game','_team'],as_index=False)['_reb'].first()
        pair_counts=d.groupby(['_season','_game'])['_team'].nunique(); two=int((pair_counts==2).sum()); bad=int((pair_counts!=2).sum())
        valid_games=set(map(tuple,pair_counts[pair_counts==2].reset_index()[['_season','_game']].to_records(index=False)))
        if valid_games:
            key=list(zip(d._season,d._game)); d=d[[k in valid_games for k in key]].copy()
        opp=d.rename(columns={'_team':'_opp_team','_reb':'_opp_reb'})
        q=d.merge(opp,on=['_season','_game'],how='inner'); q=q[q['_team']!=q['_opp_team']]
        own=q.groupby(['_season','_team'],as_index=False).agg(team_season_rebounds=('_reb','sum'),opponent_season_rebounds=('_opp_reb','sum'),games=('_game','nunique'))
        rec={'family':f,'files':len(rs),'seasons':int(own._season.nunique()),'season_teams':int(len(own)),'games_two_team':two,'games_bad_team_count':bad,'conflicts':conflicts,'negative_counts':int((own[['team_season_rebounds','opponent_season_rebounds']]<0).any(axis=1).sum())}
        family_results.append(rec)
        score=(rec['seasons'],rec['season_teams'],-rec['conflicts'],-rec['games_bad_team_count'])
        if best is None or score>best[0]: best=(score,rec,own)

    best_summary=None
    if best:
        rec,own=best[1],best[2]; best_summary=rec.copy()
        own.to_csv(out/'BEST_TEAM_SEASON_REBOUND_TOTALS.csv.gz',index=False,compression='gzip')
        mapdf=own.rename(columns={'_season':'season','_team':'team_id'})
        t=derived.merge(mapdf,on=['season','team_id'],how='left')
        tr=pd.to_numeric(t.get('team_rebounds'),errors='coerce'); rr=pd.to_numeric(t.get('opponent_rebounds_exact'),errors='coerce')
        off_team=pd.to_numeric(t.get('team_season_rebounds'),errors='coerce')-tr
        off_opp=pd.to_numeric(t.get('opponent_season_rebounds'),errors='coerce')-rr
        t['_off_team']=off_team; t['_off_opp']=off_opp
        best_summary.update({'canonical_with_totals':int(t.team_season_rebounds.notna().sum()),'complement_with_totals':int(t.merge(complement[KEYS],on=KEYS,how='inner').team_season_rebounds.notna().sum()),'negative_off_team':int((off_team<0).sum()),'negative_off_opponent':int((off_opp<0).sum()),'positive_off_denominator':int((off_team+off_opp>0).sum())})

    clean_attempts=[]
    for r in attempts:
        x={k:v for k,v in r.items() if k!='data'}; clean_attempts.append(x)
    production_ready=bool(best_summary and best_summary.get('canonical_with_totals')==14524 and best_summary.get('complement_with_totals')==9647 and best_summary.get('negative_off_team')==0 and best_summary.get('negative_off_opponent')==0)
    report={'status':'PASS','canonical_keys':14524,'exact_keys':4877,'complement_keys':9647,'derived_vs_exact':comparisons,'source_code_evidence':code,'candidate_paths':len(candidate_paths),'candidate_attempts':clean_attempts,'family_results':sorted(family_results,key=lambda r:(-r['seasons'],-r['season_teams'])),'best_team_game_family':best_summary,'production_subtraction_numerically_ready':production_ready,'rounded_percentage_backsolve_used':False}
    (out/'TREB_DIRECT_TOTALS_PROOF_V2.json').write_text(json.dumps(report,indent=2)+'\n')
    md=['# TREB direct totals proof v2','',f'- Canonical: **14524**',f'- Exact: **4877**',f'- Complement: **9647**',f'- Candidate paths: **{len(candidate_paths)}**',f'- Production subtraction numerically ready: **{production_ready}**','',f'Best family: `{json.dumps(best_summary,sort_keys=True)}`','', 'Derived-vs-exact:', '```json',json.dumps(comparisons,indent=2),'```','', 'Top families:', '```json',json.dumps(report['family_results'][:20],indent=2),'```']
    (out/'TREB_DIRECT_TOTALS_PROOF_V2.md').write_text('\n'.join(md)+'\n')
    print(json.dumps({'status':'PASS','candidate_paths':len(candidate_paths),'best_team_game_family':best_summary,'derived_vs_exact':comparisons,'production_subtraction_numerically_ready':production_ready,'source_code_files':[x['path'] for x in code]},indent=2))

if __name__=='__main__': main()
