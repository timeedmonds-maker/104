#!/usr/bin/env python3
"""Promote the finite seven-row V5 source-only player repair class."""
from __future__ import annotations
import argparse,json,re
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--audit',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    d=json.loads(a.audit.read_text());assert d['status']=='COMPLETE' and int(d['current_residual_rows'])==27
    lc=d['lineup_controls']['interval_invariant'];assert lc=={'applicable':4682,'correct':4682,'wrong':0};assert d['lineup_safe']['interval_invariant'] is True
    rn=d['live_controls']['resolved_named_player'];assert rn=={'applicable':5034,'correct':5034,'wrong':0};assert d['live_safe']['resolved_named_player'] is True
    cc=d['live_controls']['counter_credited_player'];assert cc=={'applicable':5163,'correct':5163,'wrong':0};assert d['live_safe']['counter_credited_player'] is True
    cand=d['repair_candidates'];assert len(cand)==7
    outrows=[];seen=set()
    for r in cand:
        assert r['lineup_methods']==['interval_invariant']
        assert 'resolved_named_player' in r['live_methods'] and r['live'] is True
        assert r['resolved_player_id'] is not None
        assert not re.search(r'\bTeam\s+Rebound\b',r['description'],re.I)
        k=(int(r['game_id']),int(r['pbp_index']));assert k not in seen;seen.add(k)
        outrows.append({
          'game_id':int(r['game_id']),'pbp_index_audit_only':int(r['pbp_index']),'period':int(r['period']),
          'start_time':str(r['start_time']),'end_time':str(r['end_time']),'pbp_description':str(r['description']),
          'resolved_player_id':int(r['resolved_player_id']),'lineup':[int(x) for x in r['lineup']],
          'real':True,'pbp_is_oreb_audit_only':bool(r['pbp_is_oreb']),
          'resolution_method':'pbp_interval_lineup_invariant_plus_unique_named_player_live'
        })
    out={'status':'PROMOTED','method':'finite_source_only_player_synthesis','repair_rows':7,'repair_games':len({r['game_id'] for r in outrows}),'lineup_control_correct':4682,'lineup_control_wrong':0,'named_player_live_control_correct':5034,'named_player_live_control_wrong':0,'repairs':outrows}
    a.output.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({k:v for k,v in out.items() if k!='repairs'},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
