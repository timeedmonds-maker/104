from __future__ import annotations
import math
from typing import Any

TEAM_METRICS = [
"2pt FGM Assist%","Non Putback 2pt FGM Assist%","3pt FGM Assist%","3pt FG%","Non-Heave 3pt FG%","2pt FG%","eFG%","TS%","3PAr","% of FG3A Blocked","% of FG2A Blocked","Live Ball TO%","DReb% - Missed FTs","OReb% - Missed FTs","DReb% - Missed 2s","OReb% - Missed 2s","DReb% - Missed 3s","OReb% - Missed 3s","DReb% - Missed FGs","OReb% - Missed FGs","At Rim OReb%","Short Mid-Range OReb%","Long Mid-Range OReb%","Arc 3 OReb%","Corner 3 OReb%","At Rim DReb%","Short Mid-Range DReb%","Long Mid-Range DReb%","Arc 3 DReb%","Corner 3 DReb%","Blocks Recovered %","Seconds Per Possession - Offense","Seconds Per Possession - Defense","At Rim Shot Frequency","At Rim FG%","At Rim % Assisted","Short Mid Range Shot Frequency","Short Mid Range FG%","Short Mid Range % Assisted","Long Mid Range Shot Frequency","Long Mid Range FG%","Long Mid % Assisted","Corner 3 Shot Frequency","Corner 3 FG%","Corner 3 % Assisted","Arc 3 Shot Frequency","Arc 3 FG%","Arc 3 % Assisted","At Rim or 3pt Shot Frequency","Non-Heave Arc 3 FG%","Shot Quality","Shooting Foul Drawn Rate","3pt Shooting Foul Drawn Rate","2pt Shooting Foul Drawn Rate","Second Chance Points%","Penalty Points%","Penalty Possessions%","Avg 2pt Shot Distance","Avg 3pt Shot Distance","Penalty Efficiency Excluding Last Minute Take Foul Possessions","DReb%","OReb%","Pts per 100 Possessions","Assist Points per 100 Possessions","FTA per 100 Possessions","TOs per 100 Possessions","Assists per 100 Possessions","Pts per 100 Possessions - Defense","Shot Quality - Defense","3pt FG% - Defense","2pt FG% - Defense","eFG% - Defense","3PAr - Defense","At Rim Shot Frequency - Defense","At Rim FG% - Defense","Short Mid Range Shot Frequency - Defense","Short Mid Range FG% - Defense","Long Mid Range Shot Frequency - Defense","Long Mid Range FG% - Defense","Corner 3 Shot Frequency - Defense","Corner 3 FG% - Defense","Arc 3 Shot Frequency - Defense","Arc 3 FG% - Defense","Pace","Second Chance Efficiency","Penalty Efficiency","Second Chance Possessions Per 100 Possessions","First Chance Points Per 100 Possessions","Second Chance Points Per 100 Possessions"]

DIRECT = {
"2pt FGM Assist%":"Assisted2sPct","Non Putback 2pt FGM Assist%":"NonPutbacksAssisted2sPct","3pt FGM Assist%":"Assisted3sPct","3pt FG%":"Fg3Pct","Non-Heave 3pt FG%":"NonHeaveFg3Pct","2pt FG%":"Fg2Pct","eFG%":"EfgPct","TS%":"TsPct","3PAr":"FG3APct","% of FG3A Blocked":"FG3APctBlocked","% of FG2A Blocked":"FG2APctBlocked","Live Ball TO%":"LiveBallTurnoverPct","DReb% - Missed FTs":"DefFTReboundPct","OReb% - Missed FTs":"OffFTReboundPct","DReb% - Missed 2s":"DefTwoPtReboundPct","OReb% - Missed 2s":"OffTwoPtReboundPct","DReb% - Missed 3s":"DefThreePtReboundPct","OReb% - Missed 3s":"OffThreePtReboundPct","DReb% - Missed FGs":"DefFGReboundPct","OReb% - Missed FGs":"OffFGReboundPct","At Rim OReb%":"OffAtRimReboundPct","Short Mid-Range OReb%":"OffShortMidRangeReboundPct","Long Mid-Range OReb%":"OffLongMidRangeReboundPct","Arc 3 OReb%":"OffArc3ReboundPct","Corner 3 OReb%":"OffCorner3ReboundPct","At Rim DReb%":"DefAtRimReboundPct","Short Mid-Range DReb%":"DefShortMidRangeReboundPct","Long Mid-Range DReb%":"DefLongMidRangeReboundPct","Arc 3 DReb%":"DefArc3ReboundPct","Corner 3 DReb%":"DefCorner3ReboundPct","Blocks Recovered %":"BlocksRecoveredPct","Seconds Per Possession - Offense":"SecondsPerPossOff","Seconds Per Possession - Defense":"SecondsPerPossDef","At Rim Shot Frequency":"AtRimFrequency","At Rim FG%":"AtRimAccuracy","At Rim % Assisted":"AtRimPctAssisted","Short Mid Range Shot Frequency":"ShortMidRangeFrequency","Short Mid Range FG%":"ShortMidRangeAccuracy","Short Mid Range % Assisted":"ShortMidRangePctAssisted","Long Mid Range Shot Frequency":"LongMidRangeFrequency","Long Mid Range FG%":"LongMidRangeAccuracy","Long Mid % Assisted":"LongMidRangePctAssisted","Corner 3 Shot Frequency":"Corner3Frequency","Corner 3 FG%":"Corner3Accuracy","Corner 3 % Assisted":"Corner3PctAssisted","Arc 3 Shot Frequency":"Arc3Frequency","Arc 3 FG%":"Arc3Accuracy","Arc 3 % Assisted":"Arc3PctAssisted","At Rim or 3pt Shot Frequency":"AtRimFG3AFrequency","Non-Heave Arc 3 FG%":"NonHeaveArc3Accuracy","Shot Quality":"ShotQualityAvg","Shooting Foul Drawn Rate":"ShootingFoulsDrawnPct","3pt Shooting Foul Drawn Rate":"TwoPtShootingFoulsDrawnPct","2pt Shooting Foul Drawn Rate":"ThreePtShootingFoulsDrawnPct","Second Chance Points%":"SecondChancePointsPct","Penalty Points%":"PenaltyPointsPct","Penalty Possessions%":"PenaltyOffPossPct","Avg 2pt Shot Distance":"Avg2ptShotDistance","Avg 3pt Shot Distance":"Avg3ptShotDistance"}

DEFENSE_MIRROR = {"Pts per 100 Possessions - Defense":"points_per_100","Shot Quality - Defense":"ShotQualityAvg","3pt FG% - Defense":"Fg3Pct","2pt FG% - Defense":"Fg2Pct","eFG% - Defense":"EfgPct","3PAr - Defense":"FG3APct","At Rim Shot Frequency - Defense":"AtRimFrequency","At Rim FG% - Defense":"AtRimAccuracy","Short Mid Range Shot Frequency - Defense":"ShortMidRangeFrequency","Short Mid Range FG% - Defense":"ShortMidRangeAccuracy","Long Mid Range Shot Frequency - Defense":"LongMidRangeFrequency","Long Mid Range FG% - Defense":"LongMidRangeAccuracy","Corner 3 Shot Frequency - Defense":"Corner3Frequency","Corner 3 FG% - Defense":"Corner3Accuracy","Arc 3 Shot Frequency - Defense":"Arc3Frequency","Arc 3 FG% - Defense":"Arc3Accuracy"}

def _num(row: dict[str,Any], key: str) -> float | None:
    try: v=float(row.get(key))
    except (TypeError,ValueError): return None
    return v if math.isfinite(v) else None

def _div(a,b,scale=1.0):
    if a is None or b is None or b==0: return None
    return scale*a/b

def _points_per_100(r): return _div(_num(r,"Points"),_num(r,"OffPoss"),100.0)

def translate(team: dict[str,Any], opponent: dict[str,Any]) -> dict[str,float|None]:
    out={label:_num(team,key) for label,key in DIRECT.items()}
    tor=_num(team,"OffRebounds"); odr=_num(opponent,"DefRebounds")
    tdr=_num(team,"DefRebounds"); oor=_num(opponent,"OffRebounds")
    out["OReb%"]=_div(tor,None if tor is None or odr is None else tor+odr)
    out["DReb%"]=_div(tdr,None if tdr is None or oor is None else tdr+oor)
    out["Pts per 100 Possessions"]=_points_per_100(team)
    out["Assist Points per 100 Possessions"]=_div(_num(team,"AssistPoints"),_num(team,"OffPoss"),100.0)
    out["FTA per 100 Possessions"]=_div(_num(team,"FTA"),_num(team,"OffPoss"),100.0)
    out["TOs per 100 Possessions"]=_div(_num(team,"Turnovers"),_num(team,"OffPoss"),100.0)
    out["Assists per 100 Possessions"]=_div(_num(team,"Assists"),_num(team,"OffPoss"),100.0)
    for label,key in DEFENSE_MIRROR.items(): out[label]=_points_per_100(opponent) if key=="points_per_100" else _num(opponent,key)
    out["Pace"]=_num(team,"Pace")
    if out["Pace"] is None: out["Pace"]=_div(_num(team,"TotalPoss"),_num(team,"SecondsPlayed"),1440.0)
    out["Penalty Efficiency Excluding Last Minute Take Foul Possessions"]=_div(_num(team,"PenaltyPointsExcludingTakeFouls"),_num(team,"PenaltyOffPossExcludingTakeFouls"),100.0)
    out["Second Chance Efficiency"]=_div(_num(team,"SecondChancePoints"),_num(team,"SecondChanceOffPoss"),100.0)
    out["Penalty Efficiency"]=_div(_num(team,"PenaltyPoints"),_num(team,"PenaltyOffPoss"),100.0)
    out["Second Chance Possessions Per 100 Possessions"]=_div(_num(team,"SecondChanceOffPoss"),_num(team,"OffPoss"),100.0)
    out["First Chance Points Per 100 Possessions"]=_div(_num(team,"FirstChancePoints"),_num(team,"OffPoss"),100.0)
    out["Second Chance Points Per 100 Possessions"]=_div(_num(team,"SecondChancePoints"),_num(team,"OffPoss"),100.0)
    if set(out)!=set(TEAM_METRICS): raise RuntimeError(f"translator coverage mismatch missing={set(TEAM_METRICS)-set(out)} extra={set(out)-set(TEAM_METRICS)}")
    return {m:out[m] for m in TEAM_METRICS}

def source_dictionary() -> dict[str,str]:
    src={label:f"team raw key {key}" for label,key in DIRECT.items()}
    for label,key in DEFENSE_MIRROR.items(): src[label]="opponent raw Points/OffPoss * 100" if key=="points_per_100" else f"opponent raw key {key}"
    src.update({"DReb%":"team DefRebounds / (team DefRebounds + opponent OffRebounds)","OReb%":"team OffRebounds / (team OffRebounds + opponent DefRebounds)","Pts per 100 Possessions":"team Points / OffPoss * 100","Assist Points per 100 Possessions":"team AssistPoints / OffPoss * 100","FTA per 100 Possessions":"team FTA / OffPoss * 100","TOs per 100 Possessions":"team Turnovers / OffPoss * 100","Assists per 100 Possessions":"team Assists / OffPoss * 100","Pace":"team raw Pace; fallback TotalPoss / SecondsPlayed * 1440","Penalty Efficiency Excluding Last Minute Take Foul Possessions":"PenaltyPointsExcludingTakeFouls / PenaltyOffPossExcludingTakeFouls * 100","Second Chance Efficiency":"SecondChancePoints / SecondChanceOffPoss * 100","Penalty Efficiency":"PenaltyPoints / PenaltyOffPoss * 100","Second Chance Possessions Per 100 Possessions":"SecondChanceOffPoss / OffPoss * 100","First Chance Points Per 100 Possessions":"FirstChancePoints / OffPoss * 100","Second Chance Points Per 100 Possessions":"SecondChancePoints / OffPoss * 100"})
    return {m:src[m] for m in TEAM_METRICS}

def self_test():
    t={"OffRebounds":30,"DefRebounds":70,"Points":110,"OffPoss":100,"AssistPoints":60,"FTA":20,"Turnovers":12,"Assists":25,"TotalPoss":201,"SecondsPlayed":2880,"Pace":100.5,"SecondChancePoints":15,"SecondChanceOffPoss":12,"PenaltyPoints":22,"PenaltyOffPoss":20,"FirstChancePoints":95,"PenaltyPointsExcludingTakeFouls":20,"PenaltyOffPossExcludingTakeFouls":19}
    o={"OffRebounds":25,"DefRebounds":75,"Points":105,"OffPoss":101}
    for i,key in enumerate(set(DIRECT.values()),1): t.setdefault(key,i/1000)
    for i,key in enumerate({k for k in DEFENSE_MIRROR.values() if k!="points_per_100"},1): o.setdefault(key,i/1000)
    x=translate(t,o)
    assert len(x)==89 and list(x)==TEAM_METRICS
    assert abs(x["OReb%"]-30/105)<1e-12 and abs(x["DReb%"]-70/95)<1e-12
    assert abs(x["Pts per 100 Possessions"]-110)<1e-12
    assert abs(x["Pts per 100 Possessions - Defense"]-(105/101*100))<1e-12
    assert abs(x["Second Chance Efficiency"]-125)<1e-12
    assert len(source_dictionary())==89
    print("WOWY_METRIC_TRANSLATOR_SELF_TEST=PASS metrics=89")

if __name__=="__main__": self_test()
