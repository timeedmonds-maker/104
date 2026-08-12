#!/usr/bin/env python3
"""Audit exact rebounder identity + exact missed-shooter team + dual lineup anchors.

Only the strongest finite evidence is considered:
- rebounder name key must map to exactly one NBA PLAYER1_ID among this game's
  actual rebound events (no surname alias fallback);
- previous PBP miss description must map to exactly one NBA miss/FT event inside
  the possession time window;
- rebound type is structural: rebounder team == shooter team => OREB, else DREB;
- missed-shot lineup and endpoint-bracket lineup must agree;
- endpoint bracket vetoes substitutions within +/-5 seconds.
Matched rows provide independent controls for player identity, rebound type, and
lineup attribution before any residual row can be promoted.
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

def norm(v): return re.sub(r'\s+',' ','' if pd.isna(v) else str(v)).strip().lower()
def name_key(desc): return norm(desc).split(' rebound',1)[0].strip()
def make_rows(p):
    x=p.copy(); x['PREV_PBP_DESCRIPTION']=x.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    r=x[x.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    r['START_ELAPSED']=[core.elapsed_seconds(int(period),clock) for period,clock in zip(r.PERIOD,r.STARTTIME)]
    r['END_ELAPSED']=[core.elapsed_seconds(int(period),clock) for period,clock in zip(r.PERIOD,r.ENDTIME)]
    return r

def exact_rebounder_map(events):
    d={}
    for _,r in events[events.EVENTMSGTYPE.eq(4)].iterrows():
        pid=int(r.PLAYER1_ID); k=name_key(r.DESCRIPTION_NORM)
        if 0<pid<core.PLAYER_MAX and k:d.setdefault(k,set()).add(pid)
    return {k:next(iter(v)) for k,v in d.items() if len(v)==1}

def prior_miss(events,row):
    prev=norm(row.PREV_PBP_DESCRIPTION)
    if not prev:return None
    h=events[events.PERIOD.eq(row.PERIOD)&events.DESCRIPTION_NORM.eq(prev)&events.EVENTMSGTYPE.isin([2,3])]
    h=h[h.ELAPSED.ge(int(row.START_ELAPSED)-5)&h.ELAPSED.le(int(row.END_ELAPSED)+5)]
    if len(h)!=1:return None
    i=int(h.index[0]); pid=int(events.loc[i,'PLAYER1_ID'])
    return {'nba_index':i,'eventnum':int(events.loc[i,'EVENTNUM']),'elapsed':int(events.loc[i,'ELAPSED']),'description':str(events.loc[i,'DESCRIPTION_NORM']),'shooter_id':pid,'lineup':tuple(int(x) for x in events.loc[i,'LINEUP'])}

def endpoint(events,row,exclude=None,radius=5):
    t=int(row.END_ELAPSED); period=int(row.PERIOD)
    ev=events[events.PERIOD.eq(period)].sort_values(['ELAPSED','EVENTNUM'],kind='stable')
    if exclude is not None:ev=ev[ev.index!=exclude]
    if bool(ev[ev.ELAPSED.ge(t-radius)&ev.ELAPSED.le(t+radius)].EVENTMSGTYPE.eq(8).any()):return None
    bef=ev[ev.ELAPSED.le(t)]; aft=ev[ev.ELAPSED.ge(t)]
    if bef.empty or aft.empty:return None
    a=tuple(int(x) for x in bef.iloc[-1].LINEUP); b=tuple(int(x) for x in aft.iloc[0].LINEUP)
    return a if a==b else None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--games',required=True); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--chunk-id',required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    ids=[int(x) for x in a.games.split(',') if x]; y=a.year
    nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False)); v3=lineup_engine.normalize_v3(pd.read_csv(a.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    keys=['resolver_applicable','resolver_correct','resolver_wrong','kind_applicable','kind_correct','kind_wrong','dual_lineup_applicable','dual_lineup_correct','dual_lineup_wrong','full_rule_applicable','full_rule_correct','full_rule_wrong']
    c={k:0 for k in keys}; residual=[]
    for gid in ids:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]); joined,_=rebound.join_pbp_rebounds(lu,pg[gid]); events=lu.events; rows=make_rows(pg[gid]); rmap=exact_rebounder_map(events); pteam=core._player_team(ng[gid])
        for idx,row in rows.iterrows():
            rid=rmap.get(name_key(row.DESCRIPTION)); miss=prior_miss(events,row); ep=None
            if idx in joined.index and pd.notna(joined.loc[idx,'NBA_INDEX']):
                ni=int(joined.loc[idx,'NBA_INDEX']); actual_rid=int(events.loc[ni,'PLAYER1_ID'])
                if rid is not None and 0<actual_rid<core.PLAYER_MAX:
                    c['resolver_applicable']+=1
                    if rid==actual_rid:c['resolver_correct']+=1
                    else:c['resolver_wrong']+=1
                if rid is None or miss is None:continue
                rtid=pteam.get(rid); stid=pteam.get(int(miss['shooter_id']))
                if rtid is None or stid is None:continue
                predicted_kind='OREB' if int(rtid)==int(stid) else 'DREB'
                actual_real=bool(core._nba_real_rebound(events,ni))
                if not actual_real:continue
                # Structural truth from the actual matched NBA rebounder and actual missed shooter.
                artid=pteam.get(actual_rid); astid=pteam.get(int(miss['shooter_id']))
                if artid is None or astid is None:continue
                actual_kind='OREB' if int(artid)==int(astid) else 'DREB'
                c['kind_applicable']+=1
                if predicted_kind==actual_kind:c['kind_correct']+=1
                else:c['kind_wrong']+=1
                ep=endpoint(events,row,exclude=ni); actual_lineup=tuple(int(x) for x in events.loc[ni,'LINEUP'])
                if ep is not None and ep==miss['lineup']:
                    c['dual_lineup_applicable']+=1
                    if ep==actual_lineup:c['dual_lineup_correct']+=1
                    else:c['dual_lineup_wrong']+=1
                    c['full_rule_applicable']+=1
                    if rid==actual_rid and predicted_kind==actual_kind and ep==actual_lineup:c['full_rule_correct']+=1
                    else:c['full_rule_wrong']+=1
            elif idx not in joined.index:
                ep=endpoint(events,row,exclude=None)
                rtid=pteam.get(rid) if rid is not None else None; stid=pteam.get(int(miss['shooter_id'])) if miss is not None else None
                kind=None
                if rtid is not None and stid is not None:kind='OREB' if int(rtid)==int(stid) else 'DREB'
                residual.append({'game_id':gid,'pbp_index':int(idx),'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'description':str(row.DESCRIPTION),'previous_description':'' if pd.isna(row.PREV_PBP_DESCRIPTION) else str(row.PREV_PBP_DESCRIPTION),'resolved_rebounder_id':rid,'rebounder_team_id':int(rtid) if rtid is not None else None,'prior_miss':None if miss is None else {'eventnum':miss['eventnum'],'elapsed':miss['elapsed'],'description':miss['description'],'shooter_id':miss['shooter_id'],'shooter_team_id':int(stid) if stid is not None else None,'lineup':list(miss['lineup'])},'team_kind':kind,'endpoint_lineup':list(ep) if ep is not None else None,'anchors_agree':bool(ep is not None and miss is not None and ep==miss['lineup'])})
    out={'chunk_id':a.chunk_id,'year':y,'controls':c,'residual_rows':residual}; a.output.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps({'chunk_id':a.chunk_id,'year':y,'controls':c,'residual_rows':len(residual)},indent=2)); return 0
if __name__=='__main__':raise SystemExit(main())
