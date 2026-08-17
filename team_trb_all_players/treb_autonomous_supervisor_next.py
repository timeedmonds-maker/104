#!/usr/bin/env python3
import argparse,csv,gzip,json,pathlib,time
from collections import defaultdict
import requests

def gid(x): return str(x).strip().removesuffix('.0').zfill(10)
def sid(x): return str(x).strip().removesuffix('.0')
def read_gz(p):
    with gzip.open(p,'rt',encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def pick(root,name):
    z=list(pathlib.Path(root).rglob(name))
    if not z: raise SystemExit('missing '+name)
    return z[0]
def iv(x): return int(round(float(x)))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir);out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True)
    team_exact=read_gz(pick(cur,'RECOVERED_RESIDUAL_SHARED_TEAM_GAME_PRIMITIVES.csv.gz'))
    reg=list(csv.DictReader(open(pick(cur,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),newline='')))
    targets=[]
    for r in reg:
        if int(float(r.get('team_target_count') or 0))<=0: continue
        for t in str(r.get('team_ids') or '').split('|'):
            if t.strip(): targets.append((r['season'],gid(r['game_id']),sid(t)))
    targets=sorted(set(targets))
    sess=requests.Session();sess.headers.update({'User-Agent':'Mozilla/5.0','Accept':'application/json,text/plain,*/*'})
    by=defaultdict(list)
    for r in team_exact: by[r['season']].append(r)
    controls=[]
    for season in sorted(by):
        games=[]
        for r in sorted(by[season],key=lambda z:(gid(z['game_id']),sid(z['team_id']))):
            g=gid(r['game_id'])
            if g not in games: games.append(g)
            if len(games)>=5: break
        for r in by[season]:
            if gid(r['game_id']) in games: controls.append(r)
    qa={'status':'PASS_NO_PROGRESS','team_targets':len(targets),'lanes':{}}
    lane_candidates=[]

    def run_lane(name,fetcher,provenance):
        c=0; seasons=set();mism=[];errs=[]
        for r in controls:
            try:
                got=fetcher(gid(r['game_id'])).get(sid(r['team_id']))
                if got is None: raise RuntimeError('TEAM_NOT_PRESENT')
                exp=tuple(iv(r[k]) for k in ['team_oreb','team_dreb','opponent_oreb','opponent_dreb'])
                c+=1;seasons.add(r['season'])
                if got!=exp:mism.append({'season':r['season'],'game_id':gid(r['game_id']),'team_id':sid(r['team_id']),'expected':exp,'got':got})
            except Exception as e: errs.append({'season':r['season'],'game_id':gid(r['game_id']),'error':repr(e)})
        gate=c>=50 and len(seasons)>=10 and not mism
        promoted=[];terr=[]
        if gate:
            for season,g,t in targets:
                try:
                    got=fetcher(g).get(t)
                    if got is None: raise RuntimeError('TEAM_NOT_PRESENT')
                    promoted.append({'season':season,'game_id':g,'team_id':t,'team_oreb':got[0],'team_dreb':got[1],'opponent_oreb':got[2],'opponent_dreb':got[3],'provenance':provenance})
                except Exception as e:terr.append({'season':season,'game_id':g,'team_id':t,'error':repr(e)})
        qa['lanes'][name]={'gate':gate,'controls':c,'seasons':len(seasons),'mismatches':len(mism),'errors':len(errs),'promoted':len(promoted),'target_errors':len(terr),'mismatch_examples':mism[:6],'error_examples':errs[:8],'target_error_examples':terr[:8]}
        lane_candidates.extend((name,r) for r in promoted)

    # Independent official NBA static live-data boxscore. No stats.nba.com endpoint or lineup reconstruction.
    cdn_cache={}
    def cdn(g):
        if g in cdn_cache:return cdn_cache[g]
        u=f'https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{g}.json'
        rr=sess.get(u,timeout=15);rr.raise_for_status();js=rr.json();game=js.get('game') or {}
        rows={}
        for side in ('homeTeam','awayTeam'):
            d=game.get(side) or {}; st=d.get('statistics') or {};t=sid(d.get('teamId'))
            o=st.get('reboundsOffensive');de=st.get('reboundsDefensive')
            if t and o is not None and de is not None:rows[t]=(iv(o),iv(de))
        if len(rows)!=2:raise RuntimeError('CDN_TWO_TEAM_ROWS_NOT_FOUND')
        ts=list(rows);ans={ts[0]:(*rows[ts[0]],*rows[ts[1]]),ts[1]:(*rows[ts[1]],*rows[ts[0]])};cdn_cache[g]=ans;time.sleep(.03);return ans
    run_lane('nba_cdn_liveData_team_boxscore',cdn,'NBA CDN liveData team boxscore; >=50 controls, >=10 seasons, zero mismatches')

    # Independent legacy NBA static mobile game-detail feed. Kept separate from CDN gate.
    legacy_cache={}
    def legacy(g):
        if g in legacy_cache:return legacy_cache[g]
        # season stem is encoded in game id only indirectly; try retained target/control season years via broad endpoint variants.
        last=None
        for stem in range(2000,2027):
            u=f'https://data.nba.net/data/10s/v2015/json/mobile_teams/nba/{stem}/scores/gamedetail/{g}_gamedetail.json'
            try:
                rr=sess.get(u,timeout=8)
                if rr.status_code!=200:continue
                js=rr.json();game=js.get('g') or js.get('game') or js
                rows={}
                for key in ('hls','vls','homeTeam','awayTeam'):
                    d=game.get(key) if isinstance(game,dict) else None
                    if not isinstance(d,dict):continue
                    t=sid(d.get('tid') or d.get('teamId') or '')
                    st=d.get('tstsg') or d.get('statistics') or d
                    o=st.get('oreb') if isinstance(st,dict) else None;de=st.get('dreb') if isinstance(st,dict) else None
                    if o is None and isinstance(st,dict):o=st.get('reboundsOffensive')
                    if de is None and isinstance(st,dict):de=st.get('reboundsDefensive')
                    if t and o is not None and de is not None:rows[t]=(iv(o),iv(de))
                if len(rows)==2:
                    ts=list(rows);ans={ts[0]:(*rows[ts[0]],*rows[ts[1]]),ts[1]:(*rows[ts[1]],*rows[ts[0]])};legacy_cache[g]=ans;return ans
            except Exception as e:last=repr(e)
        raise RuntimeError(last or 'LEGACY_STATIC_GAME_NOT_FOUND')
    run_lane('nba_legacy_static_mobile_gamedetail',legacy,'NBA legacy static mobile game detail; >=50 controls, >=10 seasons, zero mismatches')

    # Union only conflict-free independently gated candidates.
    merged={};sources=defaultdict(list)
    for name,r in lane_candidates:
        k=(r['game_id'],r['team_id']);v=tuple(iv(r[x]) for x in ['team_oreb','team_dreb','opponent_oreb','opponent_dreb'])
        if k in merged and merged[k][0]!=v: raise SystemExit(f'EXACT SOURCE CONFLICT {k}: {merged[k][0]} != {v}')
        merged[k]=(v,r);sources[k].append(name)
    promoted=[]
    for k,(v,r) in sorted(merged.items()):
        q=dict(r);q['provenance']=q['provenance']+'; gated_sources='+','.join(sorted(sources[k]));promoted.append(q)
    tf=['season','game_id','team_id','team_oreb','team_dreb','opponent_oreb','opponent_dreb','provenance']
    with gzip.open(out/'CANDIDATE_TEAM_GAME_PRIMITIVES.csv.gz','wt',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=tf);w.writeheader();w.writerows(promoted)
    pf=['season','game_id','team_id','player_id','seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on','provenance']
    with gzip.open(out/'CANDIDATE_PLAYER_GAME_PRIMITIVES.csv.gz','wt',encoding='utf-8',newline='') as f:csv.DictWriter(f,fieldnames=pf).writeheader()
    qa['new_team_facts']=len(promoted);qa['new_player_facts']=0
    if promoted:qa['status']='PASS_PROGRESS'
    (out/'SUPERVISOR_SOURCE_QA.json').write_text(json.dumps(qa,indent=2));print(json.dumps(qa,indent=2))
if __name__=='__main__':main()
