#!/usr/bin/env python3
"""Build a compact evidence pack for the 30 structurally impossible V2 tenures."""
from __future__ import annotations
import argparse,gzip,json
from pathlib import Path
import pandas as pd

BASE=Path(__file__).resolve().parent
IMPACT=BASE/'impact_database'
FAIL=BASE/'final_integrity_rebuild'/'DEFINITE_TENURE_BOUNDARY_FAILURES.json'
TX=IMPACT/'roster_tenure'/'normalized_transactions.jsonl.gz'
GAMES=IMPACT/'roster_tenure'/'regular_season_games_raw'
V2=IMPACT/'roster_tenure_v2'/'player_team_season_targets.jsonl.gz'


def read_jsonl(path:Path):
    rows=[]
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:
            if line.strip():rows.append(json.loads(line))
    return rows

def load_schedule():
    team={}
    game_to_season={}
    for p in sorted(GAMES.glob('*.json.gz')):
        season=p.name.replace('.json.gz','')
        with gzip.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
        for g in d['results']:
            gid=int(g['GameId']);date=str(pd.Timestamp(g['Date']).date());game_to_season[gid]=season
            for tid in (int(g['HomeTeamId']),int(g['AwayTeamId'])):
                team.setdefault((season,tid),[]).append({'game_id':gid,'game_date':date})
    for k in team:team[k].sort(key=lambda r:(r['game_date'],r['game_id']))
    return team,game_to_season

def norm_pid(v):
    try:return str(int(float(v)))
    except:return str(v)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--playerstatistics',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    failures=json.loads(FAIL.read_text())['rows']; keys={(r['season'],str(r['player_id']),int(r['team_id'])) for r in failures}
    tx=read_jsonl(TX);v2=read_jsonl(V2);schedule,_=load_schedule()
    v2map={(r['season'],str(r['player_id']),int(r['team_id'])):r for r in v2}
    use=['personId','gameId','gameDate','playerteamId','numMinutes','comment','startingPosition','firstName','lastName']
    ps=pd.read_csv(a.playerstatistics,usecols=lambda c:c in use,low_memory=False)
    ps['player_id']=ps.personId.map(norm_pid);ps['team_id']=pd.to_numeric(ps.playerteamId,errors='coerce').astype('Int64');ps['game_id']=pd.to_numeric(ps.gameId,errors='coerce').astype('Int64');ps['minutes']=pd.to_numeric(ps.numMinutes,errors='coerce')
    target_pids={k[1] for k in keys};target_tids={k[2] for k in keys}
    ps=ps[ps.player_id.isin(target_pids)&ps.team_id.isin(target_tids)&ps.game_id.notna()].copy()
    txdf=pd.DataFrame(tx);txdf['player_id']=txdf.player_id.astype(str)
    out=[]
    for f in failures:
        key=(f['season'],str(f['player_id']),int(f['team_id']));games=schedule[key[:1]+key[2:]] if False else schedule[(key[0],key[2])]
        game_ids={g['game_id'] for g in games};index={g['game_id']:i for i,g in enumerate(games)}
        g=ps[(ps.player_id.eq(key[1]))&(ps.team_id.eq(key[2]))&(ps.game_id.astype('Int64').isin(game_ids))].copy()
        g['game_id']=g.game_id.astype(int);g['game_date']=g.game_id.map({x['game_id']:x['game_date'] for x in games})
        g=g.sort_values(['game_date','game_id'])
        roster_rows=[]
        for r in g.itertuples(index=False):
            roster_rows.append({'game_id':int(r.game_id),'game_date':str(r.game_date),'minutes':None if pd.isna(r.minutes) else float(r.minutes),'comment':None if not hasattr(r,'comment') or pd.isna(r.comment) else str(r.comment),'starting_position':None if not hasattr(r,'startingPosition') or pd.isna(r.startingPosition) else str(r.startingPosition)})
        positive=[x for x in roster_rows if x['minutes'] is not None and x['minutes']>0]
        # Include schedule context around each observed roster edge and the V2 edges.
        edge_indices=[]
        for rr in roster_rows:
            edge_indices.append(index.get(rr['game_id']))
        vv=v2map.get(key,{})
        for bound in ('query_start_date','query_end_date'):
            if bound in vv:
                dates=[i for i,x in enumerate(games) if x['game_date']>=str(vv[bound])]
                if dates:edge_indices.append(dates[0])
        ctx=set()
        for i in [x for x in edge_indices if x is not None]:
            ctx.update(range(max(0,i-3),min(len(games),i+4)))
        txg=txdf[(txdf.player_id.eq(key[1]))&(txdf.season.eq(key[0]))].copy() if 'season' in txdf else txdf[txdf.player_id.eq(key[1])].copy()
        txrows=[]
        for _,r in txg.sort_values('exact_date').iterrows():
            txrows.append({k:(None if pd.isna(r.get(k)) else r.get(k)) for k in ['exact_date','event_type','source_team_id','destination_team_id','raw_text','source_url'] if k in r.index})
        out.append({
          'season':key[0],'player_id':key[1],'player':f['player'],'team_id':key[2],
          'core_games':int(f['core_games']),'core_seconds':float(f['core_seconds']),
          'v2_window_games':int(f['window_games']),'v2_reconstructed_seconds':int(f['reconstructed_seconds']),'v2_seconds_diff':float(f['seconds_diff']),
          'v2_target':vv,
          'cc0_roster_rows_count':len(roster_rows),'cc0_positive_games_count':len(positive),
          'cc0_roster_rows':roster_rows,
          'cc0_positive_game_ids':[x['game_id'] for x in positive],
          'transaction_rows':txrows,
          'schedule_context':[games[i] for i in sorted(ctx)],
        })
    payload={'target_pts':len(out),'rows':out}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    print(json.dumps({'target_pts':len(out),'cc0_rows_available':sum(r['cc0_roster_rows_count']>0 for r in out),'cc0_positive_count_matches_core':sum(r['cc0_positive_games_count']==r['core_games'] for r in out)},indent=2))
if __name__=='__main__':main()
