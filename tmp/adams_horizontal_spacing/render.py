import io,requests,math
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw
from matplotlib.offsetbox import OffsetImage,AnnotationBbox

DATA={
'2024-25':{'ATL':28.3878,'BOS':27.5293,'CLE':28.1178,'NOP':28.8667,'CHI':24.7669,'DAL':25.5959,'DEN':29.3685,'GSW':29.6737,'HOU':34.7126,'LAC':26.5146,'LAL':25.3647,'MIA':24.6935,'MIL':21.7693,'MIN':28.2246,'BKN':27.3640,'NYK':28.7000,'ORL':28.2477,'IND':23.4983,'PHI':26.0914,'PHX':24.7425,'POR':31.7228,'SAC':27.6472,'SAS':26.2481,'OKC':26.6634,'TOR':29.6807,'UTA':29.6304,'MEM':31.5351,'WAS':25.7026,'DET':28.8814,'CHA':29.4160},
'2025-26':{'ATL':28.1192,'BOS':32.5813,'CLE':29.2867,'NOP':29.5513,'CHI':26.7104,'DAL':26.8063,'DEN':27.1241,'GSW':29.3926,'HOU':37.2938,'LAC':27.4096,'LAL':27.4244,'MIA':28.6792,'MIL':24.8778,'MIN':28.9973,'BKN':27.5199,'NYK':31.8660,'ORL':29.1617,'IND':24.7021,'PHI':29.5106,'PHX':31.4160,'POR':34.2214,'SAC':28.6560,'SAS':29.3337,'OKC':25.3712,'TOR':29.2744,'UTA':28.7283,'MEM':28.9007,'WAS':27.2079,'DET':34.1057,'CHA':33.8925}}
ON={'2024-25':43.8,'2025-26':43.8}; SW={'2024-25':11.5,'2025-26':8.0}

def get(url):
 r=requests.get(url,timeout=30,headers={'User-Agent':'Mozilla/5.0'}); r.raise_for_status(); return r.content

def logo(abbr):
 mp={'GSW':'gs','NOP':'no','NYK':'ny','SAS':'sa','UTA':'utah','WAS':'wsh'}
 im=Image.open(io.BytesIO(get(f'https://a.espncdn.com/i/teamlogos/nba/500/{mp.get(abbr,abbr.lower())}.png'))).convert('RGBA')
 bb=im.getchannel('A').getbbox(); im=im.crop(bb) if bb else im
 sc=88/max(im.size); im=im.resize((round(im.width*sc),round(im.height*sc)),Image.Resampling.LANCZOS)
 c=Image.new('RGBA',(96,96),(255,255,255,0)); c.alpha_composite(im,((96-im.width)//2,(96-im.height)//2)); return c
LOGOS={a:logo(a) for a in DATA['2024-25']}

# Build one clean Adams marker from the official NBA CDN headshot.
# Exactly one black outer ring; no scatter marker underneath and no inherited crop ring.
raw=Image.open(io.BytesIO(get('https://cdn.nba.com/headshots/nba/latest/1040x760/203500.png'))).convert('RGBA')
bb=raw.getchannel('A').getbbox(); raw=raw.crop(bb) if bb else raw
w,hh=raw.size
# crop high enough to remove jersey while retaining full head/hair
raw=raw.crop((int(.17*w),0,int(.83*w),int(.64*hh)))
# fit portrait into canonical circular marker
N=360
R=174
inner=150
scale=min((inner*2)/raw.width,(inner*2)/raw.height)
raw=raw.resize((round(raw.width*scale),round(raw.height*scale)),Image.Resampling.LANCZOS)
avatar=Image.new('RGBA',(N,N),(255,255,255,0))
# white circular base
ad=ImageDraw.Draw(avatar)
ad.ellipse((N//2-R,N//2-R,N//2+R,N//2+R),fill=(255,255,255,255))
# circular clipping mask for portrait
pm=Image.new('L',(N,N),0)
ImageDraw.Draw(pm).ellipse((N//2-inner,N//2-inner,N//2+inner,N//2+inner),fill=255)
portrait_layer=Image.new('RGBA',(N,N),(255,255,255,0))
portrait_layer.alpha_composite(raw,((N-raw.width)//2,(N-raw.height)//2+7))
avatar.alpha_composite(Image.composite(portrait_layer,Image.new('RGBA',(N,N),(255,255,255,0)),pm))
# ONE outer ring only
ad=ImageDraw.Draw(avatar)
ad.ellipse((N//2-R,N//2-R,N//2+R,N//2+R),outline=(17,17,17,255),width=9)

fig,axs=plt.subplots(2,1,figsize=(18,11.5),dpi=300,sharex=True)
fig.patch.set_facecolor('#FAFAF8')
fig.suptitle("STEVEN ADAMS’ OFFENSIVE REBOUNDING IMPACT",fontsize=36,fontweight='black',y=.978,color='#111')
fig.text(.5,.918,'Increase in team OREB Rate with Adams on court',ha='center',fontsize=21,fontweight='bold',color='#222')

for ax,season in zip(axs,['2024-25','2025-26']):
 ax.set_facecolor('#FAFAF8'); ax.set_xlim(20,45.8); ax.set_ylim(-.70,.70); ax.set_yticks([])
 ax.grid(axis='x',color='#E1E1DD',lw=.9); ax.axhline(0,color='#ECECE8',lw=1)
 for sp in ax.spines.values(): sp.set_visible(False)
 vals=sorted([(v,a) for a,v in DATA[season].items() if a!='HOU'])
 lanes=[0,.24,-.24,.48,-.48]
 placed=[]
 for v,a in vals:
  y=0
  for yy in lanes:
   if all(not(abs(v-pv)<.82 and abs(yy-py)<.18) for pv,py in placed):
    y=yy; break
  placed.append((v,y))
  ax.add_artist(AnnotationBbox(OffsetImage(LOGOS[a],zoom=.56),(v,y),frameon=False,zorder=3))
 hv=DATA[season]['HOU']
 ax.scatter([hv],[0],s=1750,facecolor='#FAFAF8',edgecolor='#111',linewidth=2.1,zorder=5)
 ax.add_artist(AnnotationBbox(OffsetImage(LOGOS['HOU'],zoom=.62),(hv,0),frameon=False,zorder=6))
 ax.text(hv,-.50,f'#1  HOU  {hv:.1f}%',ha='center',va='center',fontsize=14,fontweight='bold')
 # connector terminates at centre but is covered by the opaque white marker base
 ax.plot([hv,ON[season]],[0,0],color='#111',lw=2.5,zorder=4)
 ax.add_artist(AnnotationBbox(OffsetImage(avatar,zoom=.40),(ON[season],0),frameon=False,zorder=7))
 ax.text(ON[season],.47,f"{ON[season]:.1f}% | +{SW[season]:.1f}",ha='center',va='center',fontsize=19,fontweight='bold')
 ax.text(19.85,0,season,ha='right',va='center',fontsize=21,fontweight='bold')

axs[-1].set_xticks([20,25,30,35,40,45]); axs[-1].set_xticklabels([f'{x}%' for x in [20,25,30,35,40,45]],fontsize=15,color='#444')
axs[-1].set_xlabel('TEAM OFFENSIVE REBOUND %',fontsize=17,fontweight='bold',color='#666',labelpad=18)
fig.text(.93,.03,'@funakistats',ha='right',fontsize=12,fontweight='bold',color='#777')
plt.subplots_adjust(left=.08,right=.965,top=.835,bottom=.11,hspace=.13)
plt.savefig('STEVEN_ADAMS_ROCKETS_OREB_HORIZONTAL_FRESH.png',dpi=420,bbox_inches='tight',facecolor=fig.get_facecolor())
