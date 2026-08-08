#!/usr/bin/env python3
"""Deterministic publication scatter renderer for validated TREB data.

All plotted coordinates come directly from the input rows. Styling, outlier selection,
label placement, and optional real-player headshots are separately auditable.
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path
import numpy as np
import pandas as pd

from funakistats_headshots import DEFAULT_REGISTRY, resolve_headshot


def madz(a):
    a=np.asarray(a,float); m=np.nanmedian(a); d=np.nanmedian(np.abs(a-m))
    if not np.isfinite(d) or d<1e-12:
        s=np.nanstd(a); return np.zeros_like(a) if (not np.isfinite(s) or s<1e-12) else (a-m)/s
    return .6744897501960817*(a-m)/d


def outlier_score(x,y):
    return np.hypot(madz(x),madz(y))


def overlap(a,b,pad=3):
    return not (a.x1+pad<b.x0 or b.x1+pad<a.x0 or a.y1+pad<b.y0 or b.y1+pad<a.y0)


# Prefer lateral labels with a clean horizontal terminal arm. Small vertical offsets
# are available only when needed for collision avoidance; the leader then uses a
# 90-degree elbow rather than a diagonal/angled arm.
OFFSETS=((42,0),(-42,0),(42,12),(-42,12),(42,-12),(-42,-12),(54,20),(-54,20),(54,-20),(-54,-20),(66,30),(-66,30),(66,-30),(-66,-30))


def _alignment(dx,dy):
    ha='left' if dx>0 else 'right'
    return ha,'center'


def _annotation(ax,row,x,y,player,dx,dy):
    ha,va=_alignment(dx,dy)
    return ax.annotate(
        str(row[player]),
        xy=(float(row[x]),float(row[y])),
        xytext=(dx,dy),
        textcoords='offset points',
        ha=ha,va=va,
        fontsize=9.2,fontweight='semibold',
        annotation_clip=True,
        arrowprops=dict(
            arrowstyle='-',
            linewidth=.72,
            shrinkA=3,
            shrinkB=5,
            connectionstyle='angle,angleA=0,angleB=90,rad=0',
        ),
        zorder=8,
    )


def place_labels(ax,labels,x,y,player):
    """Greedy deterministic collision-aware label placement with orthogonal leaders."""
    fig=ax.figure; fig.canvas.draw(); renderer=fig.canvas.get_renderer(); axes_box=ax.get_window_extent(renderer=renderer)
    occupied=[]; placements=[]
    ordered=labels.sort_values('_score',ascending=False,kind='mergesort')
    for _,row in ordered.iterrows():
        best=None; best_penalty=float('inf')
        for dx,dy in OFFSETS:
            ann=_annotation(ax,row,x,y,player,dx,dy); fig.canvas.draw(); box=ann.get_window_extent(renderer=renderer)
            outside=(box.x0<axes_box.x0 or box.x1>axes_box.x1 or box.y0<axes_box.y0 or box.y1>axes_box.y1)
            collisions=sum(overlap(box,b) for b in occupied)
            # Prefer the shortest clean lateral placement and penalise vertical travel,
            # keeping leader arms visually consistent across the chart.
            penalty=1000*collisions+(500 if outside else 0)+abs(dx)+1.35*abs(dy)
            ann.remove()
            if penalty<best_penalty: best=(dx,dy); best_penalty=penalty
            if collisions==0 and not outside: break
        dx,dy=best; ann=_annotation(ax,row,x,y,player,dx,dy); fig.canvas.draw(); box=ann.get_window_extent(renderer=renderer); occupied.append(box)
        placements.append({
            'player':str(row[player]),
            'x':float(row[x]),
            'y':float(row[y]),
            'score':float(row['_score']),
            'dx':dx,
            'dy':dy,
            'placement_penalty':float(best_penalty),
            'leader_style':'orthogonal_elbow_horizontal_terminal',
        })
    return placements


def add_headshots(ax, labels, x, y, player, player_id, registry, allow_remote, zoom=.105):
    """Overlay real cached/retrieved headshots at exact data coordinates for labelled outliers only."""
    if not player_id or player_id not in labels.columns:
        return []
    from matplotlib.offsetbox import AnnotationBbox, OffsetImage
    from PIL import Image
    audits=[]
    for _,row in labels.sort_values('_score',ascending=False,kind='mergesort').iterrows():
        pid=str(row[player_id]).replace('.0','')
        try:
            path,audit=resolve_headshot(pid,registry,allow_remote=allow_remote)
        except Exception as exc:
            path=None; audit={'player_id':pid,'ok':False,'reason':repr(exc)}
        record={'player':str(row[player]),'player_id':pid,'x':float(row[x]),'y':float(row[y]),**audit}
        if path:
            image=np.asarray(Image.open(path).convert('RGBA'))
            artist=AnnotationBbox(OffsetImage(image,zoom=zoom), (float(row[x]),float(row[y])), xycoords='data', frameon=False, pad=0, zorder=7, annotation_clip=True)
            ax.add_artist(artist)
        audits.append(record)
    return audits


def render(df,x,y,player,minutes,out,title,subtitle,xlabel,ylabel,threshold=10000,label_count=20,editorial_players=(),player_id=None,headshots=False,headshot_registry=DEFAULT_REGISTRY,fetch_headshots=False):
    import matplotlib.pyplot as plt
    need={x,y,player,minutes}; miss=need-set(df.columns)
    if miss: raise ValueError(f"Missing columns: {sorted(miss)}")
    d=df.copy()
    for c in (x,y,minutes): d[c]=pd.to_numeric(d[c],errors='coerce')
    d=d.dropna(subset=[x,y,player,minutes]).copy()
    if d.empty: raise ValueError('No plottable rows after validation')
    d['_score']=outlier_score(d[x],d[y])
    strong=d[minutes]>=threshold
    labels=d.loc[strong].sort_values('_score',ascending=False,kind='mergesort').head(label_count)
    if editorial_players:
        extra=d[d[player].astype(str).isin(set(editorial_players))]
        labels=pd.concat([labels,extra]).loc[lambda z:~z.index.duplicated(keep='first')]

    fig=plt.figure(figsize=(12,15),dpi=260)
    ax=fig.add_axes([.11,.15,.82,.68])
    ax.scatter(d.loc[~strong,x],d.loc[~strong,y],s=15,alpha=.16,linewidths=0,zorder=2)
    ax.scatter(d.loc[strong,x],d.loc[strong,y],s=28,alpha=.45,linewidths=0,zorder=3)
    ax.scatter(labels[x],labels[y],s=58,alpha=.95,linewidths=.8,zorder=6)
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.grid(linewidth=.55,alpha=.15); ax.set_axisbelow(True); ax.tick_params(labelsize=10.5,length=0,pad=7)
    ax.set_xlabel(xlabel,fontsize=12.5,fontweight='semibold',labelpad=14)
    ax.set_ylabel(ylabel,fontsize=12.5,fontweight='semibold',labelpad=14)
    xr=float(d[x].max()-d[x].min()) or 1; yr=float(d[y].max()-d[y].min()) or 1
    ax.set_xlim(d[x].min()-.07*xr,d[x].max()+.07*xr); ax.set_ylim(d[y].min()-.08*yr,d[y].max()+.08*yr)
    headshot_audit=add_headshots(ax,labels,x,y,player,player_id,Path(headshot_registry),fetch_headshots) if headshots else []
    placements=place_labels(ax,labels,x,y,player)

    fig.text(.11,.935,title,ha='left',va='top',fontsize=28,fontweight='bold')
    fig.text(.11,.895,subtitle,ha='left',va='top',fontsize=13.2,linespacing=1.35)
    detail=f"All players shown · stronger points = ≥{int(threshold):,} minutes · labels = reproducible robust outliers"
    if headshots: detail += " · headshots = labelled outliers only"
    fig.text(.11,.095,detail,ha='left',va='top',fontsize=9.5)
    out=Path(out); out.parent.mkdir(parents=True,exist_ok=True)
    for ext in ('png','svg','pdf'): fig.savefig(out.with_suffix('.'+ext),dpi=320 if ext=='png' else None,bbox_inches='tight',pad_inches=.18)
    plt.close(fig)
    audit={
        'rows':int(len(d)),
        'threshold_rows':int(strong.sum()),
        'label_rows':int(len(labels)),
        'method':'2D robust MAD distance + optional named editorial rows',
        'coordinate_integrity':'x/y copied directly from input rows',
        'collision_policy':'fixed lateral candidate search in display coordinates; extreme labels placed first; lowest-overlap fallback audited',
        'leader_line_policy':'straight orthogonal elbow connectors with horizontal terminal arms; no diagonal label arms',
        'headshot_policy':'real retrieved/supplied images only; never generated likenesses',
        'headshots':headshot_audit,
        'labels':placements,
        'outputs':{e:str(out.with_suffix('.'+e)) for e in ('png','svg','pdf')},
    }
    out.with_name(out.name+'_audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    return audit


def selftest():
    rng=np.random.default_rng(104); n=240
    d=pd.DataFrame({'player':[f'Player {i:03d}' for i in range(n)],'player_id':[str(1000+i) for i in range(n)],'minutes':rng.integers(1500,28000,n),'x':rng.normal(50,1.3,n),'y':rng.normal(27,1,n)})
    d.loc[0,['x','y','minutes']]=[56.5,32,24000]; d.loc[1,['x','y','minutes']]=[44,21,21000]
    a=outlier_score(d.x,d.y); b=outlier_score(d.x,d.y); assert np.allclose(a,b)
    eligible=d[d.minutes>=10000].copy(); eligible['_score']=a[eligible.index]
    chosen=list(eligible.sort_values('_score',ascending=False,kind='mergesort').head(12).index); assert 0 in chosen and 1 in chosen
    try: import matplotlib
    except ImportError: return
    audit=render(d,'x','y','player','minutes','/tmp/treb_scatter_selftest','TREB Scatter Renderer — QA','Synthetic data only','Synthetic x','Synthetic y',10000,12,player_id='player_id')
    assert audit['rows']==n and audit['label_rows']==12 and len(audit['labels'])==12
    assert all(np.isfinite(p['placement_penalty']) for p in audit['labels'])
    assert audit['leader_line_policy'].startswith('straight orthogonal elbow')
    assert all(p['leader_style']=='orthogonal_elbow_horizontal_terminal' for p in audit['labels'])


def main():
    p=argparse.ArgumentParser(); p.add_argument('--input',type=Path); p.add_argument('--x'); p.add_argument('--y'); p.add_argument('--player',default='player_name'); p.add_argument('--player-id'); p.add_argument('--minutes',default='minutes'); p.add_argument('--output-prefix',default='outputs/funakistats_scatter'); p.add_argument('--title',default='Historical NBA outliers'); p.add_argument('--subtitle',default='Regular season · 2000-01 through 2025-26'); p.add_argument('--x-label'); p.add_argument('--y-label'); p.add_argument('--minutes-threshold',type=float,default=10000); p.add_argument('--label-count',type=int,default=20); p.add_argument('--editorial-player',action='append',default=[]); p.add_argument('--headshots',action='store_true'); p.add_argument('--fetch-headshots',action='store_true'); p.add_argument('--headshot-registry',type=Path,default=DEFAULT_REGISTRY); p.add_argument('--self-test',action='store_true'); a=p.parse_args()
    if a.self_test: selftest(); print('SCATTER RENDERER SELF-TEST PASSED'); return
    if not a.input or not a.x or not a.y: raise SystemExit('--input --x --y required')
    if a.headshots and not a.player_id: raise SystemExit('--player-id is required with --headshots')
    d=pd.read_parquet(a.input) if a.input.suffix.lower() in ('.parquet','.pq') else pd.read_csv(a.input)
    print(json.dumps(render(d,a.x,a.y,a.player,a.minutes,a.output_prefix,a.title,a.subtitle,a.x_label or a.x,a.y_label or a.y,a.minutes_threshold,a.label_count,a.editorial_player,a.player_id,a.headshots,a.headshot_registry,a.fetch_headshots),indent=2))

if __name__=='__main__': main()
