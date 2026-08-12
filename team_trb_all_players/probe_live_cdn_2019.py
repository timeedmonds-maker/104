#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd
import modern_cdn_lineups as modern

GAME_IDS=[21901316,21901317,21901318]

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--raw',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); a=ap.parse_args()
    out=[]
    for gid in GAME_IDS:
        p=a.raw/f'{gid}.json'
        row={'game_id':gid,'file_exists':p.exists(),'bytes':p.stat().st_size if p.exists() else 0}
        if not p.exists() or p.stat().st_size==0:
            row['status']='SOURCE_MISSING'; out.append(row); continue
        try:
            payload=json.loads(p.read_text())
            actions=payload.get('game',{}).get('actions',[])
            g=pd.DataFrame(actions)
            row['actions']=len(g)
            lu=modern.reconstruct_game_lineups(g)
            row.update({'status':'PASS_CDN_LIVE','players_with_seconds':len(lu.seconds),'repairs':lu.repairs})
        except Exception as exc:
            row.update({'status':'FAIL','error':str(exc)})
        out.append(row)
    payload={'games':out,'status_counts':{s:sum(r['status']==s for r in out) for s in sorted({r['status'] for r in out})}}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    print(json.dumps({'status_counts':payload['status_counts'],'games':[(r['game_id'],r['status']) for r in out]},indent=2))
    return 0
if __name__=='__main__': raise SystemExit(main())
