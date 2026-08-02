import json
from pathlib import Path
import pandas as pd

OUT=Path('team_trb_all_players/output'); OUT.mkdir(parents=True, exist_ok=True)
urls=[
 'https://huggingface.co/datasets/cdechoch/nba-data-archive/resolve/main/per_season/pbpstats/2024.parquet',
 'https://huggingface.co/datasets/cdechoch/nba-data-archive/resolve/main/per_season/statsnba/2024.parquet',
]
report=[]
for url in urls:
    item={'url':url}
    try:
        df=pd.read_parquet(url)
        item.update({'ok':True,'rows':len(df),'columns':list(df.columns),'dtypes':{c:str(t) for c,t in df.dtypes.items()},'sample':df.head(3).astype(str).to_dict('records')})
    except Exception as e:
        item.update({'ok':False,'error':repr(e)})
    report.append(item)
(OUT/'hf_schema_report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2)[:30000])
if not any(x['ok'] for x in report): raise SystemExit(2)
