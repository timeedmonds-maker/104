#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,time
from pathlib import Path
import requests

URL='https://api.pbpstats.com/get-game-stats'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--game-id',required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    payload={'game_id':a.game_id,'endpoint':URL,'requests':{}}
    for typ in ('Lineup','LineupOpponent','Player'):
        params={'GameId':a.game_id,'Type':typ}
        r=requests.get(URL,params=params,timeout=60)
        item={'status_code':r.status_code,'url':r.url,'bytes':len(r.content)}
        try:
            data=r.json();item['top_level_keys']=list(data) if isinstance(data,dict) else None
            if isinstance(data,dict):
                item['response_summary']={k:(len(v) if isinstance(v,list) else type(v).__name__) for k,v in data.items()}
                samples={}
                for k,v in data.items():
                    if isinstance(v,list) and v:samples[k]=v[:3]
                item['samples']=samples
            else:item['json_type']=type(data).__name__
        except Exception as exc:item['json_error']=str(exc);item['body_prefix']=r.text[:1000]
        payload['requests'][typ]=item
        time.sleep(1)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    print(json.dumps({k:{'status_code':v['status_code'],'top_level_keys':v.get('top_level_keys'),'response_summary':v.get('response_summary')} for k,v in payload['requests'].items()},indent=2))
if __name__=='__main__':main()
