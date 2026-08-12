#!/usr/bin/env python3
"""Transaction tenure V3: fix known structural V2 event-order/renewal defects.

V2 is preserved unchanged.  This candidate builder makes only generic rule
changes that are independently auditable:
  * same-day departures are processed before same-team acquisitions/renewals;
  * in-season re-sign/rest-of-season/multi-year acquisitions are not discarded
    merely because the player was on the same team last season;
  * extensions remain non-boundary events;
  * every generated tenure must contain at least as many team games as the
    locked core says the player actually appeared in.

The final 14,524 PTS universe is unchanged.  Full/partial/segment counts are
re-derived rather than asserted.  Residual failures are emitted for targeted
boxscore/appearance evidence repair.
"""
from __future__ import annotations

import gzip
import json
from datetime import timedelta
from pathlib import Path

import pandas as pd

import build_roster_targets_v2 as v2

BASE=v2.BASE; IMPACT=v2.IMPACT; ROSTER=v2.ROSTER
OUT=IMPACT/'roster_tenure_v3_hybrid'


def core_games_map()->dict[tuple[str,str,int],int]:
    out={}
    for p in sorted(v2.CORE_OUT.glob('*/player_team_totals.csv.gz')):
        d=pd.read_csv(p,compression='gzip',usecols=['season','team_id','EntityId','GamesPlayed'])
        for r in d.itertuples(index=False):
            out[(str(r.season),str(int(r.EntityId)),int(r.team_id))]=int(r.GamesPlayed)
    return out


def build()->None:
    core=v2.load_core(); assert len(core)==14526,len(core)
    team_games,team_abbr,season_bounds=v2.load_games(); tx=v2.load_transactions(core)
    games_played=core_games_map()

    def events_for(season,pid,team_id):
        s0,e0=season_bounds[season]
        g=tx[(tx.season==season)&(tx.player_id==pid)&tx.valid_name]
        out=[]
        for _,r in g.iterrows():
            try:dt=pd.Timestamp(r.exact_date).date()
            except Exception:continue
            if not(s0<=dt<=e0):continue
            txt=str(r.raw_text).lower();et=r.event_type
            src=None if pd.isna(r.source_team_id) else int(r.source_team_id)
            dst=None if pd.isna(r.destination_team_id) else int(r.destination_team_id)
            if et=='trade':
                if src==team_id:out.append((dt,'out_trade',txt))
                if dst==team_id:out.append((dt,'in_trade',txt))
            elif et in ('acquire','claim'):
                if dst!=team_id:continue
                # Extensions do not establish roster entry.  Re-signings,
                # rest-of-season contracts, conversions and multi-year deals do
                # establish/re-establish entry if the player is currently inactive.
                if 'extension' in txt:continue
                out.append((dt,'in_acquire',txt))
            elif et=='depart' and src==team_id:
                out.append((dt,'out_depart',txt))
        priority={'out_trade':0,'out_depart':0,'in_trade':1,'in_acquire':1}
        dedup=[];seen=set()
        for x in sorted(out,key=lambda z:(z[0],priority.get(z[1],9),z[1],z[2])):
            if x not in seen:seen.add(x);dedup.append(x)
        return dedup

    def intervals_for(row):
        season,pid,team_id=row.season,str(row.player_id),int(row.team_id)
        s0,e0=season_bounds[season];key=(season,pid,team_id)
        if key in v2.MANUAL_INTERVALS:
            spec=v2.MANUAL_INTERVALS[key]
            if spec=='FULL':return [(s0,e0,'manual_verified_full')]
            ans=[]
            for a,b,reason in spec:
                aa=s0 if a=='SEASON_START' else pd.Timestamp(a).date();bb=e0 if b=='SEASON_END' else pd.Timestamp(b).date()
                ans.append((aa,bb,reason))
            return ans
        ev=events_for(season,pid,team_id)
        if not ev:return [(s0,e0,'full_no_inseason_event')]
        active=ev[0][1].startswith('out');cur=s0 if active else None;ints=[]
        for dt,typ,_ in ev:
            if typ.startswith('in'):
                st=dt+timedelta(days=1) if typ=='in_trade' else dt
                if not active:active,cur=True,st
            elif active and cur is not None:
                if cur<=dt:ints.append((cur,dt,typ))
                active,cur=False,None
        if active and cur is not None and cur<=e0:ints.append((cur,e0,'season_close'))
        return v2.merge_intervals(ints)

    rows=[]
    for r in core.itertuples(index=False):
        key=(r.season,str(r.player_id),int(r.team_id))
        if key in v2.ORPHAN_KEYS:continue
        rows.append({'season':r.season,'player_id':str(r.player_id),'player':r.player,'team_id':int(r.team_id),'seconds_on':float(r.seconds),'minutes_on':float(r.seconds)/60.0,'core_games_played':games_played.get(key),'intervals':intervals_for(r)})
    assert len(rows)==14524,len(rows)

    by_ps={}
    for i,row in enumerate(rows):by_ps.setdefault((row['season'],row['player_id']),[]).append(i)
    # Same-date cross-team ordinary handoffs remain non-overlapping.
    for _,idxs in by_ps.items():
        start_map={}
        for j in idxs:
            for seg in rows[j]['intervals']:start_map.setdefault(seg[0],set()).add(j)
        for i in idxs:
            new=[]
            for a,b,reason in rows[i]['intervals']:
                others=start_map.get(b,set())-{i}
                if others and reason!='out_trade':b=b-timedelta(days=1)
                if a<=b:new.append((a,b,reason))
            rows[i]['intervals']=new

    def game_ids(row,seg=None):
        ints=[seg] if seg else row['intervals']
        return [gid for dt,gid in team_games[(row['season'],row['team_id'])] if any(a<=dt<=b for a,b,*_ in ints)]

    failures=[];empty=[];impossible=[]
    for row in rows:
        ids=game_ids(row);row['team_games_in_tenure']=len(ids);row['total_team_games']=len(team_games[(row['season'],row['team_id'])]);row['full_core_reuse']=len(ids)==row['total_team_games']
        cg=row.get('core_games_played')
        if not ids:empty.append((row['season'],row['player_id'],row['team_id']))
        if cg is not None and len(ids)<int(cg):
            failures.append({'season':row['season'],'player_id':row['player_id'],'player':row['player'],'team_id':row['team_id'],'core_games_played':int(cg),'team_games_in_tenure':len(ids),'seconds_on':row['seconds_on'],'intervals':[{'start':str(a),'end':str(b),'reason':reason} for a,b,reason in row['intervals']]})
        if row['minutes_on']>len(ids)*65.0+1:impossible.append((row['season'],row['player_id'],row['team_id'],row['minutes_on'],len(ids)))

    overlaps=[]
    for (season,pid),idxs in by_ps.items():
        for x in range(len(idxs)):
            for y in range(x+1,len(idxs)):
                ar,br=rows[idxs[x]],rows[idxs[y]]
                if ar['team_id']==br['team_id']:continue
                for a,b,*_ in ar['intervals']:
                    for c,d,*_ in br['intervals']:
                        if a<=d and c<=b:overlaps.append((season,pid,ar['team_id'],br['team_id'],str(max(a,c)),str(min(b,d))))

    full=sum(r['full_core_reuse'] for r in rows);partial=len(rows)-full;targets=[]
    for row in rows:
        if row['full_core_reuse']:continue
        gs=[]
        for seg in row['intervals']:
            ids=game_ids(row,seg)
            if ids:gs.append((seg,ids))
        count=len(gs)
        for idx,(seg,ids) in enumerate(gs,1):
            a,b,reason=seg
            targets.append({'season':row['season'],'team_id':row['team_id'],'team_abbr':team_abbr.get((row['season'],row['team_id'])),'player_id':row['player_id'],'player':row['player'],'query_start_date':str(a),'query_end_date':str(b),'team_games_in_window':len(ids),'game_ids':ids,'minutes_on':row['minutes_on'] if count==1 else None,'segment_index':idx,'segment_count':count,'needs_on':bool(count>1),'source':'roster_tenure_v3_hybrid','boundary_reason':reason})

    OUT.mkdir(parents=True,exist_ok=True)
    v2.write_jsonl_gz(OUT/'player_team_season_targets.jsonl.gz',[{**{k:v for k,v in r.items() if k!='intervals'},'intervals':[{'start':str(a),'end':str(b),'reason':reason} for a,b,reason in r['intervals']]} for r in rows])
    v2.write_jsonl_gz(OUT/'wowy_partial_segments.jsonl.gz',targets)
    (OUT/'tenure_game_count_failures.json').write_text(json.dumps(failures,indent=2)+'\n')
    (OUT/'cross_team_overlaps.json').write_text(json.dumps(overlaps,indent=2)+'\n')
    summary={'version':'roster_repair_v3_hybrid','validated_player_team_season_rows':len(rows),'full_core_reuse_rows':int(full),'partial_player_team_season_rows':int(partial),'wowy_game_bearing_segments':len(targets),'tenure_game_count_failures':len(failures),'empty_played_tenures':len(empty),'impossible_minute_rows':len(impossible),'cross_team_overlap_pairs':len(overlaps),'status':'PASS' if not(failures or empty or impossible or overlaps) else 'REPAIR_REQUIRED','changes_from_v2':['same-day departures processed before acquisitions','re-sign/multi-year/rest-of-season acquisitions can reopen inactive tenure','V2 full/partial/segment counts no longer asserted'],'hard_universe_pts':14524}
    (OUT/'roster_repair_v3_hybrid_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':build()
