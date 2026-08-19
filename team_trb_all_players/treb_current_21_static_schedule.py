#!/usr/bin/env python3
"""Build exact team schedules for the current 21 TREB tenure-identity rows from the pinned
shufinskiy/nba_data historical regular-season archives.

Fail closed: a target team-season is emitted only when the paired static archives yield exactly
its expected number of unique regular-season game IDs, every game has one date, and the target
team ID is directly observed in the NBA Stats event feed. This script never mutates TREB values.
"""
from __future__ import annotations
import argparse,csv,json,subprocess,tarfile,tempfile,re
from collections import defaultdict,Counter
from pathlib import Path
from datetime import datetime

PIN='e829d4678be1e075f99e5d41a1c5f97089be446b'
UPSTREAM='https://github.com/shufinskiy/nba_data.git'
TEAM_COLS=('PLAYER1_TEAM_ID','PLAYER2_TEAM_ID','PLAYER3_TEAM_ID')

def norm(x):
    s=str(x or '').strip()
    if not s:return ''
    if re.fullmatch(r'[-+]?\d+(?:\.0+)?',s):
        try:return str(int(float(s)))
        except:pass
    return s

def gid(x):
    s=norm(x)
    try:return str(int(s))
    except:return s.lstrip('0') or '0'

def season_label(y:int)->str:return f'{y}-{str(y+1)[-2:]}'

def pdate(s):
    s=str(s or '').strip()
    for f in ('%Y-%m-%d','%m/%d/%Y','%Y/%m/%d','%m/%d/%y','%Y%m%d'):
        try:return datetime.strptime(s,f).date().isoformat()
        except:pass
    m=re.match(r'(\d{4}-\d{2}-\d{2})',s)
    return m.group(1) if m else ''

def read_targets(path):
    out=[]
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            out.append({'season':str(r['season']).strip(),'team_id':norm(r['team_id']),
                        'player_id':norm(r['player_id']),'expected':int(float(r.get('expected_team_games') or 0))})
    return out

def git(*args, cwd=None, stdout=None):
    return subprocess.run(['git',*args],cwd=cwd,check=True,stdout=stdout,stderr=subprocess.PIPE)

def init_upstream(tmp:Path):
    repo=tmp/'nba_data'; repo.mkdir()
    git('init','-q',cwd=repo)
    git('remote','add','origin',UPSTREAM,cwd=repo)
    # Blobless smart fetch; individual large archive blobs are lazily materialized by git show.
    last=None
    for _ in range(3):
        try:
            git('-c','protocol.version=2','fetch','-q','--depth=1','--filter=blob:none','origin',PIN,cwd=repo)
            return repo
        except subprocess.CalledProcessError as e:last=e
    raise last

def materialize(repo:Path, rel:str, dest:Path):
    # Historical layouts used both root and datasets/. Probe exact tree paths without guessing content.
    candidates=(rel,'datasets/'+rel)
    last=None
    for c in candidates:
        try:
            with dest.open('wb') as f: git('show',f'FETCH_HEAD:{c}',cwd=repo,stdout=f)
            if dest.stat().st_size>0:return c
        except subprocess.CalledProcessError as e:
            last=e
            try:dest.unlink()
            except:pass
    raise RuntimeError(f'archive not found at pinned commit: {rel}') from last

def csv_member(tf:tarfile.TarFile, stem:str):
    names=[m for m in tf.getmembers() if m.isfile() and m.name.lower().endswith('.csv')]
    exact=[m for m in names if Path(m.name).name.lower()==(stem+'.csv').lower()]
    if exact:return exact[0]
    if len(names)==1:return names[0]
    raise RuntimeError(f'ambiguous CSV members for {stem}: {[m.name for m in names[:10]]}')

def nbastats_team_games(archive:Path, wanted:set[str]):
    game_teams=defaultdict(set)
    with tarfile.open(archive,'r:xz') as tf:
        m=csv_member(tf,archive.name.removesuffix('.tar.xz'))
        fh=tf.extractfile(m)
        import io
        rd=csv.DictReader(io.TextIOWrapper(fh,encoding='utf-8-sig',errors='replace',newline=''))
        cols=set(rd.fieldnames or [])
        if 'GAME_ID' not in cols:raise RuntimeError('nbastats missing GAME_ID')
        usable=[c for c in TEAM_COLS if c in cols]
        if not usable:raise RuntimeError('nbastats missing player team-id columns')
        for r in rd:
            g=gid(r.get('GAME_ID'))
            if not g:continue
            for c in usable:
                t=norm(r.get(c))
                if t in wanted:game_teams[g].add(t)
    return game_teams

def pbp_dates(archive:Path):
    dates={}; conflicts=set()
    with tarfile.open(archive,'r:xz') as tf:
        m=csv_member(tf,archive.name.removesuffix('.tar.xz'))
        fh=tf.extractfile(m)
        import io
        rd=csv.DictReader(io.TextIOWrapper(fh,encoding='utf-8-sig',errors='replace',newline=''))
        cols=set(rd.fieldnames or [])
        gc='GAMEID' if 'GAMEID' in cols else ('GAME_ID' if 'GAME_ID' in cols else None)
        dc='GAMEDATE' if 'GAMEDATE' in cols else ('GAME_DATE' if 'GAME_DATE' in cols else None)
        if not gc or not dc:raise RuntimeError(f'pbpstats missing game/date columns: {sorted(cols)[:40]}')
        for r in rd:
            g=gid(r.get(gc)); d=pdate(r.get(dc))
            if not g or not d:continue
            if g in dates and dates[g]!=d:conflicts.add(g)
            else:dates[g]=d
    for g in conflicts:dates.pop(g,None)
    return dates,conflicts

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--targets',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    targets=read_targets(a.targets); od=Path(a.out_dir);od.mkdir(parents=True,exist_ok=True)
    by_season=defaultdict(list)
    for t in targets:by_season[t['season']].append(t)
    schedule=[]; details=[]
    with tempfile.TemporaryDirectory() as td:
        td=Path(td); repo=init_upstream(td)
        for season,ts in sorted(by_season.items()):
            y=int(season[:4]); wanted={t['team_id'] for t in ts}
            nba=td/f'nbastats_{y}.tar.xz'; pbp=td/f'pbpstats_{y}.tar.xz'
            nba_path=materialize(repo,nba.name,nba); pbp_path=materialize(repo,pbp.name,pbp)
            gt=nbastats_team_games(nba,wanted); dates,conflicts=pbp_dates(pbp)
            for t in ts:
                games=sorted((g,dates[g]) for g,teams in gt.items() if t['team_id'] in teams and g in dates)
                unique=len({g for g,_ in games}); exact=(unique==t['expected'] and len(games)==unique)
                details.append({'season':season,'team_id':t['team_id'],'player_id':t['player_id'],'expected_games':t['expected'],
                                'observed_games':unique,'exact_schedule':int(exact),'nbastats_tree_path':nba_path,
                                'pbpstats_tree_path':pbp_path,'date_conflict_games':len(conflicts)})
                if exact:
                    for g,d in games:schedule.append({'season':season,'team_id':t['team_id'],'game_id':g,'game_date':d,'source':'shufinskiy_nba_data_pinned_static','source_commit':PIN})
    # Deduplicate schedules shared by multiple target players on same team-season.
    uniq={(r['season'],r['team_id'],r['game_id']):r for r in schedule}; schedule=list(uniq.values())
    sf=od/'TREB_CURRENT_21_STATIC_TEAM_SCHEDULE.csv'
    with sf.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['season','team_id','game_id','game_date','source','source_commit']);w.writeheader();w.writerows(sorted(schedule,key=lambda r:(r['season'],r['team_id'],r['game_date'],r['game_id'])))
    df=od/'TREB_CURRENT_21_STATIC_TEAM_SCHEDULE_DIAGNOSTICS.csv'
    with df.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(details[0]));w.writeheader();w.writerows(details)
    exact_targets=sum(int(r['exact_schedule']) for r in details)
    exact_pairs=len({(r['season'],r['team_id']) for r in details if r['exact_schedule']})
    summary={'targets':len(details),'exact_target_schedules':exact_targets,'unresolved_target_schedules':len(details)-exact_targets,
             'exact_team_season_pairs':exact_pairs,'schedule_rows':len(schedule),'pinned_commit':PIN,
             'status_counts':dict(Counter('EXACT' if r['exact_schedule'] else 'UNRESOLVED' for r in details))}
    (od/'TREB_CURRENT_21_STATIC_TEAM_SCHEDULE_SUMMARY.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))
    if exact_targets!=len(details):raise SystemExit('FAIL_CLOSED: not every current target has an exact static schedule')
if __name__=='__main__':main()
