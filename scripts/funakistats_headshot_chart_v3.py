import os, math, requests
import pandas as pd, numpy as np
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from PIL import Image
from io import BytesIO

data=[(1631217,'Moussa Diabate',3348.0,37.1713,4.5525),(1631106,'Tari Eason',5144.0,36.7448,3.0844),(1642270,'Donovan Clingan',3366.0,36.156,0.44),(1641708,'Amen Thompson',6524.0,35.4386,6.0974),(1630549,"Day'Ron Sharpe",3908.0,34.3314,-0.3251),(1631200,'Kris Murray',3671.0,33.8836,-2.5912),(203500,'Steven Adams',15487.0,33.1209,5.9218),(1629111,'Jock Landale',4316.0,33.0735,2.241),(1641739,'Toumani Camara',6952.0,32.9185,-1.5966),(1631095,'Jabari Smith Jr.',9226.0,32.614,-0.386),(1630692,'Jordan Goodwin',4251.0,32.3618,-0.8792),(1629674,'Neemias Queta',3253.0,32.1925,11.9922),(1630703,'Scoot Henderson',4214.0,32.128,-5.9568),(203460,'Andre Roberson',3563.0,32.1212,4.9843),(1631117,'Walker Kessler',5095.0,31.9968,-3.2024),(1630625,'Dalano Banton',3147.0,31.9602,-4.7183),(1641709,'Ausar Thompson',4760.0,31.8226,3.5704),(1630578,'Alperen Sengun',10447.0,31.6895,-0.4299),(1642274,'Yves Missi',3253.0,31.5164,-8.3898),(1629011,'Mitchell Robinson',9270.0,31.4677,0.4574),(1630543,'Isaiah Jackson',3321.0,31.4189,-1.6365),(1631222,'Jake LaRavia',4575.0,31.32,-0.2388),(1630194,'Paul Reed',4101.0,31.2791,3.373),(1642377,'Jaylen Wells',3864.0,31.246,0.5874),(202683,'Enes Kanter',8155.0,31.234,0.1495),(1631133,'Jabari Walker',3802.0,31.015,-5.2022),(1631105,'Jalen Duren',7403.0,30.8242,-0.2336),(1629723,'John Konchar',6289.0,30.7179,1.306),(1630540,'Miles McBride',5097.0,30.7024,6.6065),(1628449,'Chris Boucher',7291.0,30.6163,2.8663)]
df=pd.DataFrame(data,columns=['player_id','player','minutes_on','team_oreb_on_pct','net_rating_on'])

os.makedirs('headshots',exist_ok=True)
for _,r in df.iterrows():
    pid=int(r.player_id)
    resp=requests.get(f'https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png',timeout=30,headers={'User-Agent':'Mozilla/5.0'})
    resp.raise_for_status()
    im=Image.open(BytesIO(resp.content)).convert('RGBA')
    alpha=np.asarray(im.getchannel('A')); ys,xs=np.where(alpha>10)
    x0,x1,y0,y1=xs.min(),xs.max()+1,ys.min(),ys.max()+1
    fg_h=y1-y0; cut_y1=int(y0+fg_h*0.72); cx=(x0+x1)//2
    crop_h=max(cut_y1-y0,1); crop_w=int(crop_h*0.95)
    cx0=max(0,cx-crop_w//2); cx1=min(im.width,cx0+crop_w)
    im.crop((cx0,y0,cx1,cut_y1)).save(f'headshots/{pid}.png')

fig=plt.figure(figsize=(12,12),dpi=300,facecolor='white')
ax=fig.add_axes([0.115,0.15,0.82,0.69],facecolor='white')
x=df.team_oreb_on_pct.values; y=df.net_rating_on.values
xmin=math.floor((x.min()-0.5)); xmax=math.ceil((x.max()+0.5)); ymin=math.floor(y.min()-1.2); ymax=math.ceil(y.max()+1.2)
xmid=np.median(x)
ax.set_xlim(xmin,xmax); ax.set_ylim(ymin,ymax)
ax.axvspan(xmin,xmid,ymin=(0-ymin)/(ymax-ymin),ymax=1,color='#f4dfbf',alpha=.50,zorder=0)
ax.axvspan(xmid,xmax,ymin=(0-ymin)/(ymax-ymin),ymax=1,color='#dfeede',alpha=.62,zorder=0)
ax.axvspan(xmin,xmid,ymin=0,ymax=(0-ymin)/(ymax-ymin),color='#f0d7d7',alpha=.50,zorder=0)
ax.axvspan(xmid,xmax,ymin=0,ymax=(0-ymin)/(ymax-ymin),color='#e4e5ec',alpha=.60,zorder=0)
ax.grid(True,color='#9ba0a6',linewidth=.6,alpha=.25,zorder=1)
ax.axhline(0,color='#6f747a',lw=1.0,alpha=.55,zorder=2); ax.axvline(xmid,color='#6f747a',lw=.9,alpha=.35,zorder=2)
ax.scatter(x,y,s=10,color='#333',zorder=3)

# Original approved portrait size: exact data point at portrait centre, no aggressive packing.
for _,r in df.iterrows():
    arr=mpimg.imread(f'headshots/{int(r.player_id)}.png')
    oi=OffsetImage(arr,zoom=0.105)
    ax.add_artist(AnnotationBbox(oi,(r.team_oreb_on_pct,r.net_rating_on),frameon=False,pad=0,box_alignment=(0.5,0.5),zorder=5))

suffixes={'Jr.','Jr','II','III','IV'}
def short_name(name):
    parts=name.split(); return parts[-2] if parts[-1] in suffixes and len(parts)>=2 else parts[-1]
shorts=[short_name(n) for n in df.player]; counts=pd.Series(shorts).value_counts()
for _,r in df.iterrows():
    s=short_name(r.player); label=r.player if counts[s]>1 else s
    ax.annotate(label,(r.team_oreb_on_pct,r.net_rating_on),xytext=(0,-28),textcoords='offset points',ha='center',va='top',fontsize=5.8,fontweight='bold',zorder=6)

xt=np.arange(math.ceil(xmin),xmax+.001,1); yt=np.arange(math.ceil(ymin/2)*2,ymax+.001,2)
ax.set_xticks(xt); ax.set_yticks(yt)
ax.set_xticklabels([f'{v:.0f}%' for v in xt],fontsize=8)
ax.set_yticklabels([f'{v:+.0f}' if v>0 else f'{v:.0f}' for v in yt],fontsize=8)
ax.set_xlabel('TEAM OREB% WHILE PLAYER IS ON COURT',fontsize=9.5,alpha=.55,labelpad=10,fontweight='bold')
ax.set_ylabel('TEAM NET RATING WHILE PLAYER IS ON COURT',fontsize=9.5,alpha=.55,labelpad=10,fontweight='bold')
for sp in ax.spines.values(): sp.set_visible(False)
fig.text(.115,.925,'TEAM OFFENSIVE REBOUNDING & NET RATING',fontsize=21.5,fontweight='bold',ha='left')
fig.text(.115,.888,'Last 10 seasons • 2016–17 to 2025–26 • Minimum 3,000 on-court minutes • Top 30 by Team OREB%',fontsize=8.7,ha='left',alpha=.68)
fig.text(.115,.073,'Career aggregate across the 10-season window. Native OREB%, offensive rating and defensive rating weighted by on-court minutes; Net Rating = ORtg − DRtg.',fontsize=6.5,alpha=.58)
fig.text(.885,.073,'@funakistats',fontsize=8.5,fontweight='bold',ha='right',color='#d5483f',alpha=.55)
out='funakistats_oreb_vs_oncourt_net_last10_top30_HEADSHOTS.png'
fig.savefig(out,dpi=300,bbox_inches='tight',pad_inches=.10,facecolor='white')
plt.close(fig)
df.to_csv('funakistats_oreb_vs_oncourt_net_last10_top30.csv',index=False)
print(out)
