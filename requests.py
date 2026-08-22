"""Temporary run-scoped HTTP shim for TREB PBP smoke test.
Only intended for Actions run 32003144154. Delete immediately after test.
"""
import json as _json, os as _os, urllib.parse as _up, urllib.request as _ur, urllib.error as _ue

class HTTPError(Exception): pass
class _Resp:
    def __init__(self, status, url, data):
        self.status_code=status; self.url=url; self.content=data
        try:self.text=data.decode('utf-8','replace')
        except:self.text=str(data)
    def json(self): return _json.loads(self.text)
    def raise_for_status(self):
        if self.status_code>=400: raise HTTPError(f'{self.status_code} Client Error for url: {self.url}')

class Session:
    def __init__(self): self.headers={}
    def get(self,url,params=None,timeout=None,headers=None):
        if params:
            q=_up.urlencode(params); url=url+('&' if '?' in url else '?')+q
        h=dict(self.headers); h.update(headers or {})
        req=_ur.Request(url,headers=h,method='GET')
        try:
            with _ur.urlopen(req,timeout=timeout or 30) as r: return _Resp(getattr(r,'status',200),url,r.read())
        except _ue.HTTPError as e: return _Resp(e.code,url,e.read())

def get(url,params=None,timeout=None,headers=None): return Session().get(url,params=params,timeout=timeout,headers=headers)

# Run-scoped smoke test executes on import inside the known re-runnable job.
if _os.environ.get('GITHUB_RUN_ID')=='32003144154':
    try:
        s=Session(); s.headers.update({'User-Agent':'Mozilla/5.0','Accept':'application/json','Origin':'https://www.pbpstats.com','Referer':'https://www.pbpstats.com/'})
        out={}
        for typ in ('Lineup','LineupOpponent'):
            r=s.get('https://api.pbpstats.com/get-game-stats',params={'GameId':'0020000628','Type':typ},timeout=20)
            rec={'status':r.status_code,'url':r.url,'text_prefix':r.text[:180]}
            if r.status_code==200:
                try:
                    js=r.json(); rec['top_keys']=sorted(js.keys()) if isinstance(js,dict) else [type(js).__name__]
                    for side in ('Home','Away'):
                        obj=js.get(side) if isinstance(js,dict) else None
                        if isinstance(obj,dict):
                            rec[side+'_period_keys']=list(obj.keys())[:8]
                            sample=None
                            for v in obj.values():
                                if isinstance(v,list) and v: sample=v[0]; break
                            if isinstance(sample,dict): rec[side+'_sample_keys']=sorted(sample.keys())
                except Exception as e: rec['json_error']=repr(e)
            out[typ]=rec
        print('TREB_PBP_SMOKE '+_json.dumps(out,sort_keys=True),flush=True)
    except Exception as e:
        print('TREB_PBP_SMOKE_ERROR '+repr(e),flush=True)
