#!/usr/bin/env python3
"""Audit 2025-26 CDN vs NBA v3 rebounds using source-native action identity.

For rows sharing (gameId, actionNumber), quantify team, player and clock agreement.
Action-number-only residuals are emitted explicitly.  This audit does not alter
production; its purpose is to determine whether cross-feed differences are
metric-material for team TREB.
"""
from __future__ import annotations
import argparse,json,re
from pathlib import Path
import pandas as pd
PLAYER_MAX=1610612737
ISO=re.compile(r"PT(?:(\d+)M)?([0-9.]+)S",re.I)
def clock(v):
    m=ISO.fullmatch(str(v).strip())
    if not m:raise ValueError(v)
    return 60*int(m.group(1) or 0)+float(m.group(2))
def num(v):
    x=pd.to_numeric(pd.Series([v]),errors='coerce').iloc[0];return None if pd.isna(x) else int(x)
def team(r):
    t=num(r.get('teamId'))
    if t and t>0:return t
    p=num(r.get('personId'))
    if p and p>=PLAYER_MAX:return p
    return None
def player(r):
    p=num(r.get('personId'));return p if p and 0<p<PLAYER_MAX else None
def rec(r):
    o={}
    for k in ['gameId','actionNumber','orderNumber','period','clock','actionType','subType','description','personId','playerName','teamId','teamTricode','reboundOffensiveTotal','reboundDefensiveTotal']:
        if k in r.index:
            v=r[k];o[k]=None if pd.isna(v) else (v.item() if hasattr(v,'item') else v)
    return o
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cdn',type=Path,required=True);ap.add_argument('--nba',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    c=pd.read_csv(a.cdn,low_memory=False);n=pd.read_csv(a.nba,low_memory=False)
    for f in (c,n):
        f['gameId']=pd.to_numeric(f.gameId,errors='raise').astype('int64');f['actionNumber']=pd.to_numeric(f.actionNumber,errors='raise').astype('int64');f['_a']=f.actionType.astype('string').fillna('').str.lower()
    c=c[c._a.eq('rebound')].copy();n=n[n._a.eq('rebound')].copy();key=['gameId','actionNumber']
    assert not c.duplicated(key).any();assert not n.duplicated(key).any()
    m=c.merge(n,on=key,how='outer',suffixes=('_cdn','_nba'),indicator=True,validate='one_to_one')
    both=m[m._merge.eq('both')].copy();left=m[m._merge.eq('left_only')].copy();right=m[m._merge.eq('right_only')].copy()
    detail=[];team_conf=[];clockdiff=[];playerdiff=[]
    for _,r in both.iterrows():
        cr=pd.Series({k[:-4]:v for k,v in r.items() if k.endswith('_cdn')});nr=pd.Series({k[:-4]:v for k,v in r.items() if k.endswith('_nba')})
        # restore key fields excluded from suffixing
        for rr in (cr,nr):rr['gameId']=r.gameId;rr['actionNumber']=r.actionNumber
        ct,nt=team(cr),team(nr);cp,np=player(cr),player(nr);dt=clock(cr.clock)-clock(nr.clock)
        d={'gameId':int(r.gameId),'actionNumber':int(r.actionNumber),'cdn_team':ct,'nba_team':nt,'same_team':ct==nt,'cdn_player':cp,'nba_player':np,'same_player':cp==np,'clock_delta_seconds':dt,'cdn':rec(cr),'nba':rec(nr)}
        if ct!=nt:team_conf.append(d)
        if cp!=np:playerdiff.append(d)
        if abs(dt)>0.011:clockdiff.append(d)
    def one_side(df,side):
        out=[]
        suf='_cdn' if side=='cdn' else '_nba'
        for _,r in df.iterrows():
            rr=pd.Series({k[:-4]:v for k,v in r.items() if k.endswith(suf)});rr['gameId']=r.gameId;rr['actionNumber']=r.actionNumber;out.append(rec(rr))
        return out
    counts={'cdn_rebounds':len(c),'nba_rebounds':len(n),'shared_action_identity':len(both),'cdn_only_actions':len(left),'nba_only_actions':len(right),'shared_same_team':len(both)-len(team_conf),'shared_team_conflicts':len(team_conf),'shared_player_attribution_differences':len(playerdiff),'shared_clock_differences_gt_0_011s':len(clockdiff),'shared_clock_delta_max_abs':max((abs(x['clock_delta_seconds']) for x in clockdiff),default=0)}
    out={'status':'COMPLETE','counts':counts,'team_conflicts':team_conf,'player_attribution_differences':playerdiff,'clock_differences':clockdiff,'cdn_only_rows':one_side(left,'cdn'),'nba_only_rows':one_side(right,'nba')}
    a.output.write_text(json.dumps(out,indent=2,default=str)+'\n');print(json.dumps(counts,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
