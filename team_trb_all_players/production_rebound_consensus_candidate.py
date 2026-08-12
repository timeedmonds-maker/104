#!/usr/bin/env python3
"""Candidate rebound layer using consensus rather than forced event identity.

For an unmatched PBP rebound, exact NBA event identity is unnecessary for TREB if
all NBA rebound events inside the already-locked ±5-second legal join window
agree on the ten-player lineup.  For generic team rebounds they must also agree
on real-vs-placeholder status.  Player-credited `(Off:n Def:n)` rows are real by
construction.  If no NBA rebound exists in the window, only a fully invariant
padded event window plus a PBP-proven non-live generic rebound is accepted.

This module is candidate-only until full residual and control canaries pass.
"""
from __future__ import annotations
import re
import pandas as pd
import local_treb_rebuild as core
import production_treb_engine as legacy

COUNTER_RE=re.compile(r"\(\s*off\s*:\s*\d+\s+def\s*:\s*\d+\s*\)",re.I)


def _norm(v):
    if pd.isna(v): return ''
    return re.sub(r"\s+"," ",str(v)).strip().lower()


def _padded_events(nba,row,alpha=5):
    lo=min(int(row.START_ELAPSED),int(row.END_ELAPSED))-alpha
    hi=max(int(row.START_ELAPSED),int(row.END_ELAPSED))+alpha
    return nba[nba.PERIOD.eq(row.PERIOD)&nba.ELAPSED.ge(lo)&nba.ELAPSED.le(hi)].copy()


def _rebound_candidates(nba,row,alpha=5):
    return _padded_events(nba,row,alpha).loc[lambda x:x.EVENTMSGTYPE.eq(4)].copy()


def _lineup_consensus(cands):
    if len(cands)==0: return None
    vals={tuple(int(x) for x in lu) for lu in cands.LINEUP}
    return next(iter(vals)) if len(vals)==1 else None


def _real_consensus(nba,cands):
    if len(cands)==0: return None
    vals={bool(core._nba_real_rebound(nba,int(i))) for i in cands.index}
    return next(iter(vals)) if len(vals)==1 else None


def _full_window_invariant(nba,row,alpha=5):
    span=_padded_events(nba,row,alpha)
    if len(span)==0 or bool(span.EVENTMSGTYPE.eq(8).any()): return None
    return _lineup_consensus(span)


def _pbp_definitely_nonlive(row):
    desc=str(row.DESCRIPTION); prev=str(row.PREV_PBP_DESCRIPTION) if pd.notna(row.PREV_PBP_DESCRIPTION) else ''
    generic=not bool(COUNTER_RE.search(desc))
    if not generic: return False
    non_live_ft=bool(re.search(r"Free Throw (?:1 of [23]|2 of 3)|Technical Free Throw|Flagrant Free Throw",prev,re.I))
    turnover_placeholder=bool(re.search(r"Turnover|Violation",prev,re.I))
    buzzer=str(row.ENDTIME)=='00:00'
    zero_interval=str(row.STARTTIME)==str(row.ENDTIME)
    return bool(non_live_ft or turnover_placeholder or buzzer or zero_interval)


def join_pbp_rebounds(lineups:core.GameLineups,pbp_game:pd.DataFrame,alpha:int=5):
    ordered=pbp_game.copy(); ordered['PREV_PBP_DESCRIPTION']=ordered.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    rebounds=ordered[ordered.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy(); rebounds['DESCRIPTION_NORM']=rebounds.DESCRIPTION.map(_norm)
    rebounds['START_ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(rebounds.PERIOD,rebounds.STARTTIME)]; rebounds['END_ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(rebounds.PERIOD,rebounds.ENDTIME)]
    rows=list(rebounds.iterrows()); nba=lineups.events; game_id=int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0

    matches=[]; ambiguous=manual=0
    for _,row in rows:
        candidates=nba[nba.PERIOD.eq(row.PERIOD)&nba.ELAPSED.gt(row.START_ELAPSED-alpha)&nba.ELAPSED.lt(row.END_ELAPSED+alpha)]
        scored=[(core._distance(row.DESCRIPTION_NORM,desc),int(ev),int(pos)) for pos,(ev,desc) in zip(candidates.index,zip(candidates.EVENTNUM,candidates.DESCRIPTION_NORM))]
        acceptable=[x for x in scored if x[0] < .2]
        if len(acceptable)>1: ambiguous+=1
        if acceptable: matches.append(min(acceptable)[2]); continue
        repair_event=legacy.JOIN_REPAIRS.get((game_id,int(row.PERIOD),row.DESCRIPTION_NORM))
        if repair_event is not None:
            hit=nba[nba.PERIOD.eq(row.PERIOD)&nba.EVENTNUM.eq(repair_event)]
            if len(hit)==1: matches.append(int(hit.index[0])); manual+=1; continue
        matches.append(None)

    used={int(x) for x in matches if x is not None}; exact_identity=exact_description=exact_player_counter=0; exact_records=[]
    def name_key(v): return _norm(v).split(' rebound',1)[0].strip()
    exact_counter_re=re.compile(r"\(off:(\d+) def:(\d+)\)",re.I)
    for pos,(_,row) in enumerate(rows):
        if matches[pos] is not None: continue
        eligible=nba[nba.PERIOD.eq(row.PERIOD)&nba.EVENTMSGTYPE.eq(4)&nba.ELAPSED.gt(row.START_ELAPSED-alpha)&nba.ELAPSED.lt(row.END_ELAPSED+alpha)&~nba.index.isin(used)]
        chosen=None; method=None; exact=eligible[eligible.DESCRIPTION_NORM.eq(row.DESCRIPTION_NORM)]
        if len(exact)==1: chosen=int(exact.index[0]); method='exact_description'
        else:
            m=exact_counter_re.search(row.DESCRIPTION_NORM)
            if m:
                ck=f"(off:{m.group(1)} def:{m.group(2)})"; pk=name_key(row.DESCRIPTION_NORM); hits=eligible[eligible.DESCRIPTION_NORM.str.contains(re.escape(ck),regex=True)&eligible.DESCRIPTION_NORM.map(name_key).eq(pk)]
                if len(hits)==1: chosen=int(hits.index[0]); method='exact_player_counter'
        if chosen is not None:
            matches[pos]=chosen; used.add(chosen); exact_identity+=1; exact_description+=int(method=='exact_description'); exact_player_counter+=int(method=='exact_player_counter'); exact_records.append({'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'pbp_description':str(row.DESCRIPTION),'nba_eventnum':int(nba.loc[chosen,'EVENTNUM']),'nba_elapsed':int(nba.loc[chosen,'ELAPSED']),'method':method})

    synthetic={}; records=[]; player_repairs=generic_repairs=nonlive_no_candidate=0
    for pos,(idx,row) in enumerate(rows):
        if matches[pos] is not None: continue
        cands=_rebound_candidates(nba,row,alpha); lineup=_lineup_consensus(cands)
        credited=bool(COUNTER_RE.search(str(row.DESCRIPTION)))
        real=True if credited else _real_consensus(nba,cands)
        method='rebound_candidate_consensus'
        if len(cands)==0:
            lineup=_full_window_invariant(nba,row,alpha)
            if lineup is not None and _pbp_definitely_nonlive(row):
                real=False; method='padded_invariant_pbp_nonlive'; nonlive_no_candidate+=1
        if lineup is None or real is None: continue
        synthetic[idx]={'lineup':lineup,'real':bool(real)}; player_repairs+=int(credited); generic_repairs+=int(not credited)
        records.append({'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'pbp_description':str(row.DESCRIPTION),'candidate_rebounds':int(len(cands)),'lineup':[int(x) for x in lineup],'nba_is_real_rebound':bool(real),'method':method})

    unmatched=[]
    for pos,(idx,row) in enumerate(rows):
        if matches[pos] is None and idx not in synthetic: unmatched.append({'game_id':game_id,'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'description':str(row.DESCRIPTION)})
    rebounds['NBA_INDEX']=matches; keep=rebounds.NBA_INDEX.notna()|rebounds.index.isin(synthetic); matched=rebounds[keep].copy()
    matched['LINEUP']=[nba.loc[int(i),'LINEUP'] if pd.notna(i) else synthetic[idx]['lineup'] for idx,i in matched.NBA_INDEX.items()]
    for col in ('EVENTMSGTYPE','EVENTMSGACTIONTYPE','PLAYER1_ID','ELAPSED','EVENTNUM'): matched['NBA_'+col]=[nba.loc[int(i),col] if pd.notna(i) else pd.NA for idx,i in matched.NBA_INDEX.items()]
    matched['NBA_IS_REAL_REBOUND']=[core._nba_real_rebound(nba,int(i)) if pd.notna(i) else synthetic[idx]['real'] for idx,i in matched.NBA_INDEX.items()]
    audit={'total_pbp_rows':int(len(pbp_game)),'rebound_bearing_rows':int(len(rebounds)),'matched_rebound_bearing_rows':int(len(matched)),'unmatched_rebound_bearing_rows':int(len(unmatched)),'ambiguous_matches':int(ambiguous),'manual_join_repairs':int(manual),'exact_identity_join_repairs':int(exact_identity),'exact_description_repairs':int(exact_description),'exact_player_counter_repairs':int(exact_player_counter),'exact_identity_records':exact_records,'consensus_semantic_join_repairs':int(len(synthetic)),'consensus_semantic_player_repairs':int(player_repairs),'consensus_semantic_generic_repairs':int(generic_repairs),'consensus_semantic_no_candidate_nonlive_repairs':int(nonlive_no_candidate),'consensus_semantic_records':records,'unmatched_rows':unmatched}
    return matched,audit


def classify_rebounds(pbp_game): return legacy.classify_rebounds(pbp_game)
