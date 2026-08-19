import importlib.util
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.offsetbox import OffsetImage, AnnotationBbox

spec=importlib.util.spec_from_file_location('base','tmp/funakistats_m3/render.py')
base=importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

df=base.df
seasons=base.seasons
logos=base.logos
avatar=base.avatar

fig,ax=plt.subplots(figsize=(12,12),dpi=300)
fig.patch.set_facecolor('#FAFAF8'); ax.set_facecolor('#FAFAF8')
xm={s:i for i,s in enumerate(seasons)}
for i in range(5): ax.axvline(i,color='#DEDEDA',lw=.9,zorder=0)
for y in [15,20,25,30,35,40,45]: ax.axhline(y,color='#E9E9E5',lw=.8,zorder=0)

# League landscape: plot each team once. If the row is Adams' own team-season,
# skip it here because it will be rendered once, below, as the highlighted marker.
for s in seasons:
    ts=df[df.season==s].sort_values('pct').reset_index(drop=True)
    for k,r in ts.iterrows():
        if r.on_pct==r.on_pct:
            continue
        x=xm[s]+((k%5)-2)*.035
        ax.add_artist(AnnotationBbox(OffsetImage(np.asarray(logos[r.team_abbr]),zoom=.42),(x,r.pct),frameon=False,zorder=2))

# Adams team-season highlight: exactly one team logo, plus connector/headshot/value.
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
ax.set_xlim(-.55,4.55); ax.set_ylim(15,47.2)
ax.set_xticks(range(5)); ax.set_xticklabels(['’22','’23','’24','’25','’26'],fontsize=14,fontweight='bold')
ax.set_yticks([15,20,25,30,35,40,45]); ax.set_yticklabels([f'{v}%' for v in [15,20,25,30,35,40,45]],fontsize=12,color='#444')
ax.set_ylabel('TEAM OREB% ON MISSED 3-POINT SHOTS',fontsize=12,fontweight='bold',color='#666',labelpad=16)
ax.tick_params(length=0)
for sp in ax.spines.values(): sp.set_visible(False)
fig.text(.5,.965,'STEVEN ADAMS’ OFFENSIVE REBOUNDING IMPACT',ha='center',va='top',fontsize=25,fontweight='black',color='#111')
fig.text(.5,.925,'Increase in team OREB Rate on missed 3s with Adams on court',ha='center',va='top',fontsize=14.5,fontweight='bold',color='#222')
fig.text(.94,.04,'@funakistats',ha='right',va='bottom',fontsize=10.5,fontweight='bold',color='#777')
plt.subplots_adjust(left=.105,right=.97,top=.855,bottom=.10)
plt.savefig('STEVEN_ADAMS_OREB_MISSED_3S_STACKED_LOGOS_CDN_FIXED.png',dpi=400,bbox_inches='tight',facecolor=fig.get_facecolor())
