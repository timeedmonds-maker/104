#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,json,pathlib,re
import pandas as pd
import run_local_treb_production as io
import local_treb_rebuild as core

POS=core.POSSESSION_ID

def gid(x): return str(int(float(str(x).strip().removesuffix('.0')))).zfill(10)
def tid(x): return str(int(float(x)))

def pbp_team_rows(pbp_game):
    g=pbp_game.copy()
    g['PREV_PBP_DESCRIPTION']=g.groupby(POS,dropna=False).DESCRIPTION.shift(1)
    r=g[g.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    if r.empty: return {},{'rebound_rows':0,'generic_rows':0,'generic_uncertain':0}
    r['REBOUND_NUMBER']=r.groupby(POS,dropna=False).cumcount()+1
    possession_oreb=r.groupby(POS,dropna=False).OFFENSIVEREBOUNDS.transform('first')
    r['IS_OREB']=r.REBOUND_NUMBER.le(possession_oreb)
    generic=~r.DESCRIPTION.fillna('').str.contains(r'\(Off:',case=False,regex=True)
    prev=r.PREV_PBP_DESCRIPTION.fillna('')
    non_live_ft=prev.str.contains(r'Free Throw (?:1 of [23]|2 of 3)|Technical Free Throw|Flagrant Free Throw',case=False,regex=True)
    turnover=prev.str.contains('Turnover|Violation',case=False,regex=True)
    buzzer=generic & r.ENDTIME.astype(str).eq('00:00')
    sameclock=generic & r.STARTTIME.astype(str).eq(r.ENDTIME.astype(str))
    explicit_placeholder=generic & (non_live_ft|turnover|buzzer|sameclock)
    # Candidate rule: player-credited rebound is real; generic row is real unless PBP itself proves placeholder.
    r['IS_REAL_CANDIDATE']=~explicit_placeholder
    teams=sorted(set(r.OPPONENT.dropna().astype(str)))
    # OPPONENT is opponent of offense; each game's two abbreviations can be derived from possession rows.
    out={}
    if len(teams)!=2:
        return {},{'rebound_rows':len(r),'generic_rows':int(generic.sum()),'generic_uncertain':int((generic&~explicit_placeholder).sum()),'error':'not_two_opponents'}
    # Return by team abbreviation; caller maps abbr->team id using NBA participant metadata, not rebound events/order.
    for team in teams:
        team_offense=~r.OPPONENT.astype(str).eq(team)
        team_defense=~team_offense
        out[team]={
          'team_oreb':int((team_offense&r.IS_OREB).sum()),
          'team_dreb':int((team_defense&r.IS_REAL_CANDIDATE&~r.IS_OREB).sum()),
          'opponent_oreb':int((team_defense&r.IS_OREB).sum()),
          'opponent_dreb':int((team_offense&r.IS_REAL_CANDIDATE&~r.IS_OREB).sum()),
        }
    return out,{'rebound_rows':len(r),'generic_rows':int(generic.sum()),'generic_uncertain':int((generic&~explicit_placeholder).sum()),'generic_placeholders':int(explicit_placeholder.sum())}

def team_abbr_map(nba_game):
    evidence={}
    for n in (1,2,3):
        tc=f'PLAYER{n}_TEAM_ID'; ac=f'PLAYER{n}_TEAM_ABBREVIATION'
        if tc not in nba_game or ac not in nba_game: continue
        for t,a in zip(pd.to_numeric(nba_game[tc],errors='coerce'),nba_game[ac]):
            if pd.notna(t) and int(t)>0 and pd.notna(a) and str(a).strip(): evidence.setdefault(str(a).strip(),[]).append(str(int(t)))
    return {a:max(set(v),key=v.count) for a,v in evidence.items() if v}

def load_exact_controls(root):
    # The authoritative residual artifact contains exact team facts used by reclosure when available.
    controls={}
    candidates=['EXACT_TEAM_GAME_FACTS.csv','EXACT_TEAM_GAME_FACTS.csv.gz','RECOVERED_EXACT_TEAM_GAME_FACTS.csv','RECOVERED_EXACT_TEAM_GAME_FACTS.csv.gz','EXACT_TEAM_GAME_PRIMITIVES.csv']
    for name in candidates:
        for p in pathlib.Path(root).rglob(name):
            op=pd.read_csv(p,low_memory=False)
            for r in op.to_dict('records'):
                if not all(z in r and pd.notna(r[z]) for z in ('team_oreb','team_dreb','opponent_oreb','opponent_dreb')): continue
                k=(gid(r['game_id']),tid(r['team_id'])); v=tuple(int(round(float(r[z]))) for z in ('team_oreb','team_dreb','opponent_oreb','opponent_dreb'))
                if k in controls and controls[k]!=v: raise RuntimeError(f'control conflict {k}')
                controls[k]=v
    return controls

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--year',type=int,required=True); ap.add_argument('--current-dir',required=True); ap.add_argument('--control-dir',action='append',default=[]); ap.add_argument('--nba',required=True); ap.add_argument('--pbp',required=True); ap.add_argument('--output-dir',required=True)
    a=ap.parse_args(); y=a.year; season=f'{y}-{(y+1)%100:02d}'; O=pathlib.Path(a.output_dir); O.mkdir(parents=True,exist_ok=True)
    cur=pathlib.Path(a.current_dir); missing=[]
    p=next(cur.rglob('MISSING_TEAM_GAME_FACTS.csv'))
    prefix=f'002{str(y)[-2:]}'
    for r in csv.DictReader(open(p,newline='')):
        if gid(r['game_id']).startswith(prefix): missing.append((gid(r['game_id']),tid(r['team_id'])))
    nba=io.normalize_nba(pd.read_csv(a.nba,low_memory=False)); pbp=io.normalize_pbp(pd.read_csv(a.pbp,low_memory=False))
    ng={gid(g):f.copy() for g,f in nba.groupby('GAME_ID',sort=False)}; pg={gid(g):f.copy() for g,f in pbp.groupby('GAMEID',sort=False)}
    controls={}
    for d in a.control_dir: controls.update(load_exact_controls(d))
    # If supplied control artifacts are sparse, use all target games for which a fully reconstructed team fact is in current retained inputs only; never compare to candidate itself.
    control_results=[]; class_stats={}; mismatches=[]
    for g in sorted(set(k[0] for k in controls if k[0].startswith(prefix))):
        if g not in ng or g not in pg: continue
        byabbr,meta=pbp_team_rows(pg[g]); amap=team_abbr_map(ng[g])
        cls=(meta.get('generic_uncertain',0),meta.get('generic_placeholders',0)); class_stats.setdefault(cls,[0,0])
        for abbr,v in byabbr.items():
            t=amap.get(abbr); k=(g,t) if t else None
            if not k or k not in controls: continue
            got=tuple(v[z] for z in ('team_oreb','team_dreb','opponent_oreb','opponent_dreb')); exp=controls[k]; ok=got==exp
            class_stats[cls][0]+=1; class_stats[cls][1]+=int(ok)
            control_results.append({'season':season,'game_id':g,'team_id':t,'generic_uncertain':cls[0],'generic_placeholders':cls[1],'ok':ok,'got':str(got),'expected':str(exp)})
            if not ok: mismatches.append((k,cls,got,exp))
    safe_classes={cls for cls,(n,ok) in class_stats.items() if n>=20 and n==ok}
    recovered=[]; audited=[]
    for g,t in missing:
        if g not in ng or g not in pg:
            audited.append({'season':season,'game_id':g,'team_id':t,'status':'SOURCE_GAP'}); continue
        byabbr,meta=pbp_team_rows(pg[g]); amap=team_abbr_map(ng[g]); inv={v:k for k,v in amap.items()}; abbr=inv.get(t); cls=(meta.get('generic_uncertain',0),meta.get('generic_placeholders',0))
        if abbr not in byabbr:
            audited.append({'season':season,'game_id':g,'team_id':t,'status':'TEAM_MAP_GAP','class':str(cls)}); continue
        if cls not in safe_classes:
            audited.append({'season':season,'game_id':g,'team_id':t,'status':'CONTROL_CLASS_NOT_PROVEN','class':str(cls)}); continue
        v=byabbr[abbr]; recovered.append({'season':season,'game_id':g,'team_id':t,**v,'provenance':'PBP Stats possession rebound universe/team identity; PBP-only real/team-rebound classifier promoted only for exact zero-error control class'}); audited.append({'season':season,'game_id':g,'team_id':t,'status':'PROMOTED','class':str(cls)})
    def write(path,rows,cols):
        with open(path,'w',newline='') as f: w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); w.writerows(rows)
    write(O/f'PBP_TEAM_RECOVERED_{y}.csv',recovered,['season','game_id','team_id','team_oreb','team_dreb','opponent_oreb','opponent_dreb','provenance'])
    cols=sorted(set().union(*(r.keys() for r in audited))) if audited else ['season','game_id','team_id','status']; write(O/f'PBP_TEAM_AUDIT_{y}.csv',audited,cols)
    write(O/f'PBP_TEAM_CONTROLS_{y}.csv',control_results,['season','game_id','team_id','generic_uncertain','generic_placeholders','ok','got','expected'])
    qa={'season':season,'status':'PASS','missing_targets':len(missing),'controls':len(control_results),'control_mismatches':len(mismatches),'safe_classes':[{"generic_uncertain":c[0],"generic_placeholders":c[1],"n":class_stats[c][0]} for c in sorted(safe_classes)],'promoted':len(recovered),'integrity':{'lineup_used':False,'empirical_model_used':False,'rounded_rate_backsolve_used':False,'opponent_rebound_inference_used':False,'promotion_requires_zero_error_class_and_min20_controls':True}}
    (O/f'PBP_TEAM_QA_{y}.json').write_text(json.dumps(qa,indent=2)+'\n'); print(json.dumps(qa),flush=True)
if __name__=='__main__': main()
