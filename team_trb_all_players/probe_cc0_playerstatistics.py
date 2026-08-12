#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd


def pick(columns, names):
    lower={str(c).lower():c for c in columns}
    for n in names:
        if n.lower() in lower: return lower[n.lower()]
    return None


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument('--csv',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    d=pd.read_csv(a.csv,low_memory=False)
    cols=list(d.columns)
    game=pick(cols,['gameId','game_id','GAME_ID'])
    player=pick(cols,['personId','playerId','player_id','PERSON_ID','PLAYER_ID'])
    team=pick(cols,['teamId','team_id','TEAM_ID'])
    minutes=pick(cols,['numMinutes','minutes','min','MIN'])
    date=pick(cols,['gameDate','game_date','GAME_DATE'])
    name=pick(cols,['playerName','player_name','name','PLAYER_NAME'])
    season=pick(cols,['season','seasonId','season_id','SEASON_ID'])
    payload={
      'row_count':int(len(d)), 'columns':cols,
      'resolved_columns':{'game':game,'player':player,'team':team,'minutes':minutes,'date':date,'name':name,'season':season},
      'duplicate_game_player_rows':None,
      'zero_or_blank_minute_rows':None,
      'positive_minute_rows':None,
      'sample_zero_or_blank_minute_rows':[],
      'sample_positive_rows':[]
    }
    if game and player:
        payload['duplicate_game_player_rows']=int(d.duplicated([game,player],keep=False).sum())
    if minutes:
        raw=d[minutes]
        num=pd.to_numeric(raw,errors='coerce')
        # Handle common MM:SS strings if needed.
        if num.notna().sum() < len(d)*0.5:
            def parse(v):
                s=str(v).strip()
                if not s or s.lower() in {'nan','none','null'}: return None
                if ':' in s:
                    try:
                        m,sec=s.split(':',1); return float(m)+float(sec)/60.0
                    except Exception: return None
                try:return float(s)
                except Exception:return None
            num=raw.map(parse)
        blank=num.isna() | num.le(0)
        payload['zero_or_blank_minute_rows']=int(blank.sum())
        payload['positive_minute_rows']=int((~blank).sum())
        keep=[c for c in (game,date,season,team,player,name,minutes) if c]
        payload['sample_zero_or_blank_minute_rows']=d.loc[blank,keep].head(20).where(pd.notna(d.loc[blank,keep].head(20)),None).to_dict('records')
        payload['sample_positive_rows']=d.loc[~blank,keep].head(5).where(pd.notna(d.loc[~blank,keep].head(5)),None).to_dict('records')
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    print(json.dumps({k:v for k,v in payload.items() if not k.startswith('sample') and k!='columns'},indent=2))
    return 0
if __name__=='__main__': raise SystemExit(main())
