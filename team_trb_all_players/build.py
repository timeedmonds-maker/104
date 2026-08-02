import json, pathlib, requests, pandas as pd
out=pathlib.Path('team_trb_all_players/output'); out.mkdir(parents=True,exist_ok=True)
api='https://huggingface.co/api/datasets/Vladislav/nba_dataset/tree/main?recursive=true&expand=false'
r=requests.get(api,timeout=30); r.raise_for_status(); items=r.json()
files=[x.get('path') for x in items if x.get('type')=='file']
(out/'file_list.json').write_text(json.dumps(files,indent=2))
report={'files':files}
for path in files:
    if not path.endswith('.parquet'): continue
    url='https://huggingface.co/datasets/Vladislav/nba_dataset/resolve/main/'+path
    try:
        df=pd.read_parquet(url)
        report[path]={'rows':len(df),'columns':list(df.columns),'dtypes':{c:str(t) for c,t in df.dtypes.items()},'sample':df.head(3).where(pd.notna(df.head(3)),None).to_dict('records')}
        # targeted summaries
        for col in ['season','season_year','year','game_id','action_type','event_type','type','sub_type']:
            if col in df.columns:
                report[path][f'{col}_values']=[str(x) for x in df[col].dropna().astype(str).unique()[:30]]
    except Exception as e:
        report[path]={'error':repr(e)}
(out/'static_schema_report.json').write_text(json.dumps(report,indent=2,default=str))
print(json.dumps({k:({'rows':v.get('rows'),'columns':v.get('columns')} if isinstance(v,dict) else v) for k,v in report.items()},indent=2))
if not any(isinstance(v,dict) and v.get('rows',0)>0 for k,v in report.items() if k!='files'):
    raise SystemExit(2)
