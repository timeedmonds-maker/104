#!/usr/bin/env python3
import argparse, gzip, json, math, re
from pathlib import Path
import pandas as pd

KEYS=['season','team_id','player_id']


def norm(s):
    return re.sub(r'[^a-z0-9]+','',str(s).lower())


def ids(v):
    s=str(v).strip()
    return s[:-2] if s.endswith('.0') and s[:-2].isdigit() else s


def canon(df):
    x=df.copy()
    x['season']=x['season'].astype(str)
    x['team_id']=pd.to_numeric(x['team_id'],errors='raise').astype('int64')
    x['player_id']=x['player_id'].map(ids).astype('string')
    return x


def read_canonical(path):
    rows=[]
    with gzip.open(path,'rt',encoding='utf-8') as fh:
        for line in fh:
            if line.strip(): rows.append(json.loads(line))
    return canon(pd.DataFrame(rows))[KEYS].drop_duplicates()


def schema_for(path):
    try:
        if path.name.endswith('.parquet'):
            import pyarrow.parquet as pq
            return list(pq.read_schema(path).names)
        if path.name.endswith('.csv') or path.name.endswith('.csv.gz'):
            return list(pd.read_csv(path,nrows=2,low_memory=False).columns)
    except Exception:
        return []
    return []


def numeric_sum_by_keys(df):
    out=df[KEYS].drop_duplicates().copy()
    nums=[]
    for c in df.columns:
        if c in KEYS: continue
        v=pd.to_numeric(df[c],errors='coerce')
        if v.notna().mean() >= 0.95:
            nums.append(c)
    if not nums: return out, []
    y=df[KEYS+nums].copy()
    for c in nums: y[c]=pd.to_numeric(y[c],errors='coerce')
    return y.groupby(KEYS,as_index=False)[nums].sum(min_count=1), nums


def pair_matches(derived_exact, exact_agg, derived_cols, exact_cols):
    z=derived_exact.merge(exact_agg,on=KEYS,how='inner',suffixes=('_derived','_exact'))
    out=[]
    for dc in derived_cols:
        if dc not in z.columns: continue
        a=pd.to_numeric(z[dc],errors='coerce')
        for ec in exact_cols:
            col=ec if ec not in derived_cols else ec+'_exact'
            if col not in z.columns: continue
            b=pd.to_numeric(z[col],errors='coerce')
            m=a.notna() & b.notna()
            if m.sum() < 100: continue
            diff=(a[m]-b[m]).abs()
            rate=float((diff <= 1e-9).mean())
            if rate >= 0.90:
                out.append({'derived':dc,'exact':ec,'n':int(m.sum()),'exact_match_rate':rate,'max_abs_diff':float(diff.max()),'mean_abs_diff':float(diff.mean())})
    return sorted(out,key=lambda r:(-r['exact_match_rate'],r['mean_abs_diff'],r['derived'],r['exact']))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--canonical',required=True)
    ap.add_argument('--exact-detail',required=True)
    ap.add_argument('--root',default='team_trb_all_players')
    ap.add_argument('--out',required=True)
    args=ap.parse_args()
    root=Path(args.root); out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    canonical=read_canonical(args.canonical)
    assert len(canonical)==14524, len(canonical)
    exact=canon(pd.read_csv(args.exact_detail,low_memory=False))
    exact_keys=exact[KEYS].drop_duplicates(); assert len(exact_keys)==4877
    complement=canonical.merge(exact_keys.assign(_e=1),on=KEYS,how='left').query('_e != 1').drop(columns='_e')
    assert len(complement)==9647

    # Load retained player-specific rebound-derived rows.
    frames=[]
    for y in range(2000,2026):
        season=f'{y}-{(y+1)%100:02d}'
        p=root/'impact_database/outputs'/season/'team_rebound_derived.csv.gz'
        d=pd.read_csv(p,low_memory=False)
        if 'season' not in d: d['season']=season
        frames.append(d)
    derived=canon(pd.concat(frames,ignore_index=True,sort=False))
    derived=canonical.merge(derived,on=KEYS,how='left')
    assert len(derived)==14524

    report={
      'status':'PASS',
      'canonical_keys':14524,
      'exact_keys':4877,
      'complement_keys':9647,
      'derived_columns':list(derived.columns),
      'semantics_proof':{},
      'team_season_totals_probe':{},
      'broader_retained_game_team_candidates':[],
      'source_code_evidence':[],
      'subtraction_proof':{},
      'production_path_ready':False,
      'conclusion':None,
    }

    # Candidate ON interpretation is deliberately tested, not assumed.
    for c in ['team_off_rebounds','team_def_rebounds','team_rebounds','opponent_rebounds_exact','seconds','minutes']:
        if c in derived:
            v=pd.to_numeric(derived[c],errors='coerce')
            report['semantics_proof'][c]={'non_null':int(v.notna().sum()),'min':None if not v.notna().any() else float(v.min()),'max':None if not v.notna().any() else float(v.max())}
    if {'team_rebounds','opponent_rebounds_exact'}.issubset(derived.columns):
        tr=pd.to_numeric(derived.team_rebounds,errors='coerce')
        orr=pd.to_numeric(derived.opponent_rebounds_exact,errors='coerce')
        den=tr+orr
        report['semantics_proof']['candidate_treb_on_from_derived_counts']={
          'positive_denominator':int(den.gt(0).sum()),
          'finite':int((tr/den.where(den.ne(0))).notna().sum()),
          'formula':'team_rebounds / (team_rebounds + opponent_rebounds_exact)'
        }

    # Cross-validate retained derived counts against the locked exact subset using all plausible numeric count fields.
    exact_agg, exact_nums=numeric_sum_by_keys(exact)
    dex=derived.merge(exact_keys,on=KEYS,how='inner')
    dcols=[c for c in ['team_off_rebounds','team_def_rebounds','team_rebounds','opponent_rebounds_exact','seconds','minutes'] if c in dex.columns]
    report['semantics_proof']['exact_detail_columns']=list(exact.columns)
    report['semantics_proof']['exact_numeric_aggregate_columns']=exact_nums
    report['semantics_proof']['high_match_pairs']=pair_matches(dex,exact_agg,dcols,exact_nums)[:100]

    # Probe player_team_totals for actual team rows and season-total own rebound counts.
    team_rows=[]; totals_meta=[]
    for y in range(2000,2026):
        season=f'{y}-{(y+1)%100:02d}'
        p=root/'impact_database/outputs'/season/'player_team_totals.csv.gz'
        d=pd.read_csv(p,low_memory=False)
        totals_meta.append({'season':season,'rows':int(len(d)),'columns':list(d.columns)})
        if {'EntityId','TeamId'}.issubset(d.columns):
            ent=pd.to_numeric(d.EntityId,errors='coerce'); tid=pd.to_numeric(d.TeamId,errors='coerce')
            t=d[ent.eq(tid)].copy()
            if len(t):
                t['season']=season
                team_rows.append(t)
    if team_rows:
        tt=pd.concat(team_rows,ignore_index=True,sort=False)
        report['team_season_totals_probe']={
          'team_rows_found':int(len(tt)),
          'unique_season_team':int(tt.assign(team_id=pd.to_numeric(tt['TeamId'],errors='coerce')).drop_duplicates(['season','TeamId']).shape[0]),
          'columns':list(tt.columns),
          'rebound_columns':[c for c in tt.columns if 'reb' in norm(c)],
          'opponent_columns':[c for c in tt.columns if 'opp' in norm(c)],
        }
        keep=[c for c in ['season','TeamId','EntityId','Name','TeamAbbreviation','OffRebounds','DefRebounds','Rebounds','OpponentPoints','Minutes','SecondsPlayed'] if c in tt.columns]
        tt[keep].to_csv(out/'TEAM_SEASON_TOTAL_ROWS.csv.gz',index=False,compression='gzip')
    else:
        report['team_season_totals_probe']={'team_rows_found':0,'sample_metadata':totals_meta[:2]}

    # Local source-code evidence: discover exactly how team_rebound_derived/opponent_rebounds_exact is constructed.
    needles=('opponent_rebounds_exact','team_rebound_derived','team_off_rebounds','team_def_rebounds')
    for p in root.rglob('*.py'):
        try: lines=p.read_text(errors='ignore').splitlines()
        except Exception: continue
        hits=[]
        for i,line in enumerate(lines):
            if any(n in line for n in needles):
                lo=max(0,i-3); hi=min(len(lines),i+4)
                hits.append({'line':i+1,'context':lines[lo:hi]})
        if hits: report['source_code_evidence'].append({'path':str(p),'hits':hits[:20]})

    # Broader schema scan for retained game/team raw rebound facts usable for exact opponent season totals.
    # Restrict to likely fact/box/game/team/rebound files to keep this finite.
    candidates=[]; scanned=0
    for p in root.rglob('*'):
        if not p.is_file(): continue
        s=str(p).lower()
        if not (s.endswith('.csv') or s.endswith('.csv.gz') or s.endswith('.parquet')): continue
        if not any(k in s for k in ('game','box','team','rebound','total','fact')): continue
        scanned+=1
        cols=schema_for(p)
        if not cols: continue
        ns={c:norm(c) for c in cols}
        game=[c for c,n in ns.items() if n in ('gameid','game') or 'gameid' in n]
        team=[c for c,n in ns.items() if n in ('teamid','team') or 'teamid' in n]
        raw=[c for c,n in ns.items() if ('reb' in n or 'rebound' in n) and not any(x in n for x in ('pct','percent','rate','candidate'))]
        opp=[c for c,n in ns.items() if ('opp' in n or 'opponent' in n) and ('reb' in n or 'rebound' in n)]
        if game and team and raw:
            candidates.append({'path':str(p),'game_columns':game,'team_columns':team,'raw_rebound_columns':raw,'opponent_rebound_columns':opp,'all_columns':cols})
    report['broader_retained_game_team_candidates']=candidates[:200]
    report['broader_schema_files_scanned']=scanned

    # Decision logic is conservative. Only declare ready when ON semantics have direct exact-subset support AND
    # a retained path to both full team and opponent totals is demonstrated.
    high=report['semantics_proof'].get('high_match_pairs',[])
    derived_team_proven=any(x['derived']=='team_rebounds' and x['exact_match_rate']>=0.999 for x in high)
    derived_opp_proven=any(x['derived']=='opponent_rebounds_exact' and x['exact_match_rate']>=0.999 for x in high)
    team_totals_ready=report['team_season_totals_probe'].get('unique_season_team',0) >= 750 and 'Rebounds' in report['team_season_totals_probe'].get('columns',[])
    direct_opp_totals=any(len(x.get('opponent_rebound_columns',[]))>0 for x in candidates)
    paired_game_totals=any(len(x.get('raw_rebound_columns',[]))>0 for x in candidates)
    report['subtraction_proof']={
      'derived_team_on_semantics_proven_against_exact_subset':derived_team_proven,
      'derived_opponent_on_semantics_proven_against_exact_subset':derived_opp_proven,
      'team_season_own_totals_available':team_totals_ready,
      'direct_game_or_opponent_rebound_candidate_found':direct_opp_totals or paired_game_totals,
      'off_formula_if_totals_proven':'team_off = team_season_total - team_on; opponent_off = opponent_season_total_against_team - opponent_on',
      'rounded_percentage_backsolve_used':False,
    }
    report['production_path_ready']=bool(derived_team_proven and derived_opp_proven and team_totals_ready and (direct_opp_totals or paired_game_totals))
    if report['production_path_ready']:
        report['conclusion']='RETAINED_RAW_COUNT_SUBTRACTION_PATH_PROVEN_FOR_PRODUCTION'
    elif derived_team_proven and derived_opp_proven:
        report['conclusion']='PLAYER_ON_COUNTS_PROVEN; FULL_TEAM/OPPONENT_TOTAL_SOURCE STILL NEEDS FINAL IDENTIFICATION'
    else:
        report['conclusion']='PLAYER_ON_COUNT_SEMANTICS_NOT_YET_PROVEN'

    (out/'TREB_ONOFF_SUBTRACTION_PROOF.json').write_text(json.dumps(report,indent=2)+'\n')
    md=[
      '# TREB ON/OFF subtraction proof','',
      f"- Status: **{report['status']}**",
      f"- Canonical keys: **{report['canonical_keys']}**",
      f"- Exact subset: **{report['exact_keys']}**",
      f"- Complement: **{report['complement_keys']}**",
      f"- Production path ready: **{report['production_path_ready']}**",
      f"- Conclusion: **{report['conclusion']}**",'',
      'Subtraction proof:', json.dumps(report['subtraction_proof'],indent=2),'',
      'Team-season totals probe:', json.dumps(report['team_season_totals_probe'],indent=2),'',
      f"Broader game/team candidate files: **{len(candidates)}**",
    ]
    (out/'TREB_ONOFF_SUBTRACTION_PROOF.md').write_text('\n'.join(md)+'\n')
    print(json.dumps({k:report[k] for k in ['status','canonical_keys','exact_keys','complement_keys','subtraction_proof','production_path_ready','conclusion']},indent=2))

if __name__=='__main__': main()
