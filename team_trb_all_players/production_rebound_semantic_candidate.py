#!/usr/bin/env python3
"""Candidate production rebound layer with invariant semantic fallback.

NOT wired into production until its cross-era controls pass exactly.
"""
from __future__ import annotations
import re
import pandas as pd
import local_treb_rebuild as core
import production_treb_engine as legacy


def _norm(value: object) -> str:
    if pd.isna(value): return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def _invariant_lineup(nba: pd.DataFrame, row: pd.Series):
    lo=int(row.START_ELAPSED); hi=int(row.END_ELAPSED)
    if hi < lo: lo,hi=hi,lo
    span=nba[nba.PERIOD.eq(row.PERIOD)&nba.ELAPSED.ge(lo)&nba.ELAPSED.le(hi)]
    if len(span)==0 or bool(span.EVENTMSGTYPE.eq(8).any()): return None
    lineups={tuple(int(x) for x in lu) for lu in span.LINEUP}
    if len(lineups)!=1: return None
    return next(iter(lineups))


def _consensus_real(nba: pd.DataFrame, row: pd.Series, alpha: int):
    cands=nba[nba.PERIOD.eq(row.PERIOD)&nba.EVENTMSGTYPE.eq(4)&nba.ELAPSED.gt(row.START_ELAPSED-alpha)&nba.ELAPSED.lt(row.END_ELAPSED+alpha)]
    if len(cands)==0: return None
    vals={bool(core._nba_real_rebound(nba,int(i))) for i in cands.index}
    return next(iter(vals)) if len(vals)==1 else None


def join_pbp_rebounds(lineups: core.GameLineups, pbp_game: pd.DataFrame, alpha: int = 5) -> tuple[pd.DataFrame, dict]:
    ordered_pbp=pbp_game.copy()
    ordered_pbp['PREV_PBP_DESCRIPTION']=ordered_pbp.groupby(core.POSSESSION_ID,dropna=False).DESCRIPTION.shift(1)
    rebounds=ordered_pbp[ordered_pbp.DESCRIPTION.fillna('').str.contains('rebound',case=False)].copy()
    rebounds['DESCRIPTION_NORM']=rebounds.DESCRIPTION.map(_norm)
    rebounds['START_ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(rebounds.PERIOD,rebounds.STARTTIME)]
    rebounds['END_ELAPSED']=[core.elapsed_seconds(int(p),c) for p,c in zip(rebounds.PERIOD,rebounds.ENDTIME)]
    rows=list(rebounds.iterrows()); nba=lineups.events; game_id=int(pbp_game.GAMEID.iloc[0]) if not pbp_game.empty else 0

    matches=[]; ambiguous=manual=0
    for _,row in rows:
        candidates=nba[nba.PERIOD.eq(row.PERIOD)&nba.ELAPSED.gt(row.START_ELAPSED-alpha)&nba.ELAPSED.lt(row.END_ELAPSED+alpha)]
        scored=[(core._distance(row.DESCRIPTION_NORM,desc),int(ev),int(pos)) for pos,(ev,desc) in zip(candidates.index,zip(candidates.EVENTNUM,candidates.DESCRIPTION_NORM))]
        acceptable=[item for item in scored if item[0] < .2]
        if len(acceptable)>1: ambiguous+=1
        if acceptable:
            matches.append(min(acceptable)[2]); continue
        repair_event=legacy.JOIN_REPAIRS.get((game_id,int(row.PERIOD),row.DESCRIPTION_NORM))
        if repair_event is not None:
            hit=nba[nba.PERIOD.eq(row.PERIOD)&nba.EVENTNUM.eq(repair_event)]
            if len(hit)==1:
                matches.append(int(hit.index[0])); manual+=1; continue
        matches.append(None)

    used={int(x) for x in matches if x is not None}
    counter_re=re.compile(r"\(off:(\d+) def:(\d+)\)",re.I)
    def name_key(value): return _norm(value).split(' rebound',1)[0].strip()
    exact_identity=exact_description=exact_player_counter=0; exact_records=[]
    for pos,(_,row) in enumerate(rows):
        if matches[pos] is not None: continue
        eligible=nba[nba.PERIOD.eq(row.PERIOD)&nba.EVENTMSGTYPE.eq(4)&nba.ELAPSED.gt(row.START_ELAPSED-alpha)&nba.ELAPSED.lt(row.END_ELAPSED+alpha)&~nba.index.isin(used)]
        chosen=None; method=None
        exact=eligible[eligible.DESCRIPTION_NORM.eq(row.DESCRIPTION_NORM)]
        if len(exact)==1: chosen=int(exact.index[0]); method='exact_description'
        else:
            counter=counter_re.search(row.DESCRIPTION_NORM)
            if counter:
                counter_key=f"(off:{counter.group(1)} def:{counter.group(2)})"; player_key=name_key(row.DESCRIPTION_NORM)
                hits=eligible[eligible.DESCRIPTION_NORM.str.contains(re.escape(counter_key),regex=True)&eligible.DESCRIPTION_NORM.map(name_key).eq(player_key)]
                if len(hits)==1: chosen=int(hits.index[0]); method='exact_player_counter'
        if chosen is not None:
            matches[pos]=chosen; used.add(chosen); exact_identity+=1; exact_description+=int(method=='exact_description'); exact_player_counter+=int(method=='exact_player_counter')
            exact_records.append({'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'pbp_description':str(row.DESCRIPTION),'nba_eventnum':int(nba.loc[chosen,'EVENTNUM']),'nba_elapsed':int(nba.loc[chosen,'ELAPSED']),'method':method})

    synthetic_by_index={}; semantic_records=[]; semantic_player=semantic_generic=0
    for pos,(pbp_idx,row) in enumerate(rows):
        if matches[pos] is not None: continue
        lineup=_invariant_lineup(nba,row)
        if lineup is None: continue
        player_credited=bool(counter_re.search(row.DESCRIPTION_NORM))
        real=True if player_credited else _consensus_real(nba,row,alpha)
        if real is None: continue
        synthetic_by_index[pbp_idx]={'lineup':lineup,'real':bool(real),'player_credited':player_credited}
        semantic_player+=int(player_credited); semantic_generic+=int(not player_credited)
        semantic_records.append({'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'pbp_description':str(row.DESCRIPTION),'lineup':[int(x) for x in lineup],'nba_is_real_rebound':bool(real),'method':'player_credit_invariant_lineup' if player_credited else 'generic_consensus_real_invariant_lineup'})

    unmatched=[]
    for pos,(pbp_idx,row) in enumerate(rows):
        if matches[pos] is None and pbp_idx not in synthetic_by_index:
            unmatched.append({'game_id':game_id,'period':int(row.PERIOD),'start_time':str(row.STARTTIME),'end_time':str(row.ENDTIME),'description':str(row.DESCRIPTION)})

    rebounds['NBA_INDEX']=matches
    keep=rebounds.NBA_INDEX.notna()|rebounds.index.isin(synthetic_by_index)
    matched=rebounds[keep].copy()
    matched['LINEUP']=[nba.loc[int(i),'LINEUP'] if pd.notna(i) else synthetic_by_index[idx]['lineup'] for idx,i in matched.NBA_INDEX.items()]
    for column in ('EVENTMSGTYPE','EVENTMSGACTIONTYPE','PLAYER1_ID','ELAPSED','EVENTNUM'):
        matched['NBA_'+column]=[nba.loc[int(i),column] if pd.notna(i) else pd.NA for idx,i in matched.NBA_INDEX.items()]
    matched['NBA_IS_REAL_REBOUND']=[core._nba_real_rebound(nba,int(i)) if pd.notna(i) else synthetic_by_index[idx]['real'] for idx,i in matched.NBA_INDEX.items()]
    audit={'total_pbp_rows':int(len(pbp_game)),'rebound_bearing_rows':int(len(rebounds)),'matched_rebound_bearing_rows':int(len(matched)),'unmatched_rebound_bearing_rows':int(len(unmatched)),'ambiguous_matches':int(ambiguous),'manual_join_repairs':int(manual),'exact_identity_join_repairs':int(exact_identity),'exact_description_repairs':int(exact_description),'exact_player_counter_repairs':int(exact_player_counter),'exact_identity_records':exact_records,'invariant_semantic_join_repairs':int(len(synthetic_by_index)),'invariant_semantic_player_repairs':int(semantic_player),'invariant_semantic_generic_repairs':int(semantic_generic),'invariant_semantic_records':semantic_records,'unmatched_rows':unmatched}
    return matched,audit


def classify_rebounds(pbp_game: pd.DataFrame) -> pd.DataFrame:
    return legacy.classify_rebounds(pbp_game)
