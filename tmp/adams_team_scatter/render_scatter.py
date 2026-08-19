import io, requests, math
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw
from matplotlib.offsetbox import OffsetImage, AnnotationBbox

DATA=[
('ATL',28.254352,1.228176),('BKN',27.440786,-8.223260),('BOS',30.034123,8.907505),('CHA',31.553198,-1.736519),('CHI',25.729514,-2.537747),('CLE',28.701813,7.264165),('DAL',26.214147,-2.861127),('DEN',28.256635,5.115387),('DET',31.467908,5.370151),('GSW',29.536670,1.878836),('HOU',35.943010,5.208252),('IND',24.112843,-2.157095),('LAC',26.952602,3.463023),('LAL',26.363193,1.770046),('MEM',30.226706,0.295997),('MIA',26.762898,1.633449),('MIL',23.318756,-1.615264),('MIN',28.610706,4.563222),('NOP',29.202583,-6.559497),('NYK',30.292035,5.932196),('OKC',26.034304,12.625429),('ORL',28.702032,0.816721),('PHI',27.806790,-3.004033),('PHX',28.194475,-0.502857),('POR',32.958845,-1.504573),('SAC',28.154855,-4.368069),('SAS',27.766129,3.013931),('TOR',29.484798,-0.378882),('UTA',29.180732,-8.474652),('WAS',26.439633,-11.552219)]
HOU=(35.943010,5.208252)
ADAMS=(43.8,10.703560)
DX=ADAMS[0]-HOU[0]
DY=ADAMS[1]-HOU[1]


def get(url):
    last=None
    for _ in range(5):
        try:
            r=requests.get(url,timeout=30,headers={'User-Agent':'Mozilla/5.0'})
            r.raise_for_status(); return r.content
        except Exception as e:
            last=e
    raise last

def logo(abbr):
    mp={'GSW':'gs','NOP':'no','NYK':'ny','SAS':'sa','UTA':'utah','WAS':'wsh'}
    im=Image.open(io.BytesIO(get(f'https://a.espncdn.com/i/teamlogos/nba/500/{mp.get(abbr,abbr.lower())}.png'))).convert('RGBA')
    bb=im.getchannel('A').getbbox(); im=im.crop(bb) if bb else im
    sc=100/max(im.size)
    im=im.resize((round(im.width*sc),round(im.height*sc)),Image.Resampling.LANCZOS)
    c=Image.new('RGBA',(112,112),(255,255,255,0)); c.alpha_composite(im,((112-im.width)//2,(112-im.height)//2)); return c

LOGOS={a:logo(a) for a,_,_ in DATA}

raw=Image.open(io.BytesIO(get('https://cdn.nba.com/headshots/nba/latest/1040x760/203500.png'))).convert('RGBA')
bb=raw.getchannel('A').getbbox(); raw=raw.crop(bb) if bb else raw
w,h=raw.size
raw=raw.crop((int(.17*w),0,int(.83*w),int(.64*h)))
N=280; R=134; inner=116
scale=min((inner*2)/raw.width,(inner*2)/raw.height)
raw=raw.resize((round(raw.width*scale),round(raw.height*scale)),Image.Resampling.LANCZOS)
avatar=Image.new('RGBA',(N,N),(255,255,255,0)); d=ImageDraw.Draw(avatar)
d.ellipse((N//2-R,N//2-R,N//2+R,N//2+R),fill=(255,255,255,255))
pm=Image.new('L',(N,N),0); ImageDraw.Draw(pm).ellipse((N//2-inner,N//2-inner,N//2+inner,N//2+inner),fill=255)
pl=Image.new('RGBA',(N,N),(255,255,255,0)); pl.alpha_composite(raw,((N-raw.width)//2,(N-raw.height)//2+4))
avatar.alpha_composite(Image.composite(pl,Image.new('RGBA',(N,N),(255,255,255,0)),pm))
d=ImageDraw.Draw(avatar); d.ellipse((N//2-R,N//2-R,N//2+R,N//2+R),outline=(15,15,15,255),width=8)

fig,ax=plt.subplots(figsize=(12.5,12.5),dpi=320)
fig.patch.set_facecolor('#FAFAF8'); ax.set_facecolor('#FAFAF8')
ax.set_xlim(21.5,46.2); ax.set_ylim(-13.5,14.5)
ax.grid(color='#E4E4E0',lw=.85,zorder=0)
ax.axhline(0,color='#B8B8B3',lw=1.2,zorder=0)
for sp in ax.spines.values(): sp.set_visible(False)
ax.tick_params(labelsize=13,colors='#555')
ax.set_xticks([22,26,30,34,38,42,46]); ax.set_xticklabels([f'{x}%' for x in [22,26,30,34,38,42,46]])
ax.set_yticks([-12,-8,-4,0,4,8,12]); ax.set_yticklabels([f'{y:+d}' if y!=0 else '0' for y in [-12,-8,-4,0,4,8,12]])

# all team logos at true positions
for a,x,y in DATA:
    z=.55 if a!='HOU' else .68
    ax.add_artist(AnnotationBbox(OffsetImage(LOGOS[a],zoom=z),(x,y),frameon=False,zorder=3 if a!='HOU' else 6))

# connector from Houston combined to Adams on-court point
ax.annotate('',xy=ADAMS,xytext=HOU,arrowprops=dict(arrowstyle='-|>',lw=3.2,color='#111',mutation_scale=18),zorder=4)
# clean Adams marker
ax.add_artist(AnnotationBbox(OffsetImage(avatar,zoom=.30),ADAMS,frameon=False,zorder=8))

# labels
ax.text(HOU[0]-0.2,HOU[1]-1.55,'HOUSTON\n35.9% OREB | +5.2 NetRtg',ha='right',va='top',fontsize=14,fontweight='bold',color='#111')
ax.text(ADAMS[0],ADAMS[1]+1.55,'ADAMS ON COURT\n43.8% OREB | +10.7 NetRtg',ha='center',va='bottom',fontsize=15,fontweight='bold',color='#111')
ax.text(39.7,8.9,f'+{DX:.1f} pp OREB\n+{DY:.1f} NetRtg',ha='center',va='center',fontsize=15,fontweight='bold',color='#111',bbox=dict(boxstyle='round,pad=.35',fc='#FAFAF8',ec='none',alpha=.96))

ax.set_xlabel('TEAM OFFENSIVE REBOUND %',fontsize=17,fontweight='bold',color='#666',labelpad=18)
ax.set_ylabel('NET RATING',fontsize=17,fontweight='bold',color='#666',labelpad=18)
fig.suptitle("STEVEN ADAMS’ TWO-SEASON TEAM IMPACT",fontsize=30,fontweight='black',y=.965,color='#111')
fig.text(.5,.922,'Team OREB% vs Net Rating | 2024-25 & 2025-26 combined',ha='center',fontsize=17,fontweight='bold',color='#333')
fig.text(.5,.892,'Team logos show each franchise’s combined two-season performance; arrow shows Houston → Adams on court',ha='center',fontsize=12.5,color='#777')
fig.text(.965,.035,'@funakistats',ha='right',fontsize=12,fontweight='bold',color='#777')
plt.subplots_adjust(left=.12,right=.965,top=.855,bottom=.11)
plt.savefig('STEVEN_ADAMS_TEAM_OREB_NETRTG_SCATTER_2SEASONS.png',dpi=420,bbox_inches='tight',facecolor=fig.get_facecolor())
