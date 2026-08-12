#!/usr/bin/env python3
"""Resolve rebound source gaps from direct player-team and lineup evidence.

Player identity is resolved only from game-local NBA rebound description keys or
unique game-local surname aliases and validated on matched rebound truth. OREB /
DREB is then structural: compare the rebounder's team abbreviation with PBP
Stats OPPONENT. Lineup is independently anchored by the exact prior miss and/or
by the rebound endpoint chronology with a substitution veto.
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
from build_exact_game_fact_layer import _team_abbreviations,_player_names

PLAYER_MAX=core.PLAYER_MAX
def norm(v): return re.sub(r'\s+',' ','' if pd.isna(v) else str(v)).strip().lower()
def name_key(desc): return norm(desc).split(' rebound',1)[0].strip()
def make_rows(p):
    x=p.copy(); x['PREV_PBP_DESCRIPTION']=x.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    r=x[x.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    r['START_ELAPSED']=[core.elapsed_seconds(int(period),clock) for period,clock in zip(r.PERIOD,r.STARTTIME)]
    r['END_ELAPSED']=[core.elapsed_seconds(int(period),clock) for period,clock in zip(r.PERIOD,r.ENDTIME)]
    return r

def resolver(events,nba_game):
    by_event={}
    for _,r in events[events.EVENTMSGTYPE.eq(4)].iterrows():
        pid=int(r.PLAYER1_ID)
        if not (0<pid<PLAYER_MAX): continue
        k=name_key(r.DESCRIPTION_NORM)
        if k: by_event.setdefault(k,set()).add(pid)
    names=_player_names(nba_game); by_alias={}
    for pid,full in names.items():
        f=norm(full); aliases={f}
        parts=f.replace('.','').split()
        if parts: aliases.add(parts[-1])
        # Common suffix-safe surname alias.
        if parts and parts[-1] in {'jr','sr','ii','iii','iv'} and len(parts)>1: aliases.add(parts[-2])
        for a in aliases:
            if a: by_alias.setdefault(a,set()).add(int(pid))
    def resolve(desc):
        k=name_key(desc)
        s=by_event.get(k,set())
        if len(s)==1:return next(iter(s)),'nba_rebound_name_key'
        s=by_alias.get(k,set())
        if len(s)==1:return next(iter(s)),'unique_game_name_alias'
        return None,None
    return resolve

def endpoint(events,period,t,exclude=None,radius=5):
    ev=events[events.PERIOD.eq(period)].sort_values(['ELAPSED','EVENTNUM'],kind='stable')
    if exclude is not None: ev=ev[ev.index!=exclude]
    if bool(ev[ev.ELAPSED.ge(t-radius)&ev.ELAPSED.le(t+radius)].EVENTMSGTYPE.eq(8).any()): return None,'near_substitution'
    bef=ev[ev.ELAPSED.le(t)]; aft=ev[ev.ELAPSED.ge(t)]
    if bef.empty or aft.empty:return None,'missing_bracket'
    a=tuple(int(x) for x in bef.iloc[-1].LINEUP); b=tuple(int(x) for x in aft.iloc[0].LINEUP)
    if a!=b:return None,'bracket_disagreement'
    return a,'bracket_agreement_no_near_sub'

def prior_miss(events,row):
    prev=norm(row.PREV_PBP_DESCRIPTION)
    if not prev:return None,None
    h=events[events.PERIOD.eq(row.PERIOD)&events.DESCRIPTION_NORM.eq(prev)&events.EVENTMSGTYPE.isin([2,3])]
    # Strong time constraint: the missed source event must live inside this PBP possession window (+/-5s source drift).
    h=h[h.ELAPSED.ge(int(row.START_ELAPSED)-5)&h.ELAPSED.le(int(row.END_ELAPSED)+5)]
    if len(h)!=1:return None,None
    i=int(h.index[0]); return tuple(int(x) for x in events.loc[i,'LINEUP']),{'eventnum':int(events.loc[i,'EVENTNUM']),'elapsed':int(events.loc[i,'ELAPSED']),'description':str(events.loc[i,'DESCRIPTION_NORM']),'eventmsgtype':int(events.loc[i,'EVENTMSGTYPE'])}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--games',required=True); ap.add_argument('--nba',type=Path,required=True); ap.add_argument('--v3',type=Path,required=True); ap.add_argument('--pbp',type=Path,required=True); ap.add_argument('--chunk-id',required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    ids=[int(x) for x in a.games.split(',') if x]; y=a.year
    nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False)); v3=lineup_engine.normalize_v3(pd.read_csv(a.v3,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False))
    ng={int(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)}; vg={int(g):f.copy() for g,f in v3.groupby('gameId',sort=False)}; pg={int(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    keys=['resolver_applicable','resolver_correct','resolver_wrong','player_real_applicable','player_real_correct','player_real_wrong','dreb_endpoint_applicable','dreb_endpoint_correct','dreb_endpoint_wrong','dreb_miss_applicable','dreb_miss_correct','dreb_miss_wrong','dreb_both_applicable','dreb_both_correct','dreb_both_wrong','oreb_endpoint_applicable','oreb_endpoint_correct','oreb_endpoint_wrong','oreb_miss_applicable','oreb_miss_correct','oreb_miss_wrong','oreb_both_applicable','oreb_both_correct','oreb_both_wrong']
    c={k:0 for k in keys}; residual=[]
    for gid in ids:
        lu=lineup_engine.reconstruct_game_lineups(ng[gid],vg[gid]); joined,_=rebound.join_pbp_rebounds(lu,pg[gid]); events=lu.events; rows=make_rows(pg[gid]); resolve=resolver(events,ng[gid]); pteam=core._player_team(ng[gid]); abbr=_team_abbreviations(ng[gid])
        for idx,row in rows.iterrows():
            pid,method=resolve(row.DESCRIPTION)
            if idx in joined.index and pd.notna(joined.loc[idx,'NBA_INDEX']):
                ni=int(joined.loc[idx,'NBA_INDEX']); actual_pid=int(events.loc[ni,'PLAYER1_ID'])
                if 0<actual_pid<PLAYER_MAX and pid is not None:
                    c['resolver_applicable']+=1
                    if pid==actual_pid:c['resolver_correct']+=1
                    else:c['resolver_wrong']+=1
                if not (0<actual_pid<PLAYER_MAX): continue
                actual_real=bool(core._nba_real_rebound(events,ni)); c['player_real_applicable']+=1
                if actual_real:c['player_real_correct']+=1
                else:c['player_real_wrong']+=1
                tid=pteam.get(actual_pid)
                if tid is None or int(tid) not in abbr or not actual_real: continue
                team_kind='DREB' if str(row.OPPONENT)==abbr[int(tid)] else 'OREB'; actual=tuple(int(x) for x in events.loc[ni,'LINEUP'])
                ep,_=endpoint(events,int(row.PERIOD),int(row.END_ELAPSED),exclude=ni); miss,_=prior_miss(events,row)
                prefix=team_kind.lower()
                if ep is not None:
                    c[prefix+'_endpoint_applicable']+=1
                    if ep==actual:c[prefix+'_endpoint_correct']+=1
                    else:c[prefix+'_endpoint_wrong']+=1
                if miss is not None:
                    c[prefix+'_miss_applicable']+=1
                    if miss==actual:c[prefix+'_miss_correct']+=1
                    else:c[prefix+'_miss_wrong']+=1
                if ep is not None and miss is not None and ep==miss:
                    c[prefix+'_both_applicable']+=1
                    if ep==actual:c[prefix+'_both_correct']+=1
                    else:c[prefix+'_both_wrong']+=1
            elif idx not in joined.index:
                ep,ep_reason=endpoint(events,int(row.PERIOD),int(row.END_ELAPSED),exclude=None); miss,miss_source=prior_miss(events,row)
                tid=pteam.get(pid) if pid is not None else None; team_abbr=abbr.get(int(tid)) if tid is not None else None
                kind=None
                if team_abbr is not None: kind='DREB' if str(row.OPPONENT)==team_abbr else 'OREB'
                residual.append({'game_id':gid,'pbp_index':int(idx),'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'description':str(row.DESCRIPTION),'previous_description':'' if pd.isna(row.PREV_PBP_DESCRIPTION) else str(row.PREV_PBP_DESCRIPTION),'resolved_player_id':pid,'resolver_method':method,'player_team_abbr':team_abbr,'opponent':str(row.OPPONENT),'team_kind':kind,'endpoint_lineup':list(ep) if ep is not None else None,'endpoint_reason':ep_reason,'prior_miss_lineup':list(miss) if miss is not None else None,'prior_miss_source':miss_source,'anchors_agree':bool(ep is not None and miss is not None and ep==miss)})
    out={'chunk_id':a.chunk_id,'year':y,'controls':c,'residual_rows':residual}; a.output.write_text(json.dumps(out,indent=2)+'\n'); print(json.dumps({'chunk_id':a.chunk_id,'year':y,'controls':c,'residual_rows':len(residual)},indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
