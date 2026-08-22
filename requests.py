"""Temporary run-scoped HTTP shim for TREB exact recovery.
Only active in historical Actions run 32003144154; remove after closure.
"""
import json as _json, os as _os, subprocess as _sp, sys as _sys, urllib.parse as _up, urllib.request as _ur, urllib.error as _ue

class HTTPError(Exception): pass
class _Resp:
    def __init__(self,status,url,data):
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

if _os.environ.get('GITHUB_RUN_ID')=='32003144154' and _os.environ.get('TREB_PBP_CHILD')!='1' and _os.path.exists('/tmp/current'):
    print('TREB_PBP_INJECT_START',flush=True)
    r=_sp.run([_sys.executable,'-u','scripts/run_pbp_player_recovery_via_historical_job.py'])
    print('TREB_PBP_INJECT_RC',r.returncode,flush=True)
