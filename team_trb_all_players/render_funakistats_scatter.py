#!/usr/bin/env python3
"""Deterministic publication scatter renderer for validated TREB data."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd


def madz(a):
    a=np.asarray(a,float); m=np.nanmedian(a); d=np.nanmedian(np.abs(a-m))
    if not np.isfinite(d) or d<1e-12:
        s=np.nanstd(a); return np.zeros_like(a) if s<1e-12 else (a-m)/s
    return .6744897501960817*(a-m)/d


def outlier_score(x,y):
    return np.hypot(madz(x),madz(y))


def render(df,x,y,player,minutes,out,title,subtitle,xlabel,ylabel,threshold=10000,label_count=20):
    import matplotlib.pyplot as plt
    need={x,y,player,minutes}; miss=need-set(df.columns)
    if miss: raise ValueError(f"Missing columns: {sorted(miss)}")
    d=df.copy()
    for c in (x,y,minutes): d[c]=pd.to_numeric(d[c],errors='coerce')
    d=d.dropna(subset=[x,y,player,minutes]).copy()
    d['_score']=outlier_score(d[x],d[y])
    strong=d[minutes]>=threshold
    labels=d.loc[strong].sort_values('_score',ascending=False,kind='mergesort').head(label_count)

    fig=plt.figure(figsize=(12,15),dpi=260)
    ax=fig.add_axes([.11,.15,.82,.68])
    ax.scatter(d.loc[~strong,x],d.loc[~strong,y],s=15,alpha=.16,linewidths=0,zorder=2)
    ax.scatter(d.loc[strong,x],d.loc[strong,y],s=28,alpha=.45,linewidths=0,zorder=3)
    ax.scatter(labels[x],labels[y],s=52,alpha=.95,linewidths=.8,zorder=6)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(linewidth=.55,alpha=.15); ax.set_axisbelow(True); ax.tick_params(labelsize=10.5,length=0,pad=7)
    ax.set_xlabel(xlabel,fontsize=12.5,fontweight='semibold',labelpad=14)
    ax.set_ylabel(ylabel,fontsize=12.5,fontweight='semibold',labelpad=14)
    xr=float(d[x].max()-d[x].min()) or 1; yr=float(d[y].max()-d[y].min()) or 1
    ax.set_xlim(d[x].min()-.07*xr,d[x].max()+.07*xr); ax.set_ylim(d[y].min()-.08*yr,d[y].max()+.08*yr)

    offsets=[(18,16),(-18,16),(18,-16),(-18,-16),(28,0),(-28,0),(0,24),(0,-24)]
    placements=[]
    for i,(_,r) in enumerate(labels.iterrows()):
        dx,dy=offsets[i%len(offsets)]; ha='left' if dx>0 else ('right' if dx<0 else 'center'); va='bottom' if dy>0 else ('top' if dy<0 else 'center')
        ax.annotate(str(r[player]),xy=(r[x],r[y]),xytext=(dx,dy),textcoords='offset points',ha=ha,va=va,fontsize=9.2,fontweight='semibold',arrowprops=dict(arrowstyle='-',linewidth=.7,shrinkA=3,shrinkB=5),zorder=7)
        placements.append({'player':str(r[player]),'x':float(r[x]),'y':float(r[y]),'score':float(r['_score']),'dx':dx,'dy':dy})

    fig.text(.11,.935,title,ha='left',va='top',fontsize=28,fontweight='bold')
    fig.text(.11,.895,subtitle,ha='left',va='top',fontsize=13.2)
    fig.text(.11,.095,f"All players shown · stronger points = ≥{int(threshold):,} minutes · labels = reproducible robust outliers",ha='left',va='top',fontsize=9.5)
    out=Path(out); out.parent.mkdir(parents=True,exist_ok=True)
    for ext in ('png','svg','pdf'): fig.savefig(out.with_suffix('.'+ext),dpi=320 if ext=='png' else None,bbox_inches='tight',pad_inches=.18)
    plt.close(fig)
    audit={'rows':len(d),'threshold_rows':int(strong.sum()),'method':'2D robust MAD distance','coordinate_integrity':'x/y copied directly from input rows','labels':placements}
    out.with_name(out.name+'_audit.json').write_text(json.dumps(audit,indent=2))
    return audit


def selftest():
    rng=np.random.default_rng(104); n=240
    d=pd.DataFrame({'player':[f'Player {i:03d}' for i in range(n)],'minutes':rng.integers(1500,28000,n),'x':rng.normal(50,1.3,n),'y':rng.normal(27,1,n)})
    d.loc[0,['x','y','minutes']]=[56.5,32,24000]; d.loc[1,['x','y','minutes']]=[44,21,21000]
    a=outlier_score(d.x,d.y); b=outlier_score(d.x,d.y); assert np.allclose(a,b)
    try: import matplotlib
    except ImportError: return
    audit=render(d,'x','y','player','minutes','/tmp/treb_scatter_selftest','TREB Scatter Renderer — QA','Synthetic data only','Synthetic x','Synthetic y',10000,12)
    assert audit['rows']==n and len(audit['labels'])==12


def main():
    p=argparse.ArgumentParser(); p.add_argument('--input',type=Path); p.add_argument('--x'); p.add_argument('--y'); p.add_argument('--player',default='player_name'); p.add_argument('--minutes',default='minutes'); p.add_argument('--output-prefix',default='outputs/funakistats_scatter'); p.add_argument('--title',default='Historical NBA outliers'); p.add_argument('--subtitle',default='Regular season · 2000-01 through 2025-26'); p.add_argument('--x-label'); p.add_argument('--y-label'); p.add_argument('--minutes-threshold',type=float,default=10000); p.add_argument('--label-count',type=int,default=20); p.add_argument('--self-test',action='store_true'); a=p.parse_args()
    if a.self_test: selftest(); print('SCATTER RENDERER SELF-TEST PASSED'); return
    if not a.input or not a.x or not a.y: raise SystemExit('--input --x --y required')
    d=pd.read_parquet(a.input) if a.input.suffix.lower() in ('.parquet','.pq') else pd.read_csv(a.input)
    print(json.dumps(render(d,a.x,a.y,a.player,a.minutes,a.output_prefix,a.title,a.subtitle,a.x_label or a.x,a.y_label or a.y,a.minutes_threshold,a.label_count),indent=2))

if __name__=='__main__': main()
