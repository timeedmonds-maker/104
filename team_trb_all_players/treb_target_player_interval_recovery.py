#!/usr/bin/env python3
"""Fail-closed target-player interval TREB recovery from pinned static feeds.

This lane deliberately does not reconstruct complete 5-v-5 lineups.  For each
missing player-game primitive it proves only that player's period-local on/off
state from the official substitution chronology (plus direct participant
presence when no substitution exists).  Rebound rows are counted only after a
strict one-to-one PBP Stats -> NBA rebound-event reconciliation.  Any ambiguous
period, substitution sequence, rebound mapping, or control mismatch is rejected.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import tempfile
from collections import defaultdict

import pandas as pd

import local_treb_rebuild as core
import production_treb_engine as prod
import production_treb_engine_v3 as v3eng
import run_local_treb_production as io
import treb_static_period_unique_recovery as static

PV = ['seconds_on','team_oreb_on','team_dreb_on','opponent_oreb_on','opponent_dreb_on']
TV = ['team_oreb','team_dreb','opponent_oreb','opponent_dreb']
COUNTER_RE = re.compile(r'\(off:(\d+) def:(\d+)\)', re.I)


def gid(x): return int(float(str(x).strip()))
def sid(x): return str(x).strip().removesuffix('.0')
def norm(x):
    if pd.isna(x): return ''
    return re.sub(r'\s+', ' ', str(x)).strip().lower()


def prepare_nba(nba_game: pd.DataFrame, v3_game: pd.DataFrame) -> pd.DataFrame:
    game, _ = prod.prepare_nba_game(nba_game)
    game = game.copy()
    game['DESCRIPTION_NORM'] = core.nba_description(game)
    game['ELAPSED'] = [core.elapsed_seconds(int(p), c) for p,c in zip(game.PERIOD, game.PCTIMESTRING)]
    order = v3eng._v3_action_map(v3_game)
    game['V3_ORDER'] = [order.get((int(p), int(ev)), 10_000_000 + int(ev)) for p,ev in zip(game.PERIOD, game.EVENTNUM)]
    return game.sort_values(['PERIOD','ELAPSED','V3_ORDER','EVENTNUM'], kind='stable').reset_index(drop=True)


def pbp_rebounds(pbp_game: pd.DataFrame) -> pd.DataFrame:
    x = pbp_game.copy()
    x['PREV_PBP_DESCRIPTION'] = x.groupby(core.POSSESSION_ID, dropna=False).DESCRIPTION.shift(1)
    x = x[x.DESCRIPTION.fillna('').str.contains('rebound', case=False)].copy()
    x['DESCRIPTION_NORM'] = x.DESCRIPTION.map(norm)
    x['START_ELAPSED'] = [core.elapsed_seconds(int(p), c) for p,c in zip(x.PERIOD, x.STARTTIME)]
    x['END_ELAPSED'] = [core.elapsed_seconds(int(p), c) for p,c in zip(x.PERIOD, x.ENDTIME)]
    return x


def name_key(v): return norm(v).split(' rebound',1)[0].strip()


def unique_order_assignment(rows: pd.DataFrame, nba: pd.DataFrame, used: set[int], alpha: int = 5):
    if rows.empty: return {}
    rr = rows.sort_values(['START_ELAPSED','END_ELAPSED'], kind='stable')
    ev = nba[nba.EVENTMSGTYPE.eq(4) & ~nba.index.isin(used)].sort_values(['ELAPSED','V3_ORDER','EVENTNUM'], kind='stable')
    rlist = list(rr.iterrows()); elist = [(int(i),int(r.ELAPSED)) for i,r in ev.iterrows()]
    n,m=len(rlist),len(elist)
    if m<n: return None
    allowed=[]
    for _,r in rlist:
        lo=min(int(r.START_ELAPSED),int(r.END_ELAPSED))-alpha
        hi=max(int(r.START_ELAPSED),int(r.END_ELAPSED))+alpha
        allowed.append([lo < e < hi for _,e in elist])
    dp=[bytearray(m+1) for _ in range(n+1)]
    for j in range(m+1): dp[n][j]=1
    for i in range(n-1,-1,-1):
        for j in range(m-1,-1,-1):
            if m-j < n-i: continue
            total=int(dp[i][j+1])
            if allowed[i][j]: total += int(dp[i+1][j+1])
            dp[i][j]=2 if total>=2 else total
    if int(dp[0][0]) != 1: return None
    path=[];i=j=0
    while i<n:
        if j>=m: return None
        skip=int(dp[i][j+1]); match=int(dp[i+1][j+1]) if allowed[i][j] else 0
        if match and not skip: path.append(j);i+=1;j+=1
        elif skip and not match: j+=1
        else: return None
    return {int(rlist[k][0]):int(elist[path[k]][0]) for k in range(n)}


def exact_rebound_join(nba: pd.DataFrame, pbp_game: pd.DataFrame, alpha: int = 5):
    rows = pbp_rebounds(pbp_game)
    mapping={}; used=set(); methods={}
    # First use only unique exact identities inside the legal clock window.
    for idx,r in rows.iterrows():
        eligible=nba[nba.PERIOD.eq(int(r.PERIOD)) & nba.EVENTMSGTYPE.eq(4) &
                     nba.ELAPSED.gt(int(r.START_ELAPSED)-alpha) & nba.ELAPSED.lt(int(r.END_ELAPSED)+alpha) &
                     ~nba.index.isin(used)]
        exact=eligible[eligible.DESCRIPTION_NORM.eq(r.DESCRIPTION_NORM)]
        chosen=None; method=None
        if len(exact)==1:
            chosen=int(exact.index[0]); method='exact_description'
        else:
            counter=COUNTER_RE.search(r.DESCRIPTION_NORM)
            if counter:
                ck=f'(off:{counter.group(1)} def:{counter.group(2)})'; pk=name_key(r.DESCRIPTION_NORM)
                hit=eligible[eligible.DESCRIPTION_NORM.str.contains(re.escape(ck),regex=True) & eligible.DESCRIPTION_NORM.map(name_key).eq(pk)]
                if len(hit)==1:
                    chosen=int(hit.index[0]); method='exact_player_counter'
        if chosen is not None:
            mapping[int(idx)]=chosen;used.add(chosen);methods[int(idx)]=method
    # Locked source-proven one-row repairs remain admissible, but only if exact identity still asserts.
    game_id=int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0
    for idx,r in rows[~rows.index.isin(mapping)].iterrows():
        ev=prod.JOIN_REPAIRS.get((game_id,int(r.PERIOD),r.DESCRIPTION_NORM))
        if ev is None: continue
        hit=nba[nba.PERIOD.eq(int(r.PERIOD)) & nba.EVENTNUM.eq(int(ev)) & nba.EVENTMSGTYPE.eq(4) & ~nba.index.isin(used)]
        if len(hit)==1:
            ni=int(hit.index[0]);mapping[int(idx)]=ni;used.add(ni);methods[int(idx)]='locked_join_repair'
    # Finally, unresolved rows may be repaired only when a period has exactly one
    # order-preserving injection into unused NBA rebound events.
    for period, rem in rows[~rows.index.isin(mapping)].groupby('PERIOD',sort=True):
        mp=unique_order_assignment(rem,nba[nba.PERIOD.eq(int(period))],used,alpha)
        if mp is None: continue
        for pi,ni in mp.items():
            if ni in used: raise ValueError(f'event reuse game={game_id} nba_index={ni}')
            mapping[int(pi)]=int(ni);used.add(int(ni));methods[int(pi)]='unique_period_order_assignment'
    unmatched=[int(i) for i in rows.index if int(i) not in mapping]
    if unmatched:
        return None, {'status':'UNMATCHED_REBOUNDS','game_id':game_id,'rebound_rows':len(rows),'matched':len(mapping),'unmatched':len(unmatched)}
    out=rows.copy()
    out['NBA_INDEX']=[mapping[int(i)] for i in out.index]
    out['NBA_IS_REAL_REBOUND']=[bool(core._nba_real_rebound(nba,int(mapping[int(i)]))) for i in out.index]
    out['NBA_ELAPSED']=[int(nba.loc[mapping[int(i)],'ELAPSED']) for i in out.index]
    out['NBA_EVENTNUM']=[int(nba.loc[mapping[int(i)],'EVENTNUM']) for i in out.index]
    out=core.classify_rebounds(out)
    return out, {'status':'PASS','game_id':game_id,'rebound_rows':len(out),'matched':len(out),'methods':dict(pd.Series(list(methods.values())).value_counts()) if methods else {}}


def participant_present(period: pd.DataFrame, pid: int) -> bool:
    for n in (1,2,3):
        ids=pd.to_numeric(period[f'PLAYER{n}_ID'],errors='coerce')
        types=pd.to_numeric(period[f'PERSON{n}TYPE'],errors='coerce')
        if bool((ids.eq(pid)&types.isin([4,5])).any()): return True
    return False


def player_state(nba: pd.DataFrame, pid: int):
    """Return (seconds, on_by_nba_index) only when every period state is unique."""
    on_by={}; seconds=0; evidence=[]
    periods=sorted(int(x) for x in pd.to_numeric(nba.PERIOD,errors='coerce').dropna().unique())
    for p in periods:
        per=nba[nba.PERIOD.eq(p)]
        relevant=per[per.EVENTMSGTYPE.eq(8) & (per.PLAYER1_ID.eq(pid)|per.PLAYER2_ID.eq(pid))]
        if len(relevant):
            first=relevant.iloc[0]
            out=int(first.PLAYER1_ID or 0)==pid; inn=int(first.PLAYER2_ID or 0)==pid
            if out==inn: return None
            state=bool(out)
            basis='first_sub_out' if state else 'first_sub_in'
        elif participant_present(per,pid):
            state=True;basis='participant_no_sub'
        else:
            return None
        start=(p-1)*720 if p<=4 else 2880+(p-5)*300
        end=start+(720 if p<=4 else 300)
        # Locked period-start feed gap: the unrecorded opening interval is not playing time.
        gap=prod.PERIOD_START_GAP_REPAIRS.get((int(nba.GAME_ID.iloc[0]),p))
        if gap is not None:
            start += int(gap['seconds_removed'])
        last=start
        for idx,row in per.iterrows():
            now=max(start,min(end,int(row.ELAPSED)))
            if now>last and state: seconds += now-last
            last=max(last,now)
            if int(row.EVENTMSGTYPE)==8 and (int(row.PLAYER1_ID or 0)==pid or int(row.PLAYER2_ID or 0)==pid):
                outgoing=int(row.PLAYER1_ID or 0)==pid; incoming=int(row.PLAYER2_ID or 0)==pid
                if outgoing and state: state=False
                elif incoming and not state: state=True
                else: return None
            on_by[int(idx)]=bool(state)
        if end>last and state: seconds += end-last
        evidence.append({'period':p,'basis':basis})
    return int(seconds),on_by,evidence


def team_abbrs(game):
    evidence=defaultdict(list)
    for n in (1,2,3):
        ids=pd.to_numeric(game[f'PLAYER{n}_TEAM_ID'],errors='coerce')
        col=f'PLAYER{n}_TEAM_ABBREVIATION'
        if col not in game: continue
        for tid,abbr in zip(ids,game[col]):
            if pd.notna(tid) and int(tid)>0 and pd.notna(abbr) and str(abbr).strip(): evidence[int(tid)].append(str(abbr).strip())
    return {t:max(set(v),key=v.count) for t,v in evidence.items()}


def game_facts(nba_raw,v3_raw,pbp,requested_players,requested_teams):
    nba=prepare_nba(nba_raw,v3_raw)
    joined,ja=exact_rebound_join(nba,pbp)
    if joined is None: raise ValueError(f"rebound join failed: {ja}")
    pteam=core._player_team(nba); ab=team_abbrs(nba); teams=sorted(set(pteam.values()))
    if len(teams)!=2 or any(t not in ab for t in teams): raise ValueError('team identity/abbreviation unresolved')
    real=joined.IS_REAL_REBOUND.astype(bool);oreb=joined.IS_OREB.astype(bool)
    masks={}
    tr=[]
    for t in teams:
        off=~joined.OPPONENT.astype(str).eq(ab[t]);deff=~off;masks[t]=(off,deff)
        tr.append({'game_id':int(nba.GAME_ID.iloc[0]),'team_id':int(t),'team_oreb':int((off&oreb).sum()),'team_dreb':int((deff&real&~oreb).sum()),'opponent_oreb':int((deff&oreb).sum()),'opponent_dreb':int((off&real&~oreb).sum())})
    pr=[];states={}
    for t,p in requested_players:
        pid=int(p);t=int(t)
        if pteam.get(pid)!=t: continue
        st=player_state(nba,pid)
        if st is None: continue
        sec,on_by,evidence=st
        on=pd.Series([bool(on_by.get(int(i),False)) for i in joined.NBA_INDEX],index=joined.index)
        off,deff=masks[t]
        pr.append({'game_id':int(nba.GAME_ID.iloc[0]),'team_id':t,'player_id':sid(pid),'seconds_on':sec,'team_oreb_on':int((on&off&oreb).sum()),'team_dreb_on':int((on&deff&real&~oreb).sum()),'opponent_oreb_on':int((on&deff&oreb).sum()),'opponent_dreb_on':int((on&off&real&~oreb).sum()),'interval_evidence':json.dumps(evidence,separators=(',',':'))})
    return tr,pr,ja


def eq(old,new,fs):
    try:return all(float(getattr(old,f))==float(new[f]) for f in fs)
    except:return False


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--current-dir',required=True);ap.add_argument('--out-dir',required=True);ap.add_argument('--season',required=True);a=ap.parse_args()
    cur=pathlib.Path(a.current_dir);out=pathlib.Path(a.out_dir);out.mkdir(parents=True,exist_ok=True);season=a.season;year=season[:4]
    reg=pd.read_csv(static.pick(cur,'NEXT_RESIDUAL_SHARED_GAME_REGISTRY.csv'),low_memory=False);target_games,np,nt=static.targets(reg,season)
    pc,tc=static.control_frames(cur,season)
    pmap={(gid(r.game_id),int(r.team_id),sid(r.player_id)):r for r in pc.itertuples(index=False)}
    tmap={(gid(r.game_id),int(r.team_id)):r for r in tc.itertuples(index=False)}
    control_games=set(g for g,_,_ in pmap)|set(g for g,_ in tmap)
    wanted=set(target_games)|control_games
    qa={'status':'PASS','season':season,'target_games':len(target_games),'control_games_requested':len(control_games),'controls_checked':0,'control_mismatches':0,'control_games_reconstructed':0,'target_games_with_candidates':0,'recovered_player_primitives':0,'recovered_team_primitives':0,'game_failures':[],'integrity':{'full_lineup_reconstruction_required':False,'player_interval_period_local':True,'exact_rebound_identity_or_unique_period_assignment_only':True,'event_reuse_forbidden':True,'empirical_model_used':False,'rounded_percentage_backsolve_used':False,'opponent_rebound_inference_used':False,'partial_tenure_team_subtraction_used':False,'promotion_performed':False}}
    if not target_games:
        qa['status']='NO_TARGETS';(out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');return 0
    cp=[];ct=[]
    with tempfile.TemporaryDirectory(prefix='treb_up_') as gd,tempfile.TemporaryDirectory(prefix='treb_arc_') as td:
        try:
            repo=static.prep(pathlib.Path(gd));tmp=pathlib.Path(td)
            nr,_=static.archive_df(repo,tmp,'nbastats',year,wanted);vr,_=static.archive_df(repo,tmp,'nbastatsv3',year,wanted);pr,_=static.archive_df(repo,tmp,'pbpstats',year,wanted)
        except Exception as e:
            qa['status']='SOURCE_FAILURE';qa['game_failures'].append({'scope':'source','error':f'{type(e).__name__}: {e}'});(out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2));return 0
    nba=io.normalize_nba(nr);v3=v3eng.normalize_v3(vr);pbp=io.normalize_pbp(pr)
    ng={gid(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)};vg={gid(g):f.copy() for g,f in v3.groupby('gameId',sort=False)};pg={gid(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    for g in sorted(wanted):
        role='target' if g in target_games else 'control'
        if g not in ng or g not in vg or g not in pg:
            qa['game_failures'].append({'game_id':g,'role':role,'status':'SOURCE_SET_GAP'});continue
        req=set(np.get(g,set()))
        if g in control_games: req |= {(t,p) for gg,t,p in pmap if gg==g}
        rteams=set(nt.get(g,set()))
        if g in control_games: rteams |= {t for gg,t in tmap if gg==g}
        try: tr,pl,ja=game_facts(ng[g],vg[g],pg[g],req,rteams)
        except Exception as e:
            qa['game_failures'].append({'game_id':g,'role':role,'status':'RECONSTRUCTION_FAIL','error':f'{type(e).__name__}: {e}'});continue
        fp={(int(z['team_id']),sid(z['player_id'])):z for z in pl};ft={int(z['team_id']):z for z in tr};mm=[];checked=0
        for k,z in fp.items():
            old=pmap.get((g,k[0],k[1]))
            if old is not None:
                checked+=1;qa['controls_checked']+=1
                if not eq(old,z,PV): mm.append({'kind':'player','key':[g,k[0],k[1]],'old':[float(getattr(old,f)) for f in PV],'new':[float(z[f]) for f in PV]})
        for t,z in ft.items():
            old=tmap.get((g,t))
            if old is not None:
                checked+=1;qa['controls_checked']+=1
                if not eq(old,z,TV): mm.append({'kind':'team','key':[g,t]})
        if mm:
            qa['control_mismatches']+=len(mm);qa['game_failures'].append({'game_id':g,'role':role,'status':'CONTROL_MISMATCH','examples':mm[:10]});continue
        if checked: qa['control_games_reconstructed']+=1
        if role!='target': continue
        got=0
        prov=f'exact target-player period-local intervals + exact static PBP/NBA rebound reconciliation @ {static.UPSTREAM_COMMIT}'
        for k in sorted(np.get(g,set())):
            if k in fp:
                z=dict(fp[k]);z['season']=season;z['provenance']=prov;cp.append(z);got+=1
        for t in sorted(nt.get(g,set())):
            if t in ft:
                z=dict(ft[t]);z['season']=season;z['provenance']=prov;ct.append(z);got+=1
        if got: qa['target_games_with_candidates']+=1
    if qa['control_mismatches']:
        qa['status']='FAIL_CONTROL_MISMATCH';cp=[];ct=[]
    elif qa['game_failures']: qa['status']='PARTIAL'
    if cp: pd.DataFrame(cp).to_csv(out/'PLAYER_CANDIDATES.csv.gz',index=False,compression='gzip')
    if ct: pd.DataFrame(ct).to_csv(out/'TEAM_CANDIDATES.csv.gz',index=False,compression='gzip')
    qa['recovered_player_primitives']=len(cp);qa['recovered_team_primitives']=len(ct)
    (out/'QA.json').write_text(json.dumps(qa,indent=2)+'\n');print(json.dumps(qa,indent=2),flush=True);return 0

if __name__=='__main__': raise SystemExit(main())
