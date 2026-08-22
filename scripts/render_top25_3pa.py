import io, requests, pandas as pd
from PIL import Image, ImageDraw, ImageFont

players=[
(1,1627750,'Jamal Murray',4.421957),(2,202331,'Paul George',3.560839),(3,203999,'Nikola Jokić',3.327061),(4,1628379,'Luke Kennard',3.185049),(5,201939,'Stephen Curry',3.156923),(6,202695,'Kawhi Leonard',2.975750),(7,202681,'Kyrie Irving',2.959188),(8,203954,'Joel Embiid',2.798739),(9,201142,'Kevin Durant',2.716807),(10,1629008,'Michael Porter Jr.',2.661839),(11,1630174,'Aaron Nesmith',2.647158),(12,1631260,'AJ Green',2.608743),(13,1630163,'LaMelo Ball',2.576754),(14,203994,'Jusuf Nurkić',2.536215),(15,1630178,'Tyrese Maxey',2.519183),(16,1630169,'Tyrese Haliburton',2.373862),(17,1629639,'Tyler Herro',2.353698),(18,203468,'CJ McCollum',2.344965),(19,1641706,'Brandon Miller',2.226628),(20,1630703,'Scoot Henderson',2.210929),(21,1631094,'Paolo Banchero',2.210135),(22,1627777,'Georges Niang',2.180776),(23,203500,'Steven Adams',2.165567),(24,1628384,'OG Anunoby',2.161761),(25,203484,'Kentavious Caldwell-Pope',2.129610)
]

ids={p[1] for p in players}; threes={pid:0 for pid in ids}
for yr in [2022,2023,2024,2025,2026]:
    df=pd.read_parquet(f'https://raw.githubusercontent.com/llimllib/nba_data/main/data/players_{yr}.parquet')
    sub=df[df.player_id.isin(ids)]
    for pid,g in sub.groupby('player_id'):
        if 'TOT' in set(g.team_abbreviation.astype(str)):
            v=g.loc[g.team_abbreviation.astype(str)=='TOT','fg3a'].iloc[0]
        else:
            v=g.fg3a.sum()
        threes[int(pid)]+=int(v or 0)

W=1800; H=1800
BG=(246,246,242); NAVY=(22,31,40); MUTED=(101,108,114); RULE=(219,221,218)
BLUE=(145,199,225); BLUE_DARK=(55,125,161); RED=(168,62,58); WHITE=(255,255,255)
CARD=(253,253,251); SHADOW=(229,230,227)

im=Image.new('RGB',(W,H),BG); d=ImageDraw.Draw(im)

def F(sz,b=False,cond=False):
    if cond:
        path='/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf'
    else:
        path='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    return ImageFont.truetype(path,sz)

def fit_font(text,maxw,start=30,minsz=18,b=True,cond=True):
    for s in range(start,minsz-1,-1):
        f=F(s,b,cond)
        if d.textbbox((0,0),text,font=f)[2] <= maxw:
            return f
    return F(minsz,b,cond)

# Broadcast-style header
header_h=255
d.rectangle((0,0,W,header_h),fill=NAVY)
d.text((92,62),'TEAM 3PT% IMPACT',font=F(78,True,True),fill=WHITE)
d.text((94,147),'BIGGEST ON-OFF SWINGS • LAST 5 SEASONS',font=F(30,True,True),fill=BLUE)
d.text((1698,73),'TOP 25',font=F(38,True,True),fill=WHITE,anchor='ra')
d.text((1698,124),'3,500+ MINUTES',font=F(22,True,True),fill=BLUE,anchor='ra')
d.text((1698,166),'2021-22 — 2025-26',font=F(20),fill=(195,202,208),anchor='ra')

# Dataset note strip
strip_y=255
d.rectangle((0,strip_y,W,strip_y+56),fill=(235,237,235))
d.text((92,strip_y+28),'SWING = TEAM 3PT% ON COURT − OFF COURT  •  TOTAL 3PA SHOWN',font=F(20,True,True),fill=MUTED,anchor='lm')

S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0','Accept':'image/avif,image/webp,image/*,*/*;q=0.8'})

def head(pid):
    r=S.get(f'https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png',timeout=30); r.raise_for_status()
    pic=Image.open(io.BytesIO(r.content)).convert('RGBA')
    bb=pic.getchannel('A').getbbox(); pic=pic.crop(bb) if bb else pic
    w,h=pic.size
    # Head-and-shoulders crop; avoid visible jersey/chest.
    pic=pic.crop((0,0,w,max(1,int(h*.69))))
    target_w,target_h=178,150
    scale=min(target_w/pic.width,target_h/pic.height)
    nw,nh=max(1,int(pic.width*scale)),max(1,int(pic.height*scale))
    pic=pic.resize((nw,nh),Image.Resampling.LANCZOS)
    canvas=Image.new('RGBA',(186,154),(0,0,0,0))
    canvas.alpha_composite(pic,((186-nw)//2,154-nh))
    return canvas

# 5x5 sports-card grid
left=84; top_y=344; gap_x=18; gap_y=20
card_w=310; card_h=258
for i,(rank,pid,name,val) in enumerate(players):
    row=i//5; col=i%5
    x=left+col*(card_w+gap_x); y=top_y+row*(card_h+gap_y)
    # small shadow + crisp card
    d.rounded_rectangle((x+4,y+5,x+card_w+4,y+card_h+5),radius=16,fill=SHADOW)
    d.rounded_rectangle((x,y,x+card_w,y+card_h),radius=16,fill=CARD,outline=RULE,width=2)
    # top accent rule
    d.rounded_rectangle((x,y,x+card_w,y+8),radius=8,fill=BLUE_DARK)
    # rank badge
    d.ellipse((x+16,y+20,x+60,y+64),fill=NAVY)
    d.text((x+38,y+42),str(rank),font=F(20,True,True),fill=WHITE,anchor='mm')
    # headshot
    hs=head(pid); im.paste(hs,(x+62,y+18),hs)
    # main swing metric top-right
    d.text((x+card_w-16,y+26),f'+{val:.2f}',font=F(36,True,True),fill=NAVY,anchor='ra')
    d.text((x+card_w-16,y+65),'3PT% SWING',font=F(14,True,True),fill=BLUE_DARK,anchor='ra')
    # divider under image
    d.line((x+16,y+164,x+card_w-16,y+164),fill=RULE,width=1)
    # player name
    name_font=fit_font(name,card_w-32,start=29,minsz=18,b=True,cond=True)
    d.text((x+16,y+184),name,font=name_font,fill=NAVY,anchor='lm')
    # 3PA stat treatment
    d.text((x+16,y+225),'3PA',font=F(15,True,True),fill=MUTED,anchor='lm')
    d.text((x+card_w-16,y+225),f'{threes[pid]:,}',font=F(24,True,True),fill=NAVY,anchor='rm')

# Footer
footer_y=1750
d.line((84,footer_y-30,1716,footer_y-30),fill=RULE,width=2)
d.text((84,footer_y),'Regular season • 3PA = player three-point attempts in period',font=F(19),fill=MUTED,anchor='ls')
d.text((1716,footer_y),'@funakistats',font=F(22,True),fill=RED,anchor='rs')

im.save('outputs/top25_team_3pt_swing_headshots_3pa.png','PNG',optimize=True)
with open('outputs/top25_team_3pt_swing_3pa.csv','w') as f:
    f.write('rank,player_id,player,team_3pt_swing_pp,three_point_attempts\n')
    for rank,pid,name,val in players:
        f.write(f'{rank},{pid},"{name}",{val:.6f},{threes[pid]}\n')
