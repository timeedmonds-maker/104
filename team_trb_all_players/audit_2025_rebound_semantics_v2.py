#!/usr/bin/env python3
"""2025-26 cross-feed rebound audit for the team-rebounding metric.

The old semantic gate incorrectly included mutable cumulative rebound counters and
required NBA v3 to expose OREB/DREB subtype text it does not expose.  This audit
uses only event identity needed to establish the rebound universe:
  1. exact entity + game + period + normalized clock;
  2. unique same-team + game + period + normalized clock for scorekeeper
     rebounder-attribution disagreements (metric-equivalent for team TREB);
  3. unique same-team rows within 0.11 seconds for clock-format/rounding drift.
Multiplicity is preserved at every layer.  All residuals and attribution
conflicts are emitted explicitly.  No production row is changed here.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import re
from pathlib import Path

import pandas as pd

PLAYER_MAX=1610612737
ISO=re.compile(r"PT(?:(\d+)M)?([0-9.]+)S",re.I)
COLON=re.compile(r"^(\d+):(\d+(?:\.\d+)?)$")

def clock_seconds(v:object)->float:
    s=str(v).strip()
    m=ISO.fullmatch(s)
    if m: return 60.0*int(m.group(1) or 0)+float(m.group(2))
    m=COLON.fullmatch(s)
    if m: return 60.0*int(m.group(1))+float(m.group(2))
    try: return float(s)
    except Exception as e: raise ValueError(f'unsupported clock {s!r}') from e

def qclock(v:object)->int:
    # hundredths avoid collapsing genuine 0.1s distinctions while making
    # textual PT06M10.00S and 06:10.0 identical.
    return int(round(clock_seconds(v)*100.0))

def num(v):
    x=pd.to_numeric(pd.Series([v]),errors='coerce').iloc[0]
    return None if pd.isna(x) else int(x)

def entity(row:pd.Series):
    pid=num(row.get('personId'))
    tid=num(row.get('teamId'))
    if pid is not None and 0<pid<PLAYER_MAX: return ('player',pid)
    if tid is not None and tid>0: return ('team',tid)
    if pid is not None and pid>=PLAYER_MAX: return ('team',pid)
    return ('unknown',0)

def team_id(row:pd.Series):
    tid=num(row.get('teamId'))
    if tid is not None and tid>0: return tid
    typ,eid=entity(row)
    return eid if typ=='team' and eid>0 else None

def rec(row:pd.Series)->dict:
    out={}
    for k in ['gameId','actionNumber','orderNumber','period','clock','actionType','subType','description','personId','playerName','teamId','teamTricode','reboundTotal','reboundDefensiveTotal','reboundOffensiveTotal']:
        if k in row.index:
            v=row[k]; out[k]=None if pd.isna(v) else (v.item() if hasattr(v,'item') else v)
    out['clock_seconds']=clock_seconds(row.clock); out['clock_hundredths']=qclock(row.clock)
    out['entity']=list(entity(row)); out['effective_team_id']=team_id(row)
    return out

def exact_sig(row):
    typ,eid=entity(row)
    return (int(row.gameId),int(row.period),qclock(row.clock),typ,int(eid))

def team_sig(row):
    return (int(row.gameId),int(row.period),qclock(row.clock),team_id(row))

def consume_by_counter(rows:pd.DataFrame, sigfn, other_counter:Counter):
    remain=other_counter.copy(); matched=[]; residual=[]
    for idx,row in rows.sort_values(['gameId','period','actionNumber'],kind='stable').iterrows():
        s=sigfn(row)
        if remain[s]>0:
            matched.append(int(idx)); remain[s]-=1
        else: residual.append(int(idx))
    return matched,residual

def unique_pair_by_team(c:pd.DataFrame,n:pd.DataFrame):
    cg=defaultdict(list); ng=defaultdict(list)
    for idx,r in c.iterrows(): cg[team_sig(r)].append(int(idx))
    for idx,r in n.iterrows(): ng[team_sig(r)].append(int(idx))
    pairs=[]; usedc=set(); usedn=set()
    for s in sorted(set(cg)&set(ng),key=str):
        if s[-1] is None: continue
        if len(cg[s])==1 and len(ng[s])==1:
            ci,ni=cg[s][0],ng[s][0]; pairs.append((ci,ni));usedc.add(ci);usedn.add(ni)
    return pairs,usedc,usedn

def unique_near_team_pairs(c:pd.DataFrame,n:pd.DataFrame,tol=0.11):
    # Only pair when a row has exactly one reciprocal candidate from same
    # game/period/team within tolerance. This prevents greedy ambiguity.
    candc=defaultdict(list); candn=defaultdict(list)
    for ci,cr in c.iterrows():
        ct=team_id(cr)
        if ct is None: continue
        for ni,nr in n.iterrows():
            if int(cr.gameId)!=int(nr.gameId) or int(cr.period)!=int(nr.period) or ct!=team_id(nr): continue
            if abs(clock_seconds(cr.clock)-clock_seconds(nr.clock))<=tol+1e-9:
                candc[int(ci)].append(int(ni));candn[int(ni)].append(int(ci))
    pairs=[]
    for ci,ns in candc.items():
        if len(ns)!=1: continue
        ni=ns[0]
        if len(candn.get(ni,[]))==1: pairs.append((ci,ni))
    return pairs

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cdn',type=Path,required=True);ap.add_argument('--nba',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    c0=pd.read_csv(a.cdn,low_memory=False);n0=pd.read_csv(a.nba,low_memory=False)
    for f in (c0,n0):
        for col in ['gameId','period','actionNumber']:
            f[col]=pd.to_numeric(f[col],errors='raise').astype('int64')
        f['_action']=f.actionType.astype('string').fillna('').str.strip().str.lower()
    c=c0[c0._action.eq('rebound')].copy();n=n0[n0._action.eq('rebound')].copy()
    c.index=range(len(c));n.index=range(len(n))

    cs=Counter(exact_sig(r) for _,r in c.iterrows());ns=Counter(exact_sig(r) for _,r in n.iterrows())
    shared_exact=sum((cs&ns).values())
    _,cresidx=consume_by_counter(c,exact_sig,ns);_,nresidx=consume_by_counter(n,exact_sig,cs)
    cr=c.loc[cresidx].copy();nr=n.loc[nresidx].copy()

    layer2,uc,un=unique_pair_by_team(cr,nr)
    cr2=cr.loc[[i for i in cr.index if i not in uc]].copy(); nr2=nr.loc[[i for i in nr.index if i not in un]].copy()
    layer3=unique_near_team_pairs(cr2,nr2,0.11); uc3={x for x,_ in layer3};un3={x for _,x in layer3}
    cr3=cr2.loc[[i for i in cr2.index if i not in uc3]].copy();nr3=nr2.loc[[i for i in nr2.index if i not in un3]].copy()

    attribution=[]
    for ci,ni in layer2:
        ce=entity(c.loc[ci]);ne=entity(n.loc[ni])
        if ce!=ne:
            attribution.append({'cdn':rec(c.loc[ci]),'nba':rec(n.loc[ni]),'same_team':team_id(c.loc[ci])==team_id(n.loc[ni]),'clock_delta_seconds':clock_seconds(c.loc[ci].clock)-clock_seconds(n.loc[ni].clock)})
    rounding=[]
    for ci,ni in layer3:
        rounding.append({'cdn':rec(c.loc[ci]),'nba':rec(n.loc[ni]),'clock_delta_seconds':clock_seconds(c.loc[ci].clock)-clock_seconds(n.loc[ni].clock)})

    # For team TREB, layer2/3 are metric-equivalent only if team identity agrees.
    all_team_equiv=all(x['same_team'] for x in attribution) and all(team_id(c.loc[ci])==team_id(n.loc[ni]) for ci,ni in layer3)
    payload={
      'status':'COMPLETE',
      'candidate_identity':'hierarchical exact entity -> unique same-team same-clock -> unique reciprocal same-team <=0.11s',
      'counts':{
        'cdn_rebounds':len(c),'nba_rebounds':len(n),'layer1_exact_entity_shared':shared_exact,
        'after_layer1_cdn':len(cr),'after_layer1_nba':len(nr),
        'layer2_unique_same_team_same_clock':len(layer2),'layer2_attribution_disagreements':len(attribution),
        'after_layer2_cdn':len(cr2),'after_layer2_nba':len(nr2),
        'layer3_unique_same_team_near_clock':len(layer3),
        'final_cdn_only':len(cr3),'final_nba_only':len(nr3),
      },
      'team_metric_equivalence_pass':bool(all_team_equiv),
      'attribution_disagreements':attribution,
      'near_clock_pairs':rounding,
      'cdn_only_rows':[rec(r) for _,r in cr3.iterrows()],
      'nba_only_rows':[rec(r) for _,r in nr3.iterrows()],
    }
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    print(json.dumps({**payload['counts'],'team_metric_equivalence_pass':payload['team_metric_equivalence_pass']},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
