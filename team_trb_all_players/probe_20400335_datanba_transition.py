#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd

GAME_ID=20400335
PERIOD=2
FOCUS={2747,2454,1924,2365,2424,2437}


def recs(df, cols):
    x=df[[c for c in cols if c in df.columns]].copy()
    return x.where(pd.notna(x),None).to_dict('records')


def clock_seconds(v):
    try:
        m,s=str(v).split(':')[:2]
        return int(m)*60+float(s)
    except Exception:
        return None


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--csv',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    d=pd.read_csv(a.csv,low_memory=False)
    for c in ('GAME_ID','PERIOD','evt','etype','mtype','pid','epid','tid','oftid','ord'):
        if c in d: d[c]=pd.to_numeric(d[c],errors='coerce')
    g=d[(d.GAME_ID==GAME_ID)&(d.PERIOD==PERIOD)].copy()
    if g.empty: raise SystemExit('game missing from data.nba')
    g['clock_seconds']=g.cl.map(clock_seconds)
    cols=['GAME_ID','PERIOD','evt','ord','cl','de','etype','mtype','pid','epid','opid','tid','oftid','clock_seconds']
    focus=g[g.pid.isin(FOCUS)|g.epid.isin(FOCUS) if 'epid' in g else g.pid.isin(FOCUS)]
    subs=g[g.etype.eq(8)]
    around=g[g.clock_seconds.between(420,540,inclusive='both')]
    jr=g[(g.pid==2747)|(g.epid==2747)] if 'epid' in g else g[g.pid==2747]
    payload={
      'columns':list(d.columns),'game_rows':int(len(g)),
      'period_substitutions':recs(subs,cols),
      'focus_player_rows':recs(focus,cols),
      'rows_9_to_7_minutes':recs(around,cols),
      'jr_smith_rows':recs(jr,cols),
      'substitution_field_check':{
         'rows':int(len(subs)),
         'pid_nonnull':int(subs.pid.notna().sum()) if 'pid' in subs else None,
         'epid_nonnull':int(subs.epid.notna().sum()) if 'epid' in subs else None,
      }
    }
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    print(json.dumps({'game_rows':len(g),'subs':len(subs),'focus':len(focus),'jr_rows':len(jr)},indent=2))

if __name__=='__main__': main()
