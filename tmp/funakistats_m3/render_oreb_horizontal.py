import io,time,requests,math
import pandas as pd, numpy as np
import matplotlib.pyplot as plt
from PIL import Image,ImageDraw
from matplotlib.offsetbox import OffsetImage,AnnotationBbox

ROWS=[['2024-25',1610612737,'ATL',28.3878,12,None,None],['2024-25',1610612738,'BOS',27.5293,17,None,None],['2024-25',1610612739,'CLE',28.1178,15,None,None],['2024-25',1610612740,'NOP',28.8667,10,None,None],['2024-25',1610612741,'CHI',24.7669,26,None,None],['2024-25',1610612742,'DAL',25.5959,24,None,None],['2024-25',1610612743,'DEN',29.3685,8,None,None],['2024-25',1610612744,'GSW',29.6737,5,None,None],['2024-25',1610612745,'HOU',34.7126,1,43.8,11.5],['2024-25',1610612746,'LAC',26.5146,20,None,None],['2024-25',1610612747,'LAL',25.3647,25,None,None],['2024-25',1610612748,'MIA',24.6935,28,None,None],['2024-25',1610612749,'MIL',21.7693,30,None,None],['2024-25',1610612750,'MIN',28.2246,14,None,None],['2024-25',1610612751,'BKN',27.3640,18,None,None],['2024-25',1610612752,'NYK',28.7000,11,None,None],['2024-25',1610612753,'ORL',28.2477,13,None,None],['2024-25',1610612754,'IND',23.4983,29,None,None],['2024-25',1610612755,'PHI',26.0914,22,None,None],['2024-25',1610612756,'PHX',24.7425,27,None,None],['2024-25',1610612757,'POR',31.7228,2,None,None],['2024-25',1610612758,'SAC',27.6472,16,None,None],['2024-25',1610612759,'SAS',26.2481,21,None,None],['2024-25',1610612760,'OKC',26.6634,19,None,None],['2024-25',1610612761,'TOR',29.6807,4,None,None],['2024-25',1610612762,'UTA',29.6304,6,None,None],['2024-25',1610612763,'MEM',31.5351,3,None,None],['2024-25',1610612764,'WAS',25.7026,23,None,None],['2024-25',1610612765,'DET',28.8814,9,None,None],['2024-25',1610612766,'CHA',29.4160,7,None,None],['2025-26',1610612737,'ATL',28.1192,20,None,None],['2025-26',1610612738,'BOS',32.5813,5,None,None],['2025-26',1610612739,'CLE',29.2867,12,None,None],['2025-26',1610612740,'NOP',29.5513,8,None,None],['2025-26',1610612741,'CHI',26.7104,27,None,None],['2025-26',1610612742,'DAL',26.8063,26,None,None],['2025-26',1610612743,'DEN',27.1241,25,None,None],['2025-26',1610612744,'GSW',29.3926,10,None,None],['2025-26',1610612745,'HOU',37.2938,1,43.8,8.0],['2025-26',1610612746,'LAC',27.4096,23,None,None],['2025-26',1610612747,'LAL',27.4244,22,None,None],['2025-26',1610612748,'MIA',28.6792,18,None,None],['2025-26',1610612749,'MIL',24.8778,29,None,None],['2025-26',1610612750,'MIN',28.9973,15,None,None],['2025-26',1610612751,'BKN',27.5199,21,None,None],['2025-26',1610612752,'NYK',31.8660,6,None,None],['2025-26',1610612753,'ORL',29.1617,14,None,None],['2025-26',1610612754,'IND',24.7021,30,None,None],['2025-26',1610612755,'PHI',29.5106,9,None,None],['2025-26',1610612756,'PHX',31.4160,7,None,None],['2025-26',1610612757,'POR',34.2214,2,None,None],['2025-26',1610612758,'SAC',28.6560,19,None,None],['2025-26',1610612759,'SAS',29.3337,11,None,None],['2025-26',1610612760,'OKC',25.3712,28,None,None],['2025-26',1610612761,'TOR',29.2744,13,None,None],['2025-26',1610612762,'UTA',28.7283,17,None,None],['2025-26',1610612763,'MEM',28.9007,16,None,None],['2025-26',1610612764,'WAS',27.2079,24,None,None],['2025-26',1610612765,'DET',34.1057,3,None,None],['2025-26',1610612766,'CHA',33.8925,4,None,None]]
df=pd.DataFrame(ROWS,columns=['season','team_id','team_abbr','pct','rank','on_pct','swing'])

def get(url):
  for _ in range(4):
    try:
      r=requests.get(url,timeout=30,headers={'User-Agent':'Mozilla/5.0'}); r.raise_for_status(); return r.content
    except Exception: time.sleep(1)
  raise RuntimeError(url)

def logo(tid,abbr):
  import cairosvg
  try:
    b=get(f'https://cdn.nba.com/logos/nba/{tid}/global/L/logo.svg')
    im=Image.open(io.BytesIO(cairosvg.svg2png(bytestring=b,output_width=600,output_height=600))).convert('RGBA')
  except Exception:
    sp={'GSW':'gs','NOP':'no','NYK':'ny','SAS':'sa','UTA':'utah','WAS':'wsh'}
    im=Image.open(io.BytesIO(get(f'https://a.espncdn.com/i/teamlogos/nba/500/{sp.get(abbr,abbr.lower())}.png'))).convert('RGBA')
  bb=im.getchannel('A').getbbox(); im=im.crop(bb) if bb else im
  sc=90/max(im.size); im=im.resize((round(im.width*sc),round(im.height*sc)),Image.Resampling.LANCZOS)
  c=Image.new('RGBA',(112,112),(255,255,255,0)); c.alpha_composite(im,((112-im.width)//2,(112-im.height)//2)); return c

logos={r.team_abbr:logo(int(r.team_id),r.team_abbr) for _,r in df[['team_id','team_abbr']].drop_duplicates().iterrows()}
# official Adams headshot, cropped high to avoid jersey
h=Image.open(io.BytesIO(get('https://cdn.nba.com/headshots/nba/latest/1040x760/203500.png'))).convert('RGBA')
bb=h.getchannel('A').getbbox(); h=h.crop(bb) if bb else h
w,hh=h.size; h=h.crop((0,0,w,int(hh*.70))); side=max(h.size)
sq=Image.new('RGBA',(side,side),(255,255,255,0)); sq.alpha_composite(h,((side-h.width)//2,(side-h.height)//2))
mask=Image.new('L',(side,side),0); ImageDraw.Draw(mask).ellipse((3,3,side-4,side-4),fill=255)
avatar=Image.new('RGBA',(side,side),(255,255,255,0)); avatar.paste(sq,(0,0),mask)

fig,ax=plt.subplots(figsize=(16,10),dpi=300)
fig.patch.set_facecolor('#FAFAF8'); ax.set_facecolor('#FAFAF8')
ypos={'2024-25':1.25,'2025-26':0.25}
# grid
for x in [20,25,30,35,40,45]: ax.axvline(x,color='#E2E2DE',lw=1,zorder=0)
ax.axhline(.75,color='#ECECE8',lw=1,zorder=0)

for s in ['2024-25','2025-26']:
  ts=df[df.season==s].sort_values('pct').reset_index(drop=True)
  y0=ypos[s]
  # vertically jitter within each row to reduce overlap while preserving horizontal metric position
  for k,r in ts.iterrows():
    if r.team_abbr=='HOU': continue
    jitter=((k%5)-2)*.048
    ax.add_artist(AnnotationBbox(OffsetImage(np.asarray(logos[r.team_abbr]),zoom=.46),(r.pct,y0+jitter),frameon=False,zorder=2))
  hou=ts[ts.team_abbr=='HOU'].iloc[0]
  # connector from full-team HOU rate to Adams ON rate
  ax.plot([hou.pct,hou.on_pct],[y0,y0],color='#111',lw=2.4,zorder=4)
  # highlighted Rockets full-team marker
  ax.scatter(hou.pct,y0,s=900,facecolor='#FAFAF8',edgecolor='#111',linewidth=2.4,zorder=5)
  ax.add_artist(AnnotationBbox(OffsetImage(np.asarray(logos['HOU']),zoom=.47),(hou.pct,y0),frameon=False,zorder=6))
  ax.text(hou.pct,y0-.205,f'#{int(hou["rank"])}  HOU {hou.pct:.1f}%',ha='center',va='top',fontsize=11,fontweight='bold',zorder=7)
  # Adams ON marker/headshot
  ax.scatter(hou.on_pct,y0,s=1500,facecolor='white',edgecolor='#111',linewidth=2.4,zorder=5)
  ax.add_artist(AnnotationBbox(OffsetImage(np.asarray(avatar),zoom=.061),(hou.on_pct,y0),frameon=False,zorder=6))
  ax.text(hou.on_pct,y0+.215,f'{hou.on_pct:.1f}% | {hou.swing:+.1f}',ha='center',va='bottom',fontsize=15,fontweight='bold',zorder=7)

ax.set_xlim(20,46.2); ax.set_ylim(-.25,1.75)
ax.set_xticks([20,25,30,35,40,45]); ax.set_xticklabels([f'{v}%' for v in [20,25,30,35,40,45]],fontsize=14,color='#444')
ax.set_yticks([1.25,.25]); ax.set_yticklabels(['2024-25','2025-26'],fontsize=17,fontweight='bold')
ax.set_xlabel('TEAM OFFENSIVE REBOUND %',fontsize=14,fontweight='bold',color='#666',labelpad=18)
ax.tick_params(length=0)
for sp in ax.spines.values(): sp.set_visible(False)
fig.text(.5,.955,'STEVEN ADAMS’ OFFENSIVE REBOUNDING IMPACT',ha='center',va='top',fontsize=29,fontweight='black',color='#111')
fig.text(.5,.905,'Increase in team OREB Rate with Adams on court',ha='center',va='top',fontsize=18,fontweight='bold',color='#222')
fig.text(.945,.04,'@funakistats',ha='right',va='bottom',fontsize=12,fontweight='bold',color='#777')
plt.subplots_adjust(left=.13,right=.97,top=.82,bottom=.14)
plt.savefig('STEVEN_ADAMS_ROCKETS_OREB_HORIZONTAL.png',dpi=400,bbox_inches='tight',facecolor=fig.get_facecolor())
