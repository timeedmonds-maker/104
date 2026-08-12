#!/usr/bin/env python3
"""Promote the finite direct-team rebound repair evidence after both control gates pass."""
from __future__ import annotations
import argparse,json,re
from pathlib import Path

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--direct',type=Path,required=True);ap.add_argument('--prefix',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    d=json.loads(a.direct.read_text());p=json.loads(a.prefix.read_text())
    assert d['status']=='COMPLETE' and d['safe_rule'] is True
    assert int(d['controls']['unique_candidate_controls'])==718
    assert int(d['controls']['identity_correct'])==718 and int(d['controls']['identity_wrong'])==0
    assert p['status']=='COMPLETE' and p['safe_rule'] is True
    assert int(p['counts']['team_correct'])==610 and int(p['counts']['team_wrong'])==0
    assert int(p['counts']['prefix_unresolved'])==0 and int(p['counts']['actual_team_unresolved'])==0
    cand=d['candidates'];assert len(cand)==50
    seen_pbp=set();seen_nba=set();rep=[]
    for r in cand:
        assert r['method']=='bracket_tricode'
        assert re.fullmatch(r'\[[A-Z]{2,4}\] Team Rebound',r['description'])
        assert r['candidate_count']==1 and r['candidate_unused'] is True
        c=r['candidate'];assert c is not None
        assert int(c['player1_id'])==int(r['resolved_team_id'])
        pk=(int(r['game_id']),int(r['pbp_index']));nk=(int(r['game_id']),int(c['nba_index']))
        assert pk not in seen_pbp and nk not in seen_nba
        seen_pbp.add(pk);seen_nba.add(nk)
        rep.append({
          'game_id':int(r['game_id']),'pbp_index':int(r['pbp_index']),'period':int(r['period']),
          'start_time':str(r['start_time']),'end_time':str(r['end_time']),'pbp_description':str(r['description']),
          'resolved_team_id':int(r['resolved_team_id']),'resolution_method':'bracket_tricode_plus_unique_unused_team_rebound_in_interval',
          'nba_index_audit_only':int(c['nba_index']),'nba_eventnum':int(c['eventnum']),'nba_elapsed':int(c['elapsed']),
          'nba_player1_id':int(c['player1_id']),'nba_description':str(c['description']),
          'lineup':[int(x) for x in c['lineup']],'real':bool(c['real'])
        })
    out={
      'status':'PROMOTED','method':'finite_direct_team_event_identity','repair_rows':len(rep),'repair_games':len({r['game_id'] for r in rep}),
      'direct_team_control_correct':718,'direct_team_control_wrong':0,'bracket_prefix_control_correct':610,'bracket_prefix_control_wrong':0,
      'live_repair_rows':sum(bool(r['real']) for r in rep),'dead_placeholder_rows':sum(not bool(r['real']) for r in rep),
      'repairs':rep,
    }
    assert out['live_repair_rows']==25 and out['dead_placeholder_rows']==25
    a.output.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({k:v for k,v in out.items() if k!='repairs'},indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
