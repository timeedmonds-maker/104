import io, requests, pandas as pd
from PIL import Image, ImageDraw, ImageFont

players=[
(1,1627750,'Jamal Murray',4.421957),(2,202331,'Paul George',3.560839),(3,203999,'Nikola Jokić',3.327061),(4,1628379,'Luke Kennard',3.185049),(5,201939,'Stephen Curry',3.156923),(6,202695,'Kawhi Leonard',2.975750),(7,202681,'Kyrie Irving',2.959188),(8,203954,'Joel Embiid',2.798739),(9,201142,'Kevin Durant',2.716807),(10,1629008,'Michael Porter Jr.',2.661839),(11,1630174,'Aaron Nesmith',2.647158),(12,1631260,'AJ Green',2.608743),(13,1630163,'LaMelo Ball',2.576754),(14,203994,'Jusuf Nurkić',2.536215),(15,1630178,'Tyrese Maxey',2.519183),(16,1630169,'Tyrese Haliburton',2.373862),(17,1629639,'Tyler Herro',2.353698),(18,203468,'CJ McCollum',2.344965),(19,1641706,'Brandon Miller',2.226628),(20,1630703,'Scoot Henderson',2.210929),(21,1631094,'Paolo Banchero',2.210135),(22,1627777,'Georges Niang',2.180776),(23,203500,'Steven Adams',2.165567),(24,1628384,'OG Anunoby',2.161761),(25,203484,'Kentavious Caldwell-Pope',2.129610)
]

ids={p[1] for p in players}
threes={pid:0 for pid in ids}
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
BG=(247,247,244); NAVY=(26,35,44); MUTED=(101,108,114); RULE=(221,222,219)
BLUE=(143,196,222); BLUE_DARK=(65,133,166); RED=(167,68,62); WHITE=(255,255,255)

im=Image.new('RGB',(W,H),BG)
d=ImageDraw.Draw(im)

def F(sz,b=False,cond=False):
    if cond:
        path='/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf'
    else:
        path='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    return ImageFont.truetype(path,sz)

# Header block
xL=100; xR=1700
d.text((xL,70),'TEAM 3PT% IMPACT',font=F(72,True,True),fill=NAVY)
d.text((xL,152),'BIGGEST ON-OFF SWINGS  |  LAST 5 SEASONS',font=F(29,True,True),fill=BLUE_DARK)
d.text((xL,200),'2021-22 to 2025-26  •  3,500+ minutes  •  total 3PA shown for each player',font=F(24),fill=MUTED)
d.line((xL,252,xR,252),fill=NAVY,width=3)

# Column headers
col1=100; col2=930; colw=770
for x in [col1,col2]:
    d.text((x,284),'RANK',font=F(18,True),fill=MUTED)
    d.text((x+165,284),'PLAYER',font=F(18,True),fill=MUTED)
    d.text((x+560,284),'3PA',font=F(18,True),fill=MUTED,anchor='ra')
    d.text((x+760,284),'3PT% SWING',font=F(18,True),fill=MUTED,anchor='ra')
    d.line((x,318,x+760,318),fill=RULE,width=2)

S=requests.Session()
S.headers.update({'User-Agent':'Mozilla/5.0','Accept':'image/avif,image/webp,image/*,*/*;q=0.8'})

def head(pid):
    r=S.get(f'https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png',timeout=30)
    r.raise_for_status()
    pic=Image.open(io.BytesIO(r.content)).convert('RGBA')
    bb=pic.getchannel('A').getbbox()
    if bb: pic=pic.crop(bb)
    w,h=pic.size
    # Crop high enough to remove most jersey while preserving shoulders/head.
    pic=pic.crop((0,0,w,max(1,int(h*.72))))
    target=94
    scale=min(target/pic.width,target/pic.height)
    nw,nh=max(1,int(pic.width*scale)),max(1,int(pic.height*scale))
    pic=pic.resize((nw,nh),Image.Resampling.LANCZOS)
    c=Image.new('RGBA',(100,100),(0,0,0,0))
    c.alpha_composite(pic,((100-nw)//2,100-nh))
    return c

# Rows: 13 left, 12 right
cols=[(players[:13],col1),(players[13:],col2)]
row0=342; rowh=104
for group,x in cols:
    for j,(rank,pid,name,val) in enumerate(group):
        y=row0+j*rowh
        if j:
            d.line((x,y-10,x+760,y-10),fill=RULE,width=1)
        # Rank: restrained, not decorative
        d.text((x+18,y+46),str(rank),font=F(26,True,True),fill=MUTED,anchor='mm')
        # Real official headshot
        hs=head(pid)
        im.paste(hs,(x+55,y-1),hs)
        # Player name
        d.text((x+165,y+31),name,font=F(27,True,True),fill=NAVY,anchor='lm')
        # 3PA is secondary information
        d.text((x+560,y+48),f'{threes[pid]:,}',font=F(23,True),fill=MUTED,anchor='rm')
        # Main metric, right-aligned
        d.text((x+760,y+33),f'+{val:.2f}',font=F(34,True,True),fill=NAVY,anchor='rm')
        d.text((x+760,y+64),'percentage points',font=F(15),fill=MUTED,anchor='rm')

# Footer
footer_y=1738
d.line((xL,footer_y-35,xR,footer_y-35),fill=RULE,width=2)
d.text((xL,footer_y),'Team 3PT% ON minus OFF  •  3PA = regular-season three-point attempts in period',font=F(20),fill=MUTED,anchor='ls')
d.text((xR,footer_y),'@funakistats',font=F(22,True),fill=RED,anchor='rs')

im.save('outputs/top25_team_3pt_swing_headshots_3pa.png','PNG',optimize=True)
with open('outputs/top25_team_3pt_swing_3pa.csv','w') as f:
    f.write('rank,player_id,player,team_3pt_swing_pp,three_point_attempts\n')
    for rank,pid,name,val in players:
        f.write(f'{rank},{pid},"{name}",{val:.6f},{threes[pid]}\n')
