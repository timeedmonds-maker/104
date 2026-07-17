import json
import requests
from pathlib import Path

out = Path('probe_output')
out.mkdir(exist_ok=True)
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
    'Referer': 'https://www.nba.com/',
    'Origin': 'https://www.nba.com',
    'Accept': 'application/json, text/plain, */*',
}
queries = {
    'dwight_2022': (2730, '2021-22'),
    'pau_2019': (2200, '2018-19'),
    'duncan_1998': (1495, '1997-98'),
    'millsap_2022': (200794, '2021-22'),
    'nene_2020': (2403, '2019-20'),
}
base='https://stats.nba.com/stats/playerdashboardonoffdetails'
for name,(pid,season) in queries.items():
    params = {
        'DateFrom':'','DateTo':'','GameSegment':'','LastNGames':'0','LeagueID':'00','Location':'',
        'MeasureType':'Advanced','Month':'0','OpponentTeamID':'0','Outcome':'','PaceAdjust':'N',
        'PerMode':'Totals','Period':'0','PlayerID':str(pid),'PlusMinus':'N','Rank':'N',
        'Season':season,'SeasonSegment':'','SeasonType':'Regular Season','VsConference':'','VsDivision':''
    }
    try:
        r=requests.get(base,params=params,headers=headers,timeout=120)
        print(name,r.status_code,len(r.text),r.url,flush=True)
        print(r.text[:300],flush=True)
        (out/f'{name}.json').write_text(r.text,encoding='utf-8')
    except Exception as exc:
        print(name,type(exc).__name__,exc,flush=True)
        (out/f'{name}_error.txt').write_text(repr(exc),encoding='utf-8')
