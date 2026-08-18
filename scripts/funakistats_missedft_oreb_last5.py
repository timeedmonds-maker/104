import os, math, requests
import pandas as pd, numpy as np
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from PIL import Image
from io import BytesIO

data=[(203500,'Steven Adams',4657,26.880481,11.882141),(1641739,'Toumani Camara',6952,24.002043,7.997244),(1642270,'Donovan Clingan',3366,22.985918,-2.987783),(1630703,'Scoot Henderson',4214,21.227717,0.655645),(1629006,'Josh Okogie',4726,20.353555,8.303298),(1631200,'Kris Murray',3671,20.082430,-1.250641),(1626172,'Kevon Looney',6288,20.047869,8.626648),(1631101,'Shaedon Sharpe',6500,19.857492,2.122087),(1641708,'Amen Thompson',6524,19.820846,4.643690),(1631217,'Moussa Diabate',3348,19.800627,5.662939),(1630583,'Santi Aldama',6517,19.780528,4.758304),(1629111,'Jock Landale',4316,19.668072,6.771434),(1626192,'Pat Connaughton',5706,19.063372,-0.599873),(201572,'Brook Lopez',9204,18.906671,2.527213),(1629630,'Ja Morant',6242,18.860974,3.255656),(1641706,'Brandon Miller',5217,18.823059,2.484161),(203507,'Giannis Antetokounmpo',10123,18.784639,4.946544),(1631106,'Tari Eason',5144,18.758554,2.779903),(1628978,'Donte DiVincenzo',9302,18.670920,3.564207),(203083,'Andre Drummond',5555,18.441692,9.357889),(1630191,'Isaiah Stewart',7371,18.347036,5.381431),(1631260,'AJ Green',4812,18.248525,0.965797),(1630526,'Jeremiah Robinson-Earl',3795,18.202398,7.529134),(1642377,'Jaylen Wells',3864,17.636879,3.172020),(1630578,'Alperen Sengun',10447,17.581918,1.737899),(1641709,'Ausar Thompson',4760,17.542416,-0.185887),(1630543,'Isaiah Jackson',3321,17.393014,6.599311),(1631093,'Jaden Ivey',6054,17.366435,2.816361),(1642264,'Stephon Castle',4170,17.297026,8.649040),(203114,'Khris Middleton',6680,17.208024,3.144733)]
df=pd.DataFrame(data,columns=['player_id','player','minutes_on','oreb_missed_ft_on_pct','oreb_missed_ft_swing_pp'])
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
x=df.oreb_missed_ft_swing_pp.values; y=df.oreb_missed_ft_on_pct.values
xmin=math.floor(x.min()-1); xmax=math.ceil(x.max()+1); ymin=math.floor(y.min()-1); ymax=math.ceil(y.max()+1)
ax.set_xlim(xmin,xmax); ax.set_ylim(ymin,ymax)
ax.grid(True,color='#9ba0a6',linewidth=.6,alpha=.22,zorder=1)
ax.axvline(0,color='#6f747a',lw=1,alpha=.5,zorder=2)
ax.scatter(x,y,s=8,color='#222',zorder=3)
for _,r in df.iterrows():
    arr=mpimg.imread(f'headshots/{int(r.player_id)}.png')
    oi=OffsetImage(arr,zoom=0.105)
    ax.add_artist(AnnotationBbox(oi,(r.oreb_missed_ft_swing_pp,r.oreb_missed_ft_on_pct),frameon=False,pad=0,box_alignment=(0.5,0.5),zorder=5))

suffixes={'Jr.','Jr','II','III','IV'}
def short_name(name):
    p=name.split(); return p[-2] if p[-1] in suffixes and len(p)>=2 else p[-1]
shorts=[short_name(n) for n in df.player]; counts=pd.Series(shorts).value_counts()
for _,r in df.iterrows():
    s=short_name(r.player); label=r.player if counts[s]>1 else s
    ax.annotate(label,(r.oreb_missed_ft_swing_pp,r.oreb_missed_ft_on_pct),xytext=(0,-28),textcoords='offset points',ha='center',va='top',fontsize=5.8,fontweight='bold',zorder=6)

xt=np.arange(math.ceil(xmin/2)*2,xmax+.001,2); yt=np.arange(math.ceil(ymin/2)*2,ymax+.001,2)
ax.set_xticks(xt); ax.set_yticks(yt)
ax.set_xticklabels([f'{v:+.0f}' if v>0 else f'{v:.0f}' for v in xt],fontsize=8)
ax.set_yticklabels([f'{v:.0f}%' for v in yt],fontsize=8)
ax.set_xlabel('OREB% ON–OFF SWING | MISSED FREE THROWS (PERCENTAGE POINTS)',fontsize=9.5,alpha=.55,labelpad=10,fontweight='bold')
ax.set_ylabel('TEAM OREB% ON MISSED FREE THROWS | ON COURT',fontsize=9.5,alpha=.55,labelpad=10,fontweight='bold')
for sp in ax.spines.values(): sp.set_visible(False)
fig.text(.115,.925,'TEAM OFFENSIVE REBOUNDING ON MISSED FREE THROWS',fontsize=20.5,fontweight='bold',ha='left')
fig.text(.115,.888,'Last 5 seasons • 2021–22 to 2025–26 • Minimum 3,000 on-court minutes • Top 30 by on-court OREB%',fontsize=8.7,ha='left',alpha=.68)
fig.text(.115,.073,'Career aggregate across the five-season window. Native OREB% on missed FTs; ON and corrected-OFF weighted by corresponding minutes; swing calculated last.',fontsize=6.5,alpha=.58)
fig.text(.885,.073,'@funakistats',fontsize=8.5,fontweight='bold',ha='right',color='#d5483f',alpha=.55)
out='funakistats_oreb_missedft_last5_top30_HEADSHOTS.png'
fig.savefig(out,dpi=300,bbox_inches='tight',pad_inches=.10,facecolor='white')
plt.close(fig)
df.to_csv('funakistats_oreb_missedft_last5_top30.csv',index=False)
print(out)
