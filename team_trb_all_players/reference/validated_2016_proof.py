import pandas as pd, numpy as np, re
from collections import defaultdict

PATH='/mnt/data/treb_work/sample/nbastats_2016.csv'
df=pd.read_csv(PATH, low_memory=False)
# normalize ints
for c in ['GAME_ID','EVENTNUM','EVENTMSGTYPE','EVENTMSGACTIONTYPE','PERIOD','PLAYER1_ID','PLAYER2_ID','PLAYER3_ID','PLAYER1_TEAM_ID','PLAYER2_TEAM_ID','PLAYER3_TEAM_ID','PERSON1TYPE','PERSON2TYPE','PERSON3TYPE']:
    if c in df: df[c]=pd.to_numeric(df[c], errors='coerce').fillna(0).astype('int64')

def clock_seconds(s):
    try:
        m,sec=str(s).split(':'); return int(m)*60+float(sec)
    except: return 0.0

def infer_starters(period_df):
    # returns team->set5 using team-linked player IDs and sub chronology
    team_players=defaultdict(set)
    for slot in (1,2,3):
        pc=f'PLAYER{slot}_ID'; tc=f'PLAYER{slot}_TEAM_ID'; typ=f'PERSON{slot}TYPE'
        for pid,tid,ptype in zip(period_df[pc],period_df[tc],period_df[typ]):
            if pid>0 and tid>0 and ptype in (4,5): team_players[int(tid)].add(int(pid))
    # include substitution players even if team_id fields absent via matching other rows later
    # map player->team from all rows
    p2t={}
    for slot in (1,2,3):
        for pid,tid in zip(period_df[f'PLAYER{slot}_ID'], period_df[f'PLAYER{slot}_TEAM_ID']):
            if pid>0 and tid>0 and pid < 1610612737: p2t[int(pid)]=int(tid)
    out={}
    subs=period_df[period_df.EVENTMSGTYPE==8]
    for tid,players in team_players.items():
        # classify players who first enter via sub-in before any sub-out as bench
        starters=[]
        for pid in players:
            evs=[]
            for idx,r in subs.iterrows():
                if int(r.PLAYER1_ID)==pid: evs.append((idx,'out'))
                if int(r.PLAYER2_ID)==pid: evs.append((idx,'in'))
            evs.sort()
            if not evs or evs[0][1]=='out': starters.append(pid)
        # if not 5 use first-appearance heuristic excluding first sub-in
        if len(starters)!=5:
            # every player whose first event occurrence is not substitution-in
            starters=[]
            for pid in players:
                first=None; role=None
                for idx,r in period_df.iterrows():
                    found=False
                    if int(r.PLAYER1_ID)==pid: found=True; rr='in' if int(r.EVENTMSGTYPE)==8 and int(r.PLAYER2_ID)==pid else ('out' if int(r.EVENTMSGTYPE)==8 else 'play')
                    if int(r.PLAYER2_ID)==pid: found=True; rr='in' if int(r.EVENTMSGTYPE)==8 else 'play'
                    if int(r.PLAYER3_ID)==pid: found=True; rr='play'
                    if found: first=idx; role=rr; break
                if role!='in': starters.append(pid)
        if len(starters)!=5:
            return None
        out[tid]=set(starters)
    if len(out)!=2 or any(len(s)!=5 for s in out.values()): return None
    return out

def is_missed_ft(r):
    if int(r.EVENTMSGTYPE)!=3: return False
    desc=' '.join(str(x) for x in [r.HOMEDESCRIPTION,r.VISITORDESCRIPTION,r.NEUTRALDESCRIPTION] if pd.notna(x)).lower()
    return 'miss' in desc

def is_missed_fg(r): return int(r.EVENTMSGTYPE)==2

def ft_is_end(r):
    desc=' '.join(str(x) for x in [r.HOMEDESCRIPTION,r.VISITORDESCRIPTION,r.NEUTRALDESCRIPTION] if pd.notna(x)).lower()
    # NBA desc examples Free Throw 1 of 2, 2 of 2, technical 1 of 1
    m=re.search(r'(\d+) of (\d+)', desc)
    if m: return m.group(1)==m.group(2)
    # single FTs and technical usually end by sequence semantics, but technical can be non-live
    return True

def real_rebound_flags(g):
    real=np.zeros(len(g),dtype=bool); teams=np.zeros(len(g),dtype=np.int64)
    rows=list(g.itertuples(index=False))
    for i,r in enumerate(rows):
        if int(r.EVENTMSGTYPE)!=4: continue
        p1=int(r.PLAYER1_ID); tid=int(r.PLAYER1_TEAM_ID)
        # team rebounds may encode team id in player1_id with no team id
        if tid<=0 and p1>=1610612737: tid=p1; p1=0
        # base placeholder
        if int(r.EVENTMSGACTIONTYPE)!=0 and p1==0: continue
        # find next significant event for buzzer cases
        j=i+1
        while j<len(rows) and int(rows[j].EVENTMSGTYPE)==18: j+=1
        clock=clock_seconds(r.PCTIMESTRING)
        if p1==0 and clock==0 and (j>=len(rows) or int(rows[j].EVENTMSGTYPE)==13): continue
        # same-time turnover placeholder (shot clock 24/8 sec, kicked ball descriptions)
        if p1==0:
            desc_time=[]
            for q in rows:
                if int(q.PERIOD)==int(r.PERIOD) and str(q.PCTIMESTRING)==str(r.PCTIMESTRING) and int(q.EVENTMSGTYPE)==5:
                    d=' '.join(str(x) for x in [q.HOMEDESCRIPTION,q.VISITORDESCRIPTION,q.NEUTRALDESCRIPTION] if pd.notna(x)).lower()
                    if 'shot clock' in d or 'kicked ball' in d:
                        desc_time.append(d)
            if desc_time: continue
        # locate missed shot according to immediate previous / jumpball skip subs/timeouts
        k=i-1
        prev=rows[k] if k>=0 else None
        shot=None
        if prev is not None and (is_missed_fg(prev) or is_missed_ft(prev)):
            shot=prev
        elif prev is not None and int(prev.EVENTMSGTYPE)==10: # jump ball, walk back subs/timeouts
            k-=1
            while k>=0 and int(rows[k].EVENTMSGTYPE) in (8,9): k-=1
            if k>=0 and (is_missed_fg(rows[k]) or is_missed_ft(rows[k])): shot=rows[k]
        # if no shot, likely placeholder or strange event; don't count
        if shot is None: continue
        if is_missed_ft(shot) and not ft_is_end(shot): continue
        # buzzer-beater team rebound at shot time
        if p1==0 and clock_seconds(shot.PCTIMESTRING)<=3 and abs(clock-clock_seconds(shot.PCTIMESTRING))<1e-9 and (j>=len(rows) or int(rows[j].EVENTMSGTYPE)==13): continue
        if tid<=0:
            continue
        real[i]=True; teams[i]=tid
    return real,teams

adams=203500; okc=1610612760
team_count=opp_count=0
bad_periods=[]; games_adams=0
for gid,g in df.groupby('GAME_ID',sort=False):
    g=g.sort_values('EVENTNUM').reset_index(drop=True)
    lineups=[None]*len(g)
    ok=True
    for period,p in g.groupby('PERIOD',sort=False):
        starters=infer_starters(p)
        if starters is None:
            bad_periods.append((gid,period)); ok=False; continue
        current={tid:set(ps) for tid,ps in starters.items()}
        for idx in p.index:
            r=g.loc[idx]
            # source _fill_columns applies substitution at event index onward, so update before snapshot at sub event
            if int(r.EVENTMSGTYPE)==8:
                off=int(r.PLAYER1_ID); on=int(r.PLAYER2_ID)
                # identify team from explicit ids or roster membership
                tid=int(r.PLAYER1_TEAM_ID) or int(r.PLAYER2_TEAM_ID)
                if tid<=0:
                    for t,s in current.items():
                        if off in s: tid=t; break
                if tid in current:
                    current[tid].discard(off); current[tid].add(on)
            lineups[idx]={t:set(s) for t,s in current.items()}
    real,teams=real_rebound_flags(g)
    for i,(isreal,rtid) in enumerate(zip(real,teams)):
        if not isreal or lineups[i] is None: continue
        # find player's team lineup
        on=False
        for tid,s in lineups[i].items():
            if adams in s:
                on=True
                if tid!=okc:
                    print('weird team',gid,tid)
                break
        if on:
            if int(rtid)==okc: team_count+=1
            else: opp_count+=1
    if any(lineups[i] and adams in lineups[i].get(okc,set()) for i in range(len(g))): games_adams+=1
print('counts',team_count,opp_count,'games',games_adams,'bad periods',len(bad_periods),bad_periods[:20])
