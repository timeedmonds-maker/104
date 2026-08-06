from __future__ import annotations

import gzip, json, math, re, shutil
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
SRC = BASE / "impact_database" / "outputs"
OUT = BASE / "impact_database" / "analysis_export"
SEASON = re.compile(r"^\d{4}-\d{2}$")
TEAM = {1610612737:"ATL",1610612738:"BOS",1610612739:"CLE",1610612740:"NOP",1610612741:"CHI",1610612742:"DAL",1610612743:"DEN",1610612744:"GSW",1610612745:"HOU",1610612746:"LAC",1610612747:"LAL",1610612748:"MIA",1610612749:"MIL",1610612750:"MIN",1610612751:"BKN",1610612752:"NYK",1610612753:"ORL",1610612754:"IND",1610612755:"PHI",1610612756:"PHX",1610612757:"POR",1610612758:"SAC",1610612759:"SAS",1610612760:"OKC",1610612761:"TOR",1610612762:"UTA",1610612763:"MEM",1610612764:"WAS",1610612765:"DET",1610612766:"CHA"}


def read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, compression="gzip", low_memory=False) if path.exists() and path.stat().st_size else pd.DataFrame()


def ids(s: pd.Series) -> pd.Series:
    x = s.astype("string").fillna("").str.strip().str.replace(r"^([0-9]+)\.0$", r"\1", regex=True)
    return x.mask(x.isin(["nan","None","<NA>"]), "")


def first(frame: pd.DataFrame, names: list[str], default="") -> pd.Series:
    x = pd.Series(pd.NA, index=frame.index, dtype="string")
    for name in names:
        if name in frame:
            v = frame[name].astype("string").str.strip().mask(lambda z: z.isin(["","nan","None","<NA>"]))
            x = x.fillna(v)
    return x.fillna(default)


def abbr(season: str, value) -> str:
    try: team, year = int(float(value)), int(season[:4])
    except (TypeError, ValueError): return ""
    if team == 1610612763 and year == 2000: return "VAN"
    if team == 1610612740:
        if year <= 2001: return "CHH"
        if year in (2005, 2006): return "NOK"
        if year <= 2012: return "NOH"
        return "NOP"
    if team == 1610612751 and year <= 2011: return "NJN"
    if team == 1610612760 and year <= 2007: return "SEA"
    return TEAM.get(team, "")


def slug(v) -> str:
    x = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(v))
    return re.sub(r"[^A-Za-z0-9]+", "_", x).strip("_").lower() or "field"


def bounds(v):
    try: a = json.loads(v) if isinstance(v, str) else v
    except Exception: a = []
    a = [float(x) for x in a] if isinstance(a, list) else []
    return (min(a), max(a)) if a else (math.nan, math.nan)


def category(metric: str):
    m = metric.lower()
    if "rebound" in m: c = "rebounding"
    elif any(x in m for x in ("shot","fieldgoal","three","free","efg","true shooting")): c = "shooting"
    elif any(x in m for x in ("turnover","assist","possession","pace")): c = "possession/playmaking"
    elif any(x in m for x in ("point","rating","score")): c = "scoring/efficiency"
    elif any(x in m for x in ("foul","block","steal")): c = "defence/discipline"
    else: c = "other"
    if m.endswith("pct") or "percentage" in m or "frequency" in m: u,g="percentage/share","Prefer component-count recomputation; otherwise use a clearly labelled weighted mean."
    elif "rating" in m or "per100" in m: u,g="rate/rating","Prefer possession weighting; package career summaries use minutes weighting."
    elif any(x in m for x in ("rebounds","assists","turnovers","points","attempts","makes","blocks","steals","fouls")): u,g="count","Sum only across non-overlapping segments."
    else: u,g="metric-dependent","Check pbpstats definition before interpreting or aggregating."
    return c,u,g


def main():
    manifest = json.loads((SRC/"manifest.json").read_text())
    if manifest.get("core_complete") != 780 or manifest.get("expected_team_seasons") != 780: raise SystemExit("Core source is not 780/780")
    seasons = sorted(p.name for p in SRC.iterdir() if p.is_dir() and SEASON.match(p.name))
    if len(seasons) != 26: raise SystemExit(f"Expected 26 seasons, found {len(seasons)}")
    if OUT.exists(): shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    P,L,R,S = [],[],[],[]
    for season in seasons:
        p,l,r = read(SRC/season/"player_team_totals.csv.gz"), read(SRC/season/"team_on_off_metrics.csv.gz"), read(SRC/season/"team_rebound_derived.csv.gz")
        if len(p):
            p["season"]=season; p["team_id"]=pd.to_numeric(p["team_id"],errors="coerce")
            p["player_id"]=ids(first(p,["EntityId","RowId","PlayerId"])); p["player"]=first(p,["Name","ShortName"],"Unknown")
            p["minutes"]=pd.to_numeric(p.get("SecondsPlayed"),errors="coerce")/60; p["team_abbr"]=[abbr(season,x) for x in p.team_id]; P.append(p)
        if len(l):
            l["season"]=season; l["team_id"]=pd.to_numeric(l["team_id"],errors="coerce"); l["subject_player_id"]=ids(l["subject_player_id"])
            for c in ("minutes_on","minutes_off","on","off","on_off"): l[c]=pd.to_numeric(l[c],errors="coerce")
            l["metric"]=l.metric.astype("string").fillna("").str.strip(); l["subject_player"]=l.subject_player.astype("string").fillna("").str.strip(); l["team_abbr"]=[abbr(season,x) for x in l.team_id]; L.append(l)
        if len(r):
            r["season"]=season; r["team_id"]=pd.to_numeric(r["team_id"],errors="coerce"); r["player_id"]=ids(r["player_id"]); r["team_abbr"]=[abbr(season,x) for x in r.team_id]; R.append(r)
        S.append({"season":season,"player_team_rows":len(p),"team_on_off_rows":len(l),"rebound_rows":len(r),"core_team_seasons":manifest["seasons"][season]["core_team_seasons"]})
    players, long, rebound = pd.concat(P,ignore_index=True,sort=False), pd.concat(L,ignore_index=True,sort=False), pd.concat(R,ignore_index=True,sort=False)
    mapped = long[(long.subject_player_id!="") & (long.metric!="")].copy()
    duplicate_long = int(long.duplicated(["season","team_id","subject_player_id","metric"],keep=False).sum())
    identity = [x for x in ("season","team_id","team_abbr","player_id","player","minutes","SecondsPlayed","EntityId","RowId","PlayerId","Name","ShortName") if x in players]
    totals = players.rename(columns={x:f"total__{slug(x)}" for x in players if x not in identity})
    mins = mapped.groupby(["season","team_id","subject_player_id"],as_index=False).agg(minutes_on=("minutes_on","max"),minutes_off=("minutes_off","max")).rename(columns={"subject_player_id":"player_id"})
    wide = mapped.pivot_table(index=["season","team_id","subject_player_id"],columns="metric",values=["on","off","on_off"],aggfunc="first")
    wide.columns=[f"metric__{slug(m)}__{kind}" for kind,m in wide.columns]; wide=wide.reset_index().rename(columns={"subject_player_id":"player_id"})
    if len(rebound):
        for c in ("team_rebounds","opponent_rebounds_exact"): rebound[c]=pd.to_numeric(rebound.get(c),errors="coerce")
        b=rebound.get("opponent_rebound_candidates",pd.Series(index=rebound.index,dtype=object)).map(bounds); rebound["opponent_rebounds_min"]=b.map(lambda x:x[0]); rebound["opponent_rebounds_max"]=b.map(lambda x:x[1])
        t=rebound.team_rebounds; rebound["team_trb_pct_exact"]=t/(t+rebound.opponent_rebounds_exact); rebound["team_trb_pct_min"]=t/(t+rebound.opponent_rebounds_max); rebound["team_trb_pct_max"]=t/(t+rebound.opponent_rebounds_min)
        keep=[x for x in ("season","team_id","player_id","team_off_rebounds","team_def_rebounds","team_rebounds","opponent_rebounds_exact","opponent_rebounds_min","opponent_rebounds_max","team_trb_pct_exact","team_trb_pct_min","team_trb_pct_max","exact","off_rebound_pct_displayed","def_rebound_pct_displayed","error") if x in rebound]; reb_master=rebound[keep]
    else: reb_master=pd.DataFrame(columns=["season","team_id","player_id"])
    master=totals.merge(mins,on=["season","team_id","player_id"],how="left").merge(wide,on=["season","team_id","player_id"],how="left").merge(reb_master,on=["season","team_id","player_id"],how="left")
    master=master.sort_values(["season","team_id","minutes","player"],ascending=[1,1,0,1]); duplicate_master=int(master.duplicated(["season","team_id","player_id"],keep=False).sum())
    if duplicate_master: raise SystemExit(f"Duplicate master keys: {duplicate_master}")
    career_all, career_10k = read(SRC/"career_team_trb_all_players.csv.gz"), read(SRC/"career_team_trb_10000_minutes.csv.gz")
    for f in (career_all,career_10k):
        f["player_id"]=ids(f["player_id"])
        for c in ("rank_10000_minutes","minutes","seconds","team_rebounds","opponent_rebounds_exact","opponent_rebounds_min","opponent_rebounds_max","team_trb_pct_exact","team_trb_pct_min","team_trb_pct_max","ambiguous_segments","season_count","team_count"): f[c]=pd.to_numeric(f.get(c),errors="coerce")
    if len(career_all)!=manifest.get("career_players") or len(career_10k)!=manifest.get("qualifying_players"): raise SystemExit("Career output counts do not match source manifest")
    c=mapped.copy(); c["on_w"]=c.on*c.minutes_on; c["off_w"]=c.off*c.minutes_off
    career_metric=c.groupby(["subject_player_id","metric"],as_index=False).agg(player=("subject_player","first"),segment_count=("season","size"),season_count=("season","nunique"),team_count=("team_id","nunique"),total_minutes_on=("minutes_on","sum"),total_minutes_off=("minutes_off","sum"),on_weighted=("on_w","sum"),off_weighted=("off_w","sum"),on_min=("on","min"),on_max=("on","max"),off_min=("off","min"),off_max=("off","max")).rename(columns={"subject_player_id":"player_id"})
    career_metric["on_minutes_weighted_mean"]=career_metric.on_weighted/career_metric.total_minutes_on.replace(0,np.nan); career_metric["off_minutes_weighted_mean"]=career_metric.off_weighted/career_metric.total_minutes_off.replace(0,np.nan); career_metric["weighted_on_minus_off"]=career_metric.on_minutes_weighted_mean-career_metric.off_minutes_weighted_mean; career_metric=career_metric.drop(columns=["on_weighted","off_weighted"])
    dictionary=[]
    for metric,g in mapped.groupby("metric",sort=True):
        cat,unit,guide=category(str(metric)); dictionary.append({"metric":metric,"metric_slug":slug(metric),"category":cat,"inferred_unit":unit,"aggregation_guidance":guide,"rows":len(g),"players":g.subject_player_id.nunique(),"seasons":g.season.nunique(),"teams":g.team_id.nunique(),"on_non_null":g.on.notna().sum(),"off_non_null":g.off.notna().sum(),"on_min":g.on.min(),"on_max":g.on.max(),"source":"pbpstats team on/off endpoint"})
    metric_dictionary=pd.DataFrame(dictionary); season_summary=pd.DataFrame(S); season_summary["total_minutes"]=season_summary.season.map(master.groupby("season").minutes.sum()); season_summary["metric_count"]=season_summary.season.map(mapped.groupby("season").metric.nunique()); season_summary["unmapped_on_off_rows"]=season_summary.season.map((long.subject_player_id=="").groupby(long.season).sum()).fillna(0).astype(int)
    player_lookup=master.groupby("player_id",as_index=False).agg(player=("player","first"),first_season=("season","min"),last_season=("season","max"),season_count=("season","nunique"),team_count=("team_id","nunique"),minutes=("minutes","sum")).sort_values(["minutes","player"],ascending=[0,1])
    team_lookup=pd.DataFrame([{"team_id":k,"current_abbr":v,"note":"Season files use historical abbreviations where franchises relocated or rebranded."} for k,v in TEAM.items()])
    tables={"player_team_season_master":master,"team_on_off_long":long,"player_team_totals_raw":players,"rebound_segments":rebound,"career_metric_summary":career_metric,"career_team_rebounding_all":career_all,"career_team_rebounding_10000":career_10k,"metric_dictionary":metric_dictionary,"season_summary":season_summary,"player_lookup":player_lookup,"team_lookup":team_lookup}
    for name,f in tables.items(): f.to_parquet(OUT/f"{name}.parquet",index=False,compression="zstd")
    for name in ("player_team_season_master","career_metric_summary","career_team_rebounding_all","career_team_rebounding_10000","metric_dictionary","season_summary","player_lookup","team_lookup"): tables[name].to_csv(OUT/f"{name}.csv.gz",index=False,compression="gzip")
    long.to_csv(OUT/"team_on_off_long.csv.gz",index=False,compression="gzip")
    db=duckdb.connect(str(OUT/"TREB_core.duckdb"))
    for name in tables: db.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{(OUT/f'{name}.parquet').as_posix()}')")
    db.execute("CREATE INDEX idx_player_season ON player_team_season_master(player_id, season)"); db.execute("CREATE INDEX idx_metric_player ON team_on_off_long(subject_player_id, metric)"); db.execute("CREATE VIEW top_career_team_trb AS SELECT * FROM career_team_rebounding_10000 ORDER BY rank_10000_minutes"); db.close()
    quality={"core_complete":780,"expected_team_seasons":780,"core_database_complete":True,"teammate_pair_layer_included":False,"season_count":26,"player_team_season_rows":len(master),"unique_players":master.player_id.nunique(),"team_on_off_rows":len(long),"mapped_team_on_off_rows":len(mapped),"unmapped_team_on_off_rows":int((long.subject_player_id=="").sum()),"metric_count":mapped.metric.nunique(),"duplicate_long_keys":duplicate_long,"duplicate_master_keys":duplicate_master,"career_players":len(career_all),"qualifying_10000_minutes":len(career_10k)}
    (OUT/"quality_report.json").write_text(json.dumps(quality,indent=2,default=int)); (OUT/"schemas.json").write_text(json.dumps({n:{c:str(t) for c,t in f.dtypes.items()} for n,f in tables.items()},indent=2))
    (OUT/"query_examples.sql").write_text("-- Top team TRB% (10,000+ minutes)\nSELECT * FROM career_team_rebounding_10000 ORDER BY rank_10000_minutes LIMIT 20;\n\n-- Player metrics\nSELECT season, team_abbr, metric, minutes_on, on, off, on_off FROM team_on_off_long WHERE lower(subject_player) LIKE '%steven adams%' ORDER BY season, metric;\n")
    (OUT/"README.md").write_text("# TREB NBA Historical Player-Impact Core Database\n\nComplete 780/780 core team-season build, 2000-01 to 2025-26 regular seasons. The disabled teammate-pair test layer is excluded. Use TREB_core.duckdb or Parquet for complete analysis; compressed CSVs provide broad compatibility. The Excel workbook is a curated exploration layer because the long table exceeds Excel's row limit.\n")
    package={"package_name":"TREB NBA historical player-impact core database","generated_at_utc":pd.Timestamp.utcnow().isoformat(),"source_core_team_seasons":780,"seasons":seasons,"quality":quality}; (OUT/"manifest_core.json").write_text(json.dumps(package,indent=2,default=int)); print(json.dumps(package,indent=2,default=int))

if __name__ == "__main__": main()
