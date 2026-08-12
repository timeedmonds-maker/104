#!/usr/bin/env python3
"""Validate player rebound counters against independent team/possession identity.

For a player-credited matched rebound, OREB/DREB can be established without the
possession-count classifier: map NBA PLAYER1_ID to his team and compare that
team's abbreviation with PBP Stats OPPONENT. build_exact_game_fact_layer uses
the same OPPONENT semantics for team offense/defense masks.
"""
from __future__ import annotations
import argparse,json,re,sys
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import local_treb_rebuild as core
import production_rebound_v4 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io
from build_exact_game_fact_layer import _team_abbreviations

COUNTER_RE=re.compile(r'^(.*?)\s+REBOUND\s+\(Off:(\d+) Def:(\d+)\)',re.I)
def norm(v): return re.sub(r'\s+',' ','' if pd.isna(v) else str(v)).strip().lower()
def rows_for(p):
    x=p.copy(); x['_SOURCE_INDEX']=range(len(x)); x['PREV_PBP_DESCRIPTION']=x.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    r=x[x.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    r['END_ELAPSED']=[core.elapsed_seconds(int(period),clock) for period,clock in zip(r.PERIOD,r.ENDTIME)]
    return r

def counter_kind_map(rows):
    prev={}; out={}
    ordered=rows.sort_values(['END_ELAPSED','_SOURCE_INDEX'],kind='stable')
    for idx,row in ordered.iterrows():
        m=COUNTER_RE.search(str(row.DESCRIPTION))
        if not m: continue
        key=norm(m.group(1)); off=int(m.group(2)); de=int(m.group(3)); po,pd=prev.get(key,(0,0)); kind=None
        if off==po+1 and de==pd: kind='OREB'
        elif de==pd+1 and off==po: kind='DREB'
        if off>=po and de>=pd: prev[key]=(off,de)
        out[idx]={'kind':kind,'off':off,'def':de,'prev_off':po,'prev_def':pd}
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--games',required=True); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--chunk-id',required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    ids=[int(x) for x in a.games.split(',') if x]; y=a.year
    nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False)); v3=lineup_engine.normalize_v3(pd.read_csv(a.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    totals={k:0 for k in ['applicable','counter_vs_team_correct','counter_vs_team_wrong','locked_vs_team_correct','locked_vs_team_wrong','counter_vs_locked_correct','counter_vs_locked_wrong']}; mismatches=[]
    for gid in ids:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]); joined,_=rebound.join_pbp_rebounds(lu,pg[gid]); rows=rows_for(pg[gid]); kinds=counter_kind_map(rows); locked=core.classify_rebounds(joined.copy()); pteam=core._player_team(ng[gid]); abbr=_team_abbreviations(ng[gid])
        for idx,j in joined.iterrows():
            if idx not in rows.index or pd.isna(j.NBA_INDEX): continue
            rec=kinds.get(idx)
            if not rec or rec['kind'] not in {'OREB','DREB'}: continue
            ni=int(j.NBA_INDEX); pid=int(lu.events.loc[ni,'PLAYER1_ID'])
            tid=pteam.get(pid)
            if tid is None or int(tid) not in abbr: continue
            team_abbr=abbr[int(tid)]; opponent=str(rows.loc[idx].OPPONENT)
            team_kind='DREB' if opponent==team_abbr else 'OREB'
            actual_real=bool(core._nba_real_rebound(lu.events,ni))
            if not actual_real: continue
            locked_kind='OREB' if bool(locked.loc[idx].IS_OREB) else 'DREB'
            totals['applicable']+=1
            if rec['kind']==team_kind: totals['counter_vs_team_correct']+=1
            else: totals['counter_vs_team_wrong']+=1
            if locked_kind==team_kind: totals['locked_vs_team_correct']+=1
            else: totals['locked_vs_team_wrong']+=1
            if rec['kind']==locked_kind: totals['counter_vs_locked_correct']+=1
            else:
                totals['counter_vs_locked_wrong']+=1
                mismatches.append({'game_id':gid,'pbp_index':int(idx),'description':str(rows.loc[idx].DESCRIPTION),'counter_kind':rec['kind'],'team_possession_kind':team_kind,'locked_kind':locked_kind,'player_id':pid,'player_team_abbr':team_abbr,'opponent':opponent,'nba_eventnum':int(lu.events.loc[ni,'EVENTNUM']),'nba_elapsed':int(lu.events.loc[ni,'ELAPSED'])})
    out={'chunk_id':a.chunk_id,'year':y,'totals':totals,'counter_locked_mismatches':mismatches}; a.output.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps({'chunk_id':a.chunk_id,'year':y,'totals':totals,'mismatches':len(mismatches)},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
