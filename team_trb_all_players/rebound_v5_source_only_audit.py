#!/usr/bin/env python3
"""Audit only the two outputs needed for V5 source-only rebound residuals.

OREB/DREB is intentionally NOT inferred here: the production engine already uses
PBP Stats possession-level OFFENSIVEREBOUNDS.  This audit asks only:
  1) can a source-only row receive a zero-error lineup anchor?
  2) can its live/dead status be determined by a zero-error PBP rule?

All strategies are validated on already matched rebound controls first.  No
repair is promoted by this script.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
from collections import defaultdict
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_treb_rebuild as core
import production_rebound_v5 as rebound
import production_treb_engine_v3 as lineup_engine
import run_local_treb_production as io

COUNTER_RE = re.compile(r'\(Off:\s*\d+\s+Def:\s*\d+\)', re.I)
BRACKET_RE = re.compile(r'^\s*\[[A-Za-z]{2,4}\]\s*')

def norm(v): return re.sub(r'\s+',' ','' if pd.isna(v) else str(v)).strip().lower()
def name_key(v):
    s=BRACKET_RE.sub('', '' if pd.isna(v) else str(v))
    return norm(s).split(' rebound',1)[0].strip()

def rows_for_game(p):
    x=p.copy(); x['PREV_PBP_DESCRIPTION']=x.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    r=x[x.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    r['START_ELAPSED']=[core.elapsed_seconds(int(q),c) for q,c in zip(r.PERIOD,r.STARTTIME)]
    r['END_ELAPSED']=[core.elapsed_seconds(int(q),c) for q,c in zip(r.PERIOD,r.ENDTIME)]
    r['REBOUND_NUMBER']=r.groupby(core.POSSESSION_ID,dropna=False).cumcount()+1
    oreb=pd.to_numeric(r.OFFENSIVEREBOUNDS,errors='coerce').fillna(0).astype(int)
    r['PBP_IS_OREB']=r.REBOUND_NUMBER.le(oreb)
    return r

def rebounder_map(events):
    d=defaultdict(set)
    for _,r in events[events.EVENTMSGTYPE.eq(4)].iterrows():
        pid=int(r.PLAYER1_ID)
        if 0<pid<core.PLAYER_MAX:
            k=name_key(r.DESCRIPTION_NORM)
            if k:d[k].add(pid)
    return {k:next(iter(v)) for k,v in d.items() if len(v)==1}

def prior_miss(events,row):
    prev=norm(row.PREV_PBP_DESCRIPTION)
    if not prev:return None
    h=events[events.PERIOD.eq(int(row.PERIOD)) & events.DESCRIPTION_NORM.eq(prev) & events.EVENTMSGTYPE.isin([2,3])]
    h=h[h.ELAPSED.ge(int(row.START_ELAPSED)-5)&h.ELAPSED.le(int(row.END_ELAPSED)+5)]
    if len(h)!=1:return None
    i=int(h.index[0]);return tuple(int(x) for x in events.loc[i,'LINEUP'])

def endpoint(events,row,exclude=None,radius=5,max_gap=None):
    t=int(row.END_ELAPSED);ev=events[events.PERIOD.eq(int(row.PERIOD))].sort_values(['ELAPSED','EVENTNUM'],kind='stable')
    if exclude is not None:ev=ev[ev.index!=exclude]
    near=ev[ev.ELAPSED.ge(t-radius)&ev.ELAPSED.le(t+radius)]
    if bool(near.EVENTMSGTYPE.eq(8).any()):return None
    bef=ev[ev.ELAPSED.le(t)];aft=ev[ev.ELAPSED.ge(t)]
    if bef.empty or aft.empty:return None
    a=bef.iloc[-1];b=aft.iloc[0]
    la=tuple(int(x) for x in a.LINEUP);lb=tuple(int(x) for x in b.LINEUP)
    if la!=lb:return None
    if max_gap is not None and max(t-int(a.ELAPSED),int(b.ELAPSED)-t)>max_gap:return None
    return la

def clock_invariant(events,row,exclude=None):
    t=int(row.END_ELAPSED);ev=events[events.PERIOD.eq(int(row.PERIOD))&events.ELAPSED.eq(t)]
    if exclude is not None:ev=ev[ev.index!=exclude]
    if ev.empty or bool(ev.EVENTMSGTYPE.eq(8).any()):return None
    lus={tuple(int(x) for x in x) for x in ev.LINEUP}
    return next(iter(lus)) if len(lus)==1 else None

def interval_invariant(events,row,exclude=None):
    lo=min(int(row.START_ELAPSED),int(row.END_ELAPSED));hi=max(int(row.START_ELAPSED),int(row.END_ELAPSED))
    ev=events[events.PERIOD.eq(int(row.PERIOD))&events.ELAPSED.ge(lo)&events.ELAPSED.le(hi)]
    if exclude is not None:ev=ev[ev.index!=exclude]
    if ev.empty or bool(ev.EVENTMSGTYPE.eq(8).any()):return None
    lus={tuple(int(x) for x in x) for x in ev.LINEUP}
    return next(iter(lus)) if len(lus)==1 else None

def lineup_predictions(events,row,exclude=None):
    miss=prior_miss(events,row)
    ep5=endpoint(events,row,exclude,5,None)
    out={
      'prior_miss_exact':miss,
      'endpoint_gap0':endpoint(events,row,exclude,5,0),
      'endpoint_gap1':endpoint(events,row,exclude,5,1),
      'endpoint_gap2':endpoint(events,row,exclude,5,2),
      'endpoint_gap3':endpoint(events,row,exclude,5,3),
      'endpoint_gap5':endpoint(events,row,exclude,5,5),
      'clock_invariant':clock_invariant(events,row,exclude),
      'interval_invariant':interval_invariant(events,row,exclude),
      'dual_miss_endpoint':miss if miss is not None and ep5 is not None and miss==ep5 else None,
    }
    return out

def live_predictions(row,rmap):
    desc=str(row.DESCRIPTION);key=name_key(desc); resolved=key in rmap
    generic_team=bool(re.search(r'\bteam\s+rebound\b',desc,re.I)) or key in {'mavericks','grizzlies','warriors','jazz','bulls','suns'}
    return {
      'counter_credited_player': True if COUNTER_RE.search(desc) else None,
      'resolved_named_player': True if resolved else None,
      'non_team_named_rebound': True if (not generic_team and key and key!='team') else None,
      'pbp_offensive_rebound': True if bool(row.PBP_IS_OREB) else None,
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--year',type=int,required=True);ap.add_argument('--games',required=True);ap.add_argument('--chunk-id',required=True);ap.add_argument('--nba',type=Path,required=True);ap.add_argument('--v3',type=Path,required=True);ap.add_argument('--pbp',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    ids=[int(x) for x in a.games.split(',') if x]
    nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False));v3=lineup_engine.normalize_v3(pd.read_csv(a.v3,low_memory=False));pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)};vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)};pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    lnames=['prior_miss_exact','endpoint_gap0','endpoint_gap1','endpoint_gap2','endpoint_gap3','endpoint_gap5','clock_invariant','interval_invariant','dual_miss_endpoint']
    rnames=['counter_credited_player','resolved_named_player','non_team_named_rebound','pbp_offensive_rebound']
    lc={s:{'applicable':0,'correct':0,'wrong':0} for s in lnames};rc={s:{'applicable':0,'correct':0,'wrong':0} for s in rnames}
    lwrong={s:[] for s in lnames};rwrong={s:[] for s in rnames};residual=[]
    for gid in ids:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]);events=lu.events;joined,_=rebound.join_pbp_rebounds(lu,pg[gid]);rows=rows_for_game(pg[gid]);rmap=rebounder_map(events)
        for idx,row in rows.iterrows():
            matched=idx in joined.index and pd.notna(joined.loc[idx,'NBA_INDEX'])
            if matched:
                ni=int(joined.loc[idx,'NBA_INDEX']);actual_lu=tuple(int(x) for x in events.loc[ni,'LINEUP']);actual_real=bool(core._nba_real_rebound(events,ni))
                for s,pred in lineup_predictions(events,row,exclude=ni).items():
                    if pred is None:continue
                    lc[s]['applicable']+=1
                    if pred==actual_lu:lc[s]['correct']+=1
                    else:
                        lc[s]['wrong']+=1;lwrong[s].append({'game_id':gid,'pbp_index':int(idx),'description':str(row.DESCRIPTION),'actual':list(actual_lu),'predicted':list(pred)})
                for s,pred in live_predictions(row,rmap).items():
                    if pred is None:continue
                    rc[s]['applicable']+=1
                    if bool(pred)==actual_real:rc[s]['correct']+=1
                    else:
                        rc[s]['wrong']+=1;rwrong[s].append({'game_id':gid,'pbp_index':int(idx),'description':str(row.DESCRIPTION),'actual_real':actual_real,'predicted_real':bool(pred)})
            elif idx not in joined.index:
                lp={k:(None if v is None else list(v)) for k,v in lineup_predictions(events,row,None).items()};rp=live_predictions(row,rmap)
                residual.append({'game_id':gid,'pbp_index':int(idx),'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'description':str(row.DESCRIPTION),'pbp_is_oreb':bool(row.PBP_IS_OREB),'resolved_player_id':rmap.get(name_key(row.DESCRIPTION)),'lineup_predictions':lp,'live_predictions':rp})
    out={'status':'DIAGNOSTIC_ONLY','chunk_id':a.chunk_id,'year':a.year,'lineup_controls':lc,'live_controls':rc,'lineup_wrong_records':lwrong,'live_wrong_records':rwrong,'residual_rows':residual}
    a.output.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({'chunk_id':a.chunk_id,'year':a.year,'lineup_controls':lc,'live_controls':rc,'residual_rows':len(residual)},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
