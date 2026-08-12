#!/usr/bin/env python3
"""Build and audit game-exact roster tenures from CC0 PlayerStatistics boxscores.

Why this exists
---------------
The transaction-derived V2 model can mis-handle rescinded trades, same-day
10-day renewals/rest-of-season conversions, and two-way continuity.  The CC0
PlayerStatistics dataset retains game-player boxscore rows including blank/DNP
minutes.  Joined to our already-locked regular-season schedule, those rows give
an independent game-level roster-presence ledger.

This stage is deliberately diagnostic/fail-closed.  It does not overwrite V2.
It writes a V3 candidate universe plus reconciliation evidence against the
locked historical core.  Promotion to production requires the audit gates in
its summary to pass or any residual exceptions to be explicitly resolved.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from collections import Counter

import pandas as pd

BASE=Path(__file__).resolve().parent
IMPACT=BASE/'impact_database'
SCHEDULE_DIR=IMPACT/'roster_tenure'/'regular_season_games_raw'
CORE_OUT=IMPACT/'outputs'
OUT=IMPACT/'roster_tenure_v3'
ORPHAN_KEYS={('2000-01','145',1610612747),('2003-04','1917',1610612746)}


def write_jsonl_gz(path:Path,rows:list[dict])->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,'wt',encoding='utf-8') as f:
        for row in rows:f.write(json.dumps(row,separators=(',',':'),default=str)+'\n')


def load_schedule():
    game_rows=[]; team_games={}; team_abbr={}
    for p in sorted(SCHEDULE_DIR.glob('*.json.gz')):
        season=p.name.replace('.json.gz','')
        if season<'2000-01' or season>'2025-26': continue
        with gzip.open(p,'rt',encoding='utf-8') as f:data=json.load(f)
        for g in data['results']:
            gid=int(g['GameId']); dt=str(pd.Timestamp(g['Date']).date())
            home=int(g['HomeTeamId']); away=int(g['AwayTeamId'])
            game_rows.append({'season':season,'game_id':gid,'game_date':dt,'home_team_id':home,'away_team_id':away})
            for tid,abbr in ((home,g['HomeTeamAbbreviation']),(away,g['AwayTeamAbbreviation'])):
                team_games.setdefault((season,tid),[]).append((dt,gid))
                team_abbr[(season,tid)]=str(abbr)
    for key in team_games: team_games[key].sort(key=lambda x:(x[0],x[1]))
    schedule=pd.DataFrame(game_rows).drop_duplicates('game_id')
    return schedule,team_games,team_abbr


def load_core()->pd.DataFrame:
    parts=[]
    for p in sorted(CORE_OUT.glob('*/player_team_totals.csv.gz')):
        d=pd.read_csv(p,compression='gzip',usecols=['season','team_id','EntityId','Name','SecondsPlayed','GamesPlayed','TeamAbbreviation'])
        d=d.rename(columns={'EntityId':'player_id','Name':'player','SecondsPlayed':'core_seconds','GamesPlayed':'core_games','TeamAbbreviation':'team_abbr'})
        parts.append(d)
    core=pd.concat(parts,ignore_index=True)
    core['player_id']=pd.to_numeric(core.player_id,errors='raise').astype('int64').astype(str)
    core['team_id']=pd.to_numeric(core.team_id,errors='raise').astype('int64')
    core['core_seconds']=pd.to_numeric(core.core_seconds,errors='coerce').fillna(0).round().astype('int64')
    core['core_games']=pd.to_numeric(core.core_games,errors='coerce').fillna(0).astype('int64')
    core=core[core.core_seconds.gt(0)].copy()
    if len(core)!=14526: raise RuntimeError(f'expected 14526 positive core PTS, got {len(core)}')
    return core


def consecutive_segments(game_list:list[tuple[str,int]],present:set[int])->list[list[tuple[str,int]]]:
    segs=[]; cur=[]
    for dt,gid in game_list:
        if gid in present:
            if cur and game_list.index((dt,gid))<0: pass
            cur.append((dt,gid))
        elif cur:
            segs.append(cur); cur=[]
    if cur:segs.append(cur)
    return segs


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--playerstatistics',type=Path,required=True); ap.add_argument('--out',type=Path,default=OUT); a=ap.parse_args()
    schedule,team_games,team_abbr=load_schedule(); core=load_core()
    sched_ids=set(schedule.game_id.astype(int))
    use=['personId','gameId','gameDate','numMinutes','playerteamId','comment','firstName','lastName']
    ps=pd.read_csv(a.playerstatistics,usecols=lambda c:c in use,low_memory=False)
    required={'personId','gameId','numMinutes','playerteamId'}
    if not required.issubset(ps.columns): raise RuntimeError(f'missing PlayerStatistics columns {required-set(ps.columns)}')
    ps['game_id']=pd.to_numeric(ps.gameId,errors='coerce').astype('Int64')
    ps=ps[ps.game_id.isin(sched_ids)].copy()
    ps['player_id_num']=pd.to_numeric(ps.personId,errors='coerce').astype('Int64')
    ps['team_id']=pd.to_numeric(ps.playerteamId,errors='coerce').astype('Int64')
    ps=ps[ps.player_id_num.notna() & ps.team_id.notna()].copy()
    ps['player_id']=ps.player_id_num.astype('int64').astype(str)
    ps['team_id']=ps.team_id.astype('int64')
    ps['game_id']=ps.game_id.astype('int64')
    ps['minutes']=pd.to_numeric(ps.numMinutes,errors='coerce')
    ps['seconds_game']=(ps.minutes.fillna(0)*60.0).round().astype('int64')
    ps=ps.merge(schedule[['game_id','season','game_date','home_team_id','away_team_id']],on='game_id',how='left',validate='many_to_one')
    ps['team_in_game']=(ps.team_id.eq(ps.home_team_id)|ps.team_id.eq(ps.away_team_id))
    wrong_team=int((~ps.team_in_game).sum())
    if wrong_team: raise RuntimeError(f'{wrong_team} PlayerStatistics rows have playerteamId not in scheduled game')
    if ps.duplicated(['game_id','player_id']).any(): raise RuntimeError('duplicate game-player rows after regular-season filter')

    # Compact durable ledger; every row is explicit official boxscore presence.
    ledger_cols=['season','game_id','game_date','team_id','player_id','seconds_game']
    if 'comment' in ps.columns: ledger_cols.append('comment')
    ledger=ps[ledger_cols].sort_values(['season','game_date','game_id','team_id','player_id']).copy()
    a.out.mkdir(parents=True,exist_ok=True)
    ledger.to_csv(a.out/'player_game_roster_ledger.csv.gz',index=False,compression='gzip')

    positive=(ps[ps.seconds_game.gt(0)].groupby(['season','team_id','player_id'],as_index=False)
              .agg(boxscore_positive_games=('game_id','nunique'),boxscore_seconds=('seconds_game','sum')))
    roster=(ps.groupby(['season','team_id','player_id'],as_index=False)
            .agg(boxscore_roster_games=('game_id','nunique'),first_roster_date=('game_date','min'),last_roster_date=('game_date','max')))
    recon=core.merge(positive,on=['season','team_id','player_id'],how='left',validate='one_to_one').merge(roster,on=['season','team_id','player_id'],how='left',validate='one_to_one')
    for c in ('boxscore_positive_games','boxscore_seconds','boxscore_roster_games'):recon[c]=recon[c].fillna(0).astype('int64')
    recon['games_diff']=recon.boxscore_positive_games-recon.core_games
    recon['seconds_diff']=recon.boxscore_seconds-recon.core_seconds
    recon['valid_final_pts']=[(s,p,int(t)) not in ORPHAN_KEYS for s,p,t in zip(recon.season,recon.player_id,recon.team_id)]
    recon.to_csv(a.out/'core_boxscore_reconciliation.csv.gz',index=False,compression='gzip')

    valid_core=recon[recon.valid_final_pts].copy()
    valid_keys=set(zip(valid_core.season,valid_core.player_id,valid_core.team_id))
    ps_by_key={(s,p,int(t)):g for (s,t,p),g in ps.groupby(['season','team_id','player_id'],sort=False)}
    targets=[]; segments=[]; missing=[]
    for r in valid_core.itertuples(index=False):
        key=(r.season,r.player_id,int(r.team_id)); game_list=team_games.get((r.season,int(r.team_id)),[])
        g=ps_by_key.get(key)
        if g is None or g.empty:
            missing.append(key); continue
        present=set(g.game_id.astype(int))
        segs=[];cur=[]
        for dt,gid in game_list:
            if gid in present:
                cur.append((dt,gid))
            elif cur:
                segs.append(cur);cur=[]
        if cur:segs.append(cur)
        total_team_games=len(game_list); roster_games=len(present)
        full=roster_games==total_team_games and len(segs)==1
        row={
          'season':r.season,'team_id':int(r.team_id),'team_abbr':team_abbr.get((r.season,int(r.team_id)),r.team_abbr),
          'player_id':r.player_id,'player':r.player,'core_seconds_on':int(r.core_seconds),'core_games_played':int(r.core_games),
          'roster_games':roster_games,'total_team_games':total_team_games,'segment_count':len(segs),'full_core_reuse_candidate':bool(full),
          'game_ids':[gid for _,gid in game_list if gid in present],
        }
        targets.append(row)
        for i,seg in enumerate(segs,1):
            ids=[gid for _,gid in seg]
            sg=g[g.game_id.isin(ids)]
            segments.append({
              'season':r.season,'team_id':int(r.team_id),'team_abbr':row['team_abbr'],'player_id':r.player_id,'player':r.player,
              'segment_index':i,'segment_count':len(segs),'query_start_date':seg[0][0],'query_end_date':seg[-1][0],
              'team_games_in_window':len(ids),'game_ids':ids,'positive_games_in_window':int(sg.seconds_game.gt(0).sum()),
              'expected_seconds_on':int(sg.seconds_game.sum()),'source':'cc0_playerstatistics_boxscore_roster_v3'
            })
    if missing: raise RuntimeError(f'valid core PTS absent from PlayerStatistics roster rows: {missing[:20]} total={len(missing)}')
    if len(targets)!=14524: raise RuntimeError(f'expected 14524 V3 PTS targets, got {len(targets)}')
    write_jsonl_gz(a.out/'player_team_season_targets.jsonl.gz',targets)
    write_jsonl_gz(a.out/'roster_segments.jsonl.gz',segments)

    full=sum(bool(r['full_core_reuse_candidate']) for r in targets); partial=len(targets)-full
    seg_counts=Counter(int(r['segment_count']) for r in targets)
    summary={
      'source':'Eoin A Moore / Kaggle NBA Database (1947-Present), PlayerStatistics.csv, CC0; filtered strictly to locked TREB regular-season game IDs',
      'schedule_games':int(len(schedule)),'player_game_roster_rows':int(len(ps)),'player_game_positive_rows':int(ps.seconds_game.gt(0).sum()),
      'raw_core_pts':int(len(recon)),'final_valid_pts':int(len(valid_core)),'orphan_pts_excluded':2,
      'core_games_exact_pts_all_raw':int(recon.games_diff.eq(0).sum()),'core_seconds_exact_pts_all_raw':int(recon.seconds_diff.eq(0).sum()),
      'core_games_exact_pts_final':int(valid_core.games_diff.eq(0).sum()),'core_seconds_exact_pts_final':int(valid_core.seconds_diff.eq(0).sum()),
      'core_games_mismatch_pts_final':int(valid_core.games_diff.ne(0).sum()),'core_seconds_mismatch_pts_final':int(valid_core.seconds_diff.ne(0).sum()),
      'max_abs_seconds_diff_final':int(valid_core.seconds_diff.abs().max()),
      'v3_full_core_reuse_candidates':int(full),'v3_partial_pts':int(partial),'v3_roster_segments':int(len(segments)),
      'pts_segment_count_distribution':{str(k):int(v) for k,v in sorted(seg_counts.items())},
      'status':'PASS_CANDIDATE' if valid_core.games_diff.eq(0).all() and valid_core.seconds_diff.abs().le(1).all() else 'RECONCILIATION_REVIEW_REQUIRED',
      'promotion_rule':'Do not promote V3 tenure targets until positive game counts and seconds reconcile to locked core (or every residual discrepancy is source-audited), and roster-presence gap behavior is audited.'
    }
    (a.out/'roster_tenure_v3_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
