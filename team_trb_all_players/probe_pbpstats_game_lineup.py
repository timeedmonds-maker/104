#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,time
from pathlib import Path
import requests

URL='https://api.pbpstats.com/get-game-stats'

def shape(v,depth=0):
    if depth>=3:return type(v).__name__
    if isinstance(v,dict):
        keys=list(v)
        return {'type':'dict','len':len(v),'keys':keys[:30],'sample':{k:shape(v[k],depth+1) for k in keys[:3]}}
    if isinstance(v,list):return {'type':'list','len':len(v),'sample':[shape(x,depth+1) for x in v[:2]]}
    return {'type':type(v).__name__,'value':v}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--game-id',required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    payload={'game_id':a.game_id,'endpoint':URL,'requests':{}}
    for typ in ('Lineup','LineupOpponent','Player'):
        params={'GameId':a.game_id,'Type':typ};r=requests.get(URL,params=params,timeout=60)
        item={'status_code':r.status_code,'url':r.url,'bytes':len(r.content)}
        try:
            data=r.json();item['shape']=shape(data)
            if r.status_code==200:item['raw_response']=data
            else:item['error_response']=data
        except Exception as exc:item['json_error']=str(exc);item['body_prefix']=r.text[:1000]
        payload['requests'][typ]=item;time.sleep(1)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2,default=str)+'\n')
    print(json.dumps({k:{'status_code':v['status_code'],'bytes':v['bytes'],'shape':v.get('shape')} for k,v in payload['requests'].items()},indent=2))
if __name__=='__main__':main()
