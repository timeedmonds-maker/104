import json, io, time, requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw
from matplotlib.offsetbox import OffsetImage, AnnotationBbox

ROWS=json.loads(r'''[["2021-22",1610612737,"ATL",19.5503,25,null,null],["2021-22",1610612738,"BOS",23.4846,6,null,null],["2021-22",1610612739,"CLE",22.47,12,null,null],["2021-22",1610612740,"NOP",25.1367,4,null,null],["2021-22",1610612741,"CHI",20.1536,21,null,null],["2021-22",1610612742,"DAL",21.0553,17,null,null],["2021-22",1610612743,"DEN",18.2951,28,null,null],["2021-22",1610612744,"GSW",20.5849,19,null,null],["2021-22",1610612745,"HOU",19.988,24,null,null],["2021-22",1610612746,"LAC",17.9984,29,null,null],["2021-22",1610612747,"LAL",20.0465,23,null,null],["2021-22",1610612748,"MIA",23.845,5,null,null],["2021-22",1610612749,"MIL",22.9897,9,null,null],["2021-22",1610612750,"MIN",23.0969,8,null,null],["2021-22",1610612751,"BKN",22.7215,11,null,null],["2021-22",1610612752,"NYK",21.9173,13,null,null],["2021-22",1610612753,"ORL",16.4891,30,null,null],["2021-22",1610612754,"IND",23.3287,7,null,null],["2021-22",1610612755,"PHI",18.4599,27,null,null],["2021-22",1610612756,"PHX",21.1901,15,null,null],["2021-22",1610612757,"POR",22.8739,10,null,null],["2021-22",1610612758,"SAC",20.2929,20,null,null],["2021-22",1610612759,"SAS",20.6368,18,null,null],["2021-22",1610612760,"OKC",20.1212,22,null,null],["2021-22",1610612761,"TOR",27.213,2,null,null],["2021-22",1610612762,"UTA",25.1822,3,null,null],["2021-22",1610612763,"MEM",28.3167,1,32.6,7.9],["2021-22",1610612764,"WAS",18.5774,26,null,null],["2021-22",1610612765,"DET",21.5981,14,null,null],["2021-22",1610612766,"CHA",21.1532,16,null,null],["2022-23",1610612737,"ATL",23.5295,11,null,null],["2022-23",1610612738,"BOS",20.4432,22,null,null],["2022-23",1610612739,"CLE",22.2983,16,null,null],["2022-23",1610612740,"NOP",23.0286,14,null,null],["2022-23",1610612741,"CHI",19.3057,28,null,null],["2022-23",1610612742,"DAL",19.0245,29,null,null],["2022-23",1610612743,"DEN",19.6316,26,null,null],["2022-23",1610612744,"GSW",22.1372,18,null,null],["2022-23",1610612745,"HOU",30.397,1,null,null],["2022-23",1610612746,"LAC",20.2629,23,null,null],["2022-23",1610612747,"LAL",21.2571,20,null,null],["2022-23",1610612748,"MIA",21.279,19,null,null],["2022-23",1610612749,"MIL",23.246,12,null,null],["2022-23",1610612750,"MIN",20.4768,21,null,null],["2022-23",1610612751,"BKN",17.7924,30,null,null],["2022-23",1610612752,"NYK",25.299,5,null,null],["2022-23",1610612753,"ORL",23.6709,9,null,null],["2022-23",1610612754,"IND",23.752,8,null,null],["2022-23",1610612755,"PHI",19.3993,27,null,null],["2022-23",1610612756,"PHX",27.5895,2,null,null],["2022-23",1610612757,"POR",22.2064,17,null,null],["2022-23",1610612758,"SAC",22.8358,15,null,null],["2022-23",1610612759,"SAS",23.1256,13,null,null],["2022-23",1610612760,"OKC",23.9735,6,null,null],["2022-23",1610612761,"TOR",27.5809,3,null,null],["2022-23",1610612762,"UTA",26.2937,4,null,null],["2022-23",1610612763,"MEM",23.63,10,31.9,11.2],["2022-23",1610612764,"WAS",19.75,25,null,null],["2022-23",1610612765,"DET",23.8744,7,null,null],["2022-23",1610612766,"CHA",19.9366,24,null,null],["2023-24",1610612737,"ATL",25.1877,6,null,null],["2023-24",1610612738,"BOS",25.0121,7,null,null],["2023-24",1610612739,"CLE",22.6104,18,null,null],["2023-24",1610612740,"NOP",24.7786,9,null,null],["2023-24",1610612741,"CHI",23.4846,14,null,null],["2023-24",1610612742,"DAL",21.0045,23,null,null],["2023-24",1610612743,"DEN",20.59,27,null,null],["2023-24",1610612744,"GSW",28.2156,1,null,null],["2023-24",1610612745,"HOU",24.999,8,null,null],["2023-24",1610612746,"LAC",22.7033,16,null,null],["2023-24",1610612747,"LAL",18.0585,28,null,null],["2023-24",1610612748,"MIA",20.8857,24,null,null],["2023-24",1610612749,"MIL",21.1416,21,null,null],["2023-24",1610612750,"MIN",22.3243,19,null,null],["2023-24",1610612751,"BKN",24.6334,10,null,null],["2023-24",1610612752,"NYK",26.3115,4,null,null],["2023-24",1610612753,"ORL",26.1612,5,null,null],["2023-24",1610612754,"IND",22.6994,17,null,null],["2023-24",1610612755,"PHI",22.2839,20,null,null],["2023-24",1610612756,"PHX",24.1965,12,null,null],["2023-24",1610612757,"POR",27.5082,3,null,null],["2023-24",1610612758,"SAC",24.2012,11,null,null],["2023-24",1610612759,"SAS",21.0856,22,null,null],["2023-24",1610612760,"OKC",20.7359,25,null,null],["2023-24",1610612761,"TOR",23.6377,13,null,null],["2023-24",1610612762,"UTA",27.7496,2,null,null],["2023-24",1610612763,"MEM",22.7517,15,null,null],["2023-24",1610612764,"WAS",17.7965,30,null,null],["2023-24",1610612765,"DET",20.5948,26,null,null],["2023-24",1610612766,"CHA",17.9498,29,null,null],["2024-25",1610612737,"ATL",23.683,18,null,null],["2024-25",1610612738,"BOS",25.8907,11,null,null],["2024-25",1610612739,"CLE",28.6288,2,null,null],["2024-25",1610612740,"NOP",26.2824,10,null,null],["2024-25",1610612741,"CHI",21.3294,27,null,null],["2024-25",1610612742,"DAL",21.7957,26,null,null],["2024-25",1610612743,"DEN",23.1282,22,null,null],["2024-25",1610612744,"GSW",26.5131,9,null,null],["2024-25",1610612745,"HOU",32.8949,1,42.2,11.6],["2024-25",1610612746,"LAC",22.1739,25,null,null],["2024-25",1610612747,"LAL",23.4445,21,null,null],["2024-25",1610612748,"MIA",22.7029,23,null,null],["2024-25",1610612749,"MIL",18.5653,30,null,null],["2024-25",1610612750,"MIN",24.296,17,null,null],["2024-25",1610612751,"BKN",24.7639,14,null,null],["2024-25",1610612752,"NYK",24.9656,13,null,null],["2024-25",1610612753,"ORL",26.5574,8,null,null],["2024-25",1610612754,"IND",20.7171,29,null,null],["2024-25",1610612755,"PHI",22.2695,24,null,null],["2024-25",1610612756,"PHX",23.4612,20,null,null],["2024-25",1610612757,"POR",28.4567,3,null,null],["2024-25",1610612758,"SAC",24.6766,15,null,null],["2024-25",1610612759,"SAS",23.5153,19,null,null],["2024-25",1610612760,"OKC",26.6003,7,null,null],["2024-25",1610612761,"TOR",27.0451,5,null,null],["2024-25",1610612762,"UTA",25.8877,12,null,null],["2024-25",1610612763,"MEM",26.87,6,null,null],["2024-25",1610612764,"WAS",20.8561,28,null,null],["2024-25",1610612765,"DET",24.5438,16,null,null],["2024-25",1610612766,"CHA",27.5991,4,null,null],["2025-26",1610612737,"ATL",28.3224,9,null,null],["2025-26",1610612738,"BOS",31.5419,4,null,null],["2025-26",1610612739,"CLE",28.863,8,null,null],["2025-26",1610612740,"NOP",26.7065,17,null,null],["2025-26",1610612741,"CHI",24.7188,22,null,null],["2025-26",1610612742,"DAL",23.0385,27,null,null],["2025-26",1610612743,"DEN",22.0931,30,null,null],["2025-26",1610612744,"GSW",27.9624,11,null,null],["2025-26",1610612745,"HOU",35.3276,1,41.7,7.8],["2025-26",1610612746,"LAC",24.7147,23,null,null],["2025-26",1610612747,"LAL",27.6651,14,null,null],["2025-26",1610612748,"MIA",27.978,10,null,null],["2025-26",1610612749,"MIL",22.4995,29,null,null],["2025-26",1610612750,"MIN",26.4095,18,null,null],["2025-26",1610612751,"BKN",25.4747,21,null,null],["2025-26",1610612752,"NYK",29.3245,7,null,null],["2025-26",1610612753,"ORL",27.6988,13,null,null],["2025-26",1610612754,"IND",22.808,28,null,null],["2025-26",1610612755,"PHI",24.1149,25,null,null],["2025-26",1610612756,"PHX",31.475,5,null,null],["2025-26",1610612757,"POR",32.3928,3,null,null],["2025-26",1610612758,"SAC",25.6914,20,null,null],["2025-26",1610612759,"SAS",27.4563,15,null,null],["2025-26",1610612760,"OKC",23.5917,26,null,null],["2025-26",1610612761,"TOR",27.804,12,null,null],["2025-26",1610612762,"UTA",26.035,19,null,null],["2025-26",1610612763,"MEM",26.8409,16,null,null],["2025-26",1610612764,"WAS",24.2724,24,null,null],["2025-26",1610612765,"DET",29.5822,6,null,null],["2025-26",1610612766,"CHA",33.6768,2,null,null]]''')
df=pd.DataFrame(ROWS,columns=['season','team_id','team_abbr','pct','rank','on_pct','swing_pp'])
seasons=['2021-22','2022-23','2023-24','2024-25','2025-26']

def get(url):
  for _ in range(3):
    try:
      r=requests.get(url,timeout=30,headers={'User-Agent':'Mozilla/5.0'})
      if r.ok and len(r.content)>1000:return r.content
    except:pass
    time.sleep(1)
  raise RuntimeError(url)

def logo(tid,abbr):
  try:
    import cairosvg
    b=get(f'https://cdn.nba.com/logos/nba/{tid}/global/L/logo.svg')
    im=Image.open(io.BytesIO(cairosvg.svg2png(bytestring=b,output_width=500,output_height=500))).convert('RGBA')
  except:
    sp={'GSW':'gs','NOP':'no','NYK':'ny','SAS':'sa','UTA':'utah','WAS':'wsh'}
    im=Image.open(io.BytesIO(get(f'https://a.espncdn.com/i/teamlogos/nba/500/{sp.get(abbr,abbr.lower())}.png'))).convert('RGBA')
  bb=im.getchannel('A').getbbox()
  if bb:im=im.crop(bb)
  sc=82/max(im.size); im=im.resize((round(im.width*sc),round(im.height*sc)),Image.Resampling.LANCZOS)
  c=Image.new('RGBA',(110,110),(255,255,255,0)); c.alpha_composite(im,((110-im.width)//2,(110-im.height)//2)); return c

logos={r.team_abbr:logo(int(r.team_id),r.team_abbr) for _,r in df[['team_id','team_abbr']].drop_duplicates().iterrows()}
h=Image.open(io.BytesIO(get('https://cdn.nba.com/headshots/nba/latest/1040x760/203500.png'))).convert('RGBA')
bb=h.getchannel('A').getbbox(); h=h.crop(bb) if bb else h
w,hh=h.size; h=h.crop((0,0,w,int(hh*.72))); side=max(h.size)
sq=Image.new('RGBA',(side,side),(255,255,255,0)); sq.alpha_composite(h,((side-h.width)//2,(side-h.height)//2))
mask=Image.new('L',(side,side),0); ImageDraw.Draw(mask).ellipse((2,2,side-3,side-3),fill=255)
avatar=Image.new('RGBA',(side,side),(255,255,255,0)); avatar.paste(sq,(0,0),mask)

fig,ax=plt.subplots(figsize=(12,12),dpi=300); fig.patch.set_facecolor('#FAFAF8'); ax.set_facecolor('#FAFAF8'); xm={s:i for i,s in enumerate(seasons)}
for i in range(5):ax.axvline(i,color='#DEDEDA',lw=.9,zorder=0)
for y in [15,20,25,30,35,40,45]:ax.axhline(y,color='#E9E9E5',lw=.8,zorder=0)
for s in seasons:
  ts=df[df.season==s].sort_values('pct').reset_index(drop=True)
  for k,r in ts.iterrows():
    x=xm[s]+((k%5)-2)*.035
    ax.add_artist(AnnotationBbox(OffsetImage(np.asarray(logos[r.team_abbr]),zoom=.42),(x,r.pct),frameon=False,zorder=2))
for _,r in df[df.on_pct.notna()].iterrows():
  x=xm[r.season]; yt=float(r.pct); yo=float(r.on_pct)
  ax.plot([x,x],[yt,yo],color='#111',lw=2.3,zorder=4)
  ax.scatter(x,yt,s=820,facecolor='#FAFAF8',edgecolor='#111',linewidth=2.2,zorder=5)
  ax.add_artist(AnnotationBbox(OffsetImage(np.asarray(logos[r.team_abbr]),zoom=.44),(x,yt),frameon=False,zorder=6))
  ax.text(x+.13,yt,f'#{int(r["rank"])}',ha='left',va='center',fontsize=11,fontweight='bold',zorder=7)
  ax.scatter(x,yo,s=1450,facecolor='white',edgecolor='#111',linewidth=2.2,zorder=5)
  ax.add_artist(AnnotationBbox(OffsetImage(np.asarray(avatar),zoom=.060),(x,yo),frameon=False,zorder=6))
  ax.text(x,yo+1.35,f'{yo:.1f}% | {r.swing_pp:+.1f}',ha='center',va='bottom',fontsize=12.5,fontweight='bold',zorder=7)
ax.text(xm['2023-24'],46.3,'ADAMS DID NOT PLAY',ha='center',va='bottom',fontsize=9.8,fontweight='bold',color='#999995')
ax.set_xlim(-.55,4.55); ax.set_ylim(15,47.2); ax.set_xticks(range(5)); ax.set_xticklabels(['’22','’23','’24','’25','’26'],fontsize=14,fontweight='bold')
ax.set_yticks([15,20,25,30,35,40,45]); ax.set_yticklabels([f'{v}%' for v in [15,20,25,30,35,40,45]],fontsize=12,color='#444')
ax.set_ylabel('TEAM OREB% ON MISSED 3-POINT SHOTS',fontsize=12,fontweight='bold',color='#666',labelpad=16); ax.tick_params(length=0)
for sp in ax.spines.values():sp.set_visible(False)
fig.text(.5,.965,'STEVEN ADAMS’ OFFENSIVE REBOUNDING IMPACT',ha='center',va='top',fontsize=25,fontweight='black',color='#111')
fig.text(.5,.925,'Increase in team OREB Rate on missed 3s with Adams on court',ha='center',va='top',fontsize=14.5,fontweight='bold',color='#222')
fig.text(.94,.04,'@funakistats',ha='right',va='bottom',fontsize=10.5,fontweight='bold',color='#777')
plt.subplots_adjust(left=.105,right=.97,top=.855,bottom=.10)
plt.savefig('STEVEN_ADAMS_OREB_MISSED_3S_STACKED_LOGOS_CDN_FINAL.png',dpi=400,bbox_inches='tight',facecolor=fig.get_facecolor())
