#!/usr/bin/env python3
"""Strict retained-source resolver for current NO_EXACT_TENURE_IDENTITY TREB rows.

This is diagnostic/fail-closed. It never changes production. A tenure is accepted only
when an exact team schedule and an independent retained roster/game-membership source
uniquely prove the same contiguous game set. Participation-only boxscore sources are
not treated as roster proof.
"""
from __future__ import annotations
import argparse, csv, json, re
from pathlib import Path
from collections import defaultdict

PLAYER_KEYS=("player_id","person_id","personid","playerid")
TEAM_KEYS=("team_id","teamid")
GAME_KEYS=("game_id","gameid")
SEASON_KEYS=("season","season_year")


def norm(s): return str(s or '').strip()
def find_col(cols, keys):
    low={c.lower():c for c in cols}
    for k in keys:
        if k in low: return low[k]
    return None

def load_targets(path: Path):
    rows=list(csv.DictReader(path.open(encoding='utf-8-sig')))
    out=[]
    for r in rows:
        if norm(r.get('prior_status'))!='NO_EXACT_TENURE_IDENTITY': continue
        try: exp=int(float(r.get('expected_team_games') or 0))
        except: exp=0
        out.append({**r,'expected_team_games_int':exp})
    return out

def split_games(s): return [x for x in norm(s).split('|') if x]

def candidate_roster_files(root: Path):
    # Exact roster/membership semantics only. Explicitly exclude output/recovery artifacts,
    # aggregate player stats and boxscore-only files.
    pats=('roster','game_roster','roster_ledger','v3_roster','player_game_roster')
    for p in root.rglob('*.csv'):
        ps=str(p).lower()
        if 'treb_recovery_status' in ps or '/outputs/' in ps or 'career' in ps: continue
        if not any(x in ps for x in pats): continue
        if p.stat().st_size > 400_000_000: continue
        yield p

def read_memberships(path: Path, wanted):
    # wanted tuples season,player,team. Return exact game membership sets per target.
    got=defaultdict(set)
    try:
        with path.open(encoding='utf-8-sig',errors='replace',newline='') as f:
            rd=csv.DictReader(f); cols=rd.fieldnames or []
            pc=find_col(cols,PLAYER_KEYS); tc=find_col(cols,TEAM_KEYS); gc=find_col(cols,GAME_KEYS); sc=find_col(cols,SEASON_KEYS)
            if not (pc and tc and gc): return got
            for r in rd:
                pid=norm(r.get(pc)); tid=norm(r.get(tc)); gid=norm(r.get(gc)); sea=norm(r.get(sc)) if sc else ''
                if not (pid and tid and gid): continue
                # season can be inferred from target only when player/team unique in wanted.
                keys=[]
                if sea and (sea,pid,tid) in wanted: keys=[(sea,pid,tid)]
                elif not sea:
                    keys=[k for k in wanted if k[1]==pid and k[2]==tid]
                for k in keys: got[k].add(gid)
    except Exception:
        return defaultdict(set)
    return got

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--repo-root',required=True); ap.add_argument('--proof',required=True); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
    root=Path(a.repo_root); proof=Path(a.proof); out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    targets=load_targets(proof)
    wanted={(norm(r['season']),norm(r['player_id']),norm(r['team_id'])) for r in targets}
    sources=[]; merged=defaultdict(set); source_sets=defaultdict(dict)
    for p in candidate_roster_files(root):
        g=read_memberships(p,wanted)
        hit=sum(bool(v) for v in g.values())
        if not hit: continue
        rel=str(p.relative_to(root)); sources.append({'path':rel,'targets_with_rows':hit})
        for k,v in g.items():
            if v:
                source_sets[k][rel]=sorted(v); merged[k].update(v)

    results=[]
    for r in targets:
        k=(norm(r['season']),norm(r['player_id']),norm(r['team_id'])); exp=r['expected_team_games_int']
        schedule=split_games(r.get('game_ids'))
        # schedule-audited proof may have full reconstructed team schedule even if status rejected.
        if not schedule and norm(r.get('reconstructed_games')).isdigit() and int(r['reconstructed_games'])==exp:
            schedule=split_games(r.get('game_ids'))
        verdict='UNRESOLVED'; accepted_source=''; accepted_games=[]; reason=''
        if not schedule:
            reason='no_exact_schedule_game_ids_in_current_proof'
        elif len(schedule)!=exp:
            reason=f'schedule_count_{len(schedule)}_ne_expected_{exp}'
        else:
            exact_matches=[]
            sched=set(schedule)
            for src,games in source_sets.get(k,{}).items():
                gs=set(games)
                # strict equality means the independent roster source proves membership
                # on every expected game and no target-team roster game outside tenure.
                if gs==sched: exact_matches.append(src)
            if exact_matches:
                verdict='EXACT_RETAINED_ROSTER_SCHEDULE_TENURE_IDENTITY'
                accepted_source='|'.join(sorted(exact_matches)); accepted_games=schedule
                reason='independent_exact_roster_membership_equals_schedule_audited_game_set'
            else:
                counts={s:len(g) for s,g in source_sets.get(k,{}).items()}
                reason='no_independent_roster_source_exactly_matches_schedule; '+json.dumps(counts,sort_keys=True)
        results.append({
            'season':k[0],'player_id':k[1],'team_id':k[2],'expected_team_games':exp,
            'schedule_games':len(schedule),'verdict':verdict,'accepted_source':accepted_source,
            'accepted_game_ids':'|'.join(accepted_games),'reason':reason,
            'roster_sources_with_rows':len(source_sets.get(k,{}))
        })
    fields=list(results[0].keys()) if results else []
    with (out/'TREB_CURRENT_21_TENURE_BOUNDARY_RESOLUTION.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(results)
    accepted=sum(r['verdict'].startswith('EXACT_') for r in results)
    summary={'targets':len(results),'exact_resolved':accepted,'unresolved':len(results)-accepted,'candidate_roster_sources':len(sources),'sources':sources}
    (out/'TREB_CURRENT_21_TENURE_BOUNDARY_SUMMARY.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
