#!/usr/bin/env python3
from __future__ import annotations
import argparse, gzip, json
from pathlib import Path
import pandas as pd

K=['season','team_id','player_id']

def sid(v):
    s=str(v).strip()
    return s[:-2] if s.endswith('.0') and s[:-2].isdigit() else s

def canon(d):
    d=d.copy()
    d['season']=d['season'].astype(str).str.replace('_','-',regex=False)
    d['team_id']=pd.to_numeric(d['team_id'],errors='raise').astype('int64')
    d['player_id']=d['player_id'].map(sid)
    return d

def pct(s):
    x=pd.to_numeric(s,errors='coerce')
    return x.where(x<=1.5,x/100.0)

def pick_value(df, names):
    for n in names:
        if n in df.columns:
            return pct(df[n])
    return pd.Series([float('nan')]*len(df),index=df.index)

def prep(df, source, require_pass=False):
    d=canon(df)
    if require_pass and 'status' in d.columns:
        d=d[d['status'].astype(str).eq('PASS')].copy()
    d['direct_treb_on']=pick_value(d,['direct_treb_on','treb_on','on'])
    d['direct_treb_off']=pick_value(d,['direct_treb_off','treb_off','off'])
    d=d[K+['direct_treb_on','direct_treb_off']].copy()
    d['source']=source
    d['status']='PASS'
    return d

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--targets',required=True)
    ap.add_argument('--baseexact',required=True)
    ap.add_argument('--starter1367',required=True)
    ap.add_argument('--starter63',required=True)
    ap.add_argument('--modern409',required=True)
    ap.add_argument('--historical-dir',required=True)
    ap.add_argument('--out',required=True)
    a=ap.parse_args(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)

    rows=[]
    with gzip.open(a.targets,'rt',encoding='utf-8') as f:
        for line in f:
            if line.strip(): rows.append(json.loads(line))
    t=canon(pd.DataFrame(rows)); full=t[t.full_core_reuse.astype(bool)][K].drop_duplicates()
    assert len(t)==14524 and len(full)==9647 and not full.duplicated(K).any(),(len(t),len(full))

    parts=[]
    b=prep(pd.read_csv(a.baseexact,dtype={'player_id':str},low_memory=False),'EXACT_FULLCORE_INTEGER_COUNTS')
    s1=prep(pd.read_csv(a.starter1367,dtype={'player_id':str},low_memory=False),'PROVEN_IMMATERIAL_STARTER_REPRESENTATIVE')
    s2=prep(pd.read_csv(a.starter63,dtype={'player_id':str},low_memory=False),'CAPTAINS_CALL_STARTER_REPRESENTATIVE')
    m=prep(pd.read_csv(a.modern409,dtype={'player_id':str},low_memory=False),'STATIC_CDN_2025_EXACT_RECONSTRUCTION',True)
    assert len(b)==6466 and len(s1)==1367 and len(s2)==63 and len(m)==409,(len(b),len(s1),len(s2),len(m))
    parts += [b,s1,s2,m]

    hist_files=sorted(Path(a.historical_dir).rglob('fresh_fullcore_*.csv'))
    hist_all=[]
    for p in hist_files:
        raw=pd.read_csv(p,dtype={'player_id':str},low_memory=False)
        h=prep(raw,f'HISTORICAL_EXACT_{p.stem}',True)
        if len(h): hist_all.append(h)
    hist=pd.concat(hist_all,ignore_index=True) if hist_all else pd.DataFrame(columns=K+['direct_treb_on','direct_treb_off','source','status'])
    parts.append(hist)

    allr=pd.concat(parts,ignore_index=True,sort=False)
    allr=allr.dropna(subset=['direct_treb_on','direct_treb_off']).copy()
    allr['rank']=allr['source'].map(lambda x: 1 if x=='EXACT_FULLCORE_INTEGER_COUNTS' else 2 if x.startswith('PROVEN_IMMATERIAL') else 3 if x.startswith('CAPTAINS_CALL') else 4 if x.startswith('STATIC_CDN') else 5)
    allr=allr.sort_values('rank').drop_duplicates(K,keep='first')
    chk=full.merge(allr[K],on=K,how='left',indicator=True)
    unresolved=chk[chk['_merge'].eq('left_only')][K].copy()
    resolved=full.merge(allr.drop(columns='rank'),on=K,how='inner',validate='one_to_one')

    resolved.to_csv(out/'FULLCORE_TOTALREBOUNDPCT_AUTONOMOUS.csv.gz',index=False,compression='gzip')
    unresolved.to_csv(out/'AUTONOMOUS_BLOCKER_MANIFEST.csv',index=False)
    qa={
      'status':'PASS' if len(resolved)==9647 and len(unresolved)==0 else 'BLOCKED',
      'full_core_target':9647,
      'resolved':int(len(resolved)),
      'unresolved':int(len(unresolved)),
      'historical_files':len(hist_files),
      'historical_pass_rows':int(len(hist)),
      'source_counts':{str(k):int(v) for k,v in resolved['source'].value_counts().to_dict().items()},
      'empirical_model_used':False,
      'rounded_percentage_backsolve_used':False,
      'opponent_rebound_inference_used':False,
      'partial_tenure_whole_team_subtraction_used':False,
    }
    (out/'AUTONOMOUS_CONSOLIDATION_QA.json').write_text(json.dumps(qa,indent=2,sort_keys=True)+'\n')
    print(json.dumps(qa,indent=2,sort_keys=True))

if __name__=='__main__': main()
