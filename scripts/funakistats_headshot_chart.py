import os, math, requests
import pandas as pd, numpy as np
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from PIL import Image
from io import BytesIO

data=[(203500, 'Steven Adams', 4657, 38.7674, 9.2647), (1631217, 'Moussa Diabate', 3348, 37.1713, 10.6249), (1631106, 'Tari Eason', 5144, 36.7448, 5.3718), (1642270, 'Donovan Clingan', 3366, 36.156, 5.8112), (1641708, 'Amen Thompson', 6524, 35.4386, 5.0363), (1630549, "Day'Ron Sharpe", 3908, 34.3314, 10.3014), (1631200, 'Kris Murray', 3671, 33.8836, 2.6432), (1629011, 'Mitchell Robinson', 5645, 33.4095, 4.8526), (1629111, 'Jock Landale', 4316, 33.0735, 4.6788), (1641739, 'Toumani Camara', 6952, 32.9185, 2.1218), (1629006, 'Josh Okogie', 4726, 32.8726, 4.6279), (1631095, 'Jabari Smith Jr.', 9226, 32.614, -1.2754), (203083, 'Andre Drummond', 5555, 32.3662, 8.5019), (1630692, 'Jordan Goodwin', 4251, 32.3618, 6.9622), (1628449, 'Chris Boucher', 4855, 32.3567, 3.1954), (1629674, 'Neemias Queta', 3253, 32.1925, 5.7281), (1630703, 'Scoot Henderson', 4214, 32.128, 0.1011), (1631117, 'Walker Kessler', 5095, 31.9968, 3.4276), (1630625, 'Dalano Banton', 3147, 31.9602, 2.2103), (1641709, 'Ausar Thompson', 4760, 31.8226, 3.5886), (1630578, 'Alperen Sengun', 10447, 31.6895, 0.5865), (1628464, 'Goga Bitadze', 4487, 31.5981, 4.4501), (1629630, 'Ja Morant', 6242, 31.5678, 2.6066), (1642274, 'Yves Missi', 3253, 31.5164, 4.0475), (1629052, 'Oshae Brissett', 3391, 31.4628, 5.4042), (1630543, 'Isaiah Jackson', 3321, 31.4189, 5.8838), (1631222, 'Jake LaRavia', 4575, 31.32, 3.6306), (1642368, 'Jaylen Wells', 3864, 31.246, 2.0027), (1629672, 'Paul Reed', 3924, 31.2014, 4.6699), (1630173, 'Precious Achiuwa', 7316, 31.1951, 2.52)]
df=pd.DataFrame(data,columns=['player_id','player','minutes_on','team_oreb_on_pct','team_oreb_swing_pp'])
os.makedirs('headshots',exist_ok=True)
for _,r in df.iterrows():
    pid=int(r.player_id)
    url=f'https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png'
    resp=requests.get(url,timeout=30,headers={'User-Agent':'Mozilla/5.0'})
    resp.raise_for_status()
    im=Image.open(BytesIO(resp.content)).convert('RGBA')
    alpha=np.asarray(im.getchannel('A')); ys,xs=np.where(alpha>10)
    x0,x1,y0,y1=xs.min(),xs.max()+1,ys.min(),ys.max()+1
    fg_h=y1-y0; cut_y1=int(y0+fg_h*0.70); cx=(x0+x1)//2
    crop_h=max(cut_y1-y0,1); crop_w=int(crop_h*0.95)
    cx0=max(0,cx-crop_w//2); cx1=min(im.width,cx0+crop_w)
    im.crop((cx0,y0,cx1,cut_y1)).save(f'headshots/{pid}.png')

fig=plt.figure(figsize=(12,12),dpi=300,facecolor='white')
ax=fig.add_axes([0.115,0.15,0.82,0.69],facecolor='white')
x=df.team_oreb_swing_pp.values; y=df.team_oreb_on_pct.values
xmin=math.floor((x.min()-.6)); xmax=math.ceil((x.max()+.6))
ymin=math.floor((y.min()-.4)); ymax=math.ceil((y.max()+.4))
xmid=np.median(x); ymid=np.median(y)

ax.axvspan(xmin,xmid,ymin=(ymid-ymin)/(ymax-ymin),ymax=1,color='#f4dfbf',alpha=.55,zorder=0)
ax.axvspan(xmid,xmax,ymin=(ymid-ymin)/(ymax-ymin),ymax=1,color='#dfeede',alpha=.65,zorder=0)
ax.axvspan(xmin,xmid,ymin=0,ymax=(ymid-ymin)/(ymax-ymin),color='#f0d7d7',alpha=.55,zorder=0)
ax.axvspan(xmid,xmax,ymin=0,ymax=(ymid-ymin)/(ymax-ymin),color='#e4e5ec',alpha=.65,zorder=0)
ax.grid(True,color='#9ba0a6',linewidth=.6,alpha=.25,zorder=1)
ax.axvline(0,color='#6f747a',lw=1.0,alpha=.55,zorder=2)
ax.axhline(ymid,color='#6f747a',lw=.9,alpha=.35,zorder=2)
ax.scatter(x,y,s=8,color='#222',zorder=3)

for _,r in df.iterrows():
    arr=mpimg.imread(f'headshots/{int(r.player_id)}.png')
    oi=OffsetImage(arr,zoom=0.105)
    ax.add_artist(AnnotationBbox(oi,(r.team_oreb_swing_pp,r.team_oreb_on_pct),frameon=False,pad=0,box_alignment=(0.5,0.5),zorder=5))

lasts=df.player.str.split().str[-1]; counts=lasts.value_counts()
for _,r in df.iterrows():
    s=r.player.split()[-1]; label=r.player if counts[s]>1 else s
    ax.annotate(label,(r.team_oreb_swing_pp,r.team_oreb_on_pct),xytext=(0,-29),textcoords='offset points',ha='center',va='top',fontsize=5.8,fontweight='bold',zorder=6)

ax.set_xlim(xmin,xmax); ax.set_ylim(ymin,ymax)
xt=np.arange(math.ceil(xmin/2)*2,xmax+.001,2)
yt=np.arange(math.ceil(ymin),ymax+.001,1)
ax.set_xticks(xt); ax.set_yticks(yt)
ax.set_xticklabels([f'{v:+.0f}' if v>0 else f'{v:.0f}' for v in xt],fontsize=8)
ax.set_yticklabels([f'{v:.0f}%' for v in yt],fontsize=8)
ax.set_xlabel('TEAM OREB% ON–OFF SWING (PERCENTAGE POINTS)',fontsize=9.5,alpha=.55,labelpad=10,fontweight='bold')
ax.set_ylabel('TEAM OREB% WHILE PLAYER IS ON COURT',fontsize=9.5,alpha=.55,labelpad=10,fontweight='bold')
for sp in ax.spines.values(): sp.set_visible(False)

fig.text(.115,.925,'LEADERS IN TEAM OFFENSIVE REBOUNDING',fontsize=22,fontweight='black',ha='left')
fig.text(.115,.888,'Last 5 seasons • 2021–22 to 2025–26 • Minimum 3,000 on-court minutes • Top 30 by Team OREB% while on court',fontsize=8.7,ha='left',alpha=.68)
fig.text(.115,.073,'Career aggregate across the five-season window. Native OREB% ON and corrected-OFF weighted by corresponding minutes; swing calculated last.',fontsize=6.5,alpha=.58)
fig.text(.885,.073,'@funakistats',fontsize=8.5,fontweight='bold',ha='right',color='#d5483f',alpha=.55)
out='funakistats_team_oreb_headshots_last5_top30_EMBEDDED.png'
fig.savefig(out,dpi=300,bbox_inches='tight',pad_inches=.10,facecolor='white')
plt.close(fig)
df.to_csv('funakistats_team_oreb_headshots_last5_top30.csv',index=False)
print(out)
