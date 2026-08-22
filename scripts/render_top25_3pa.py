import io, requests, pandas as pd
from PIL import Image,ImageDraw,ImageFont
P=[(1,1627750,'Jamal Murray',4.421957),(2,202331,'Paul George',3.560839),(3,203999,'Nikola Jokić',3.327061),(4,1628379,'Luke Kennard',3.185049),(5,201939,'Stephen Curry',3.156923),(6,202695,'Kawhi Leonard',2.975750),(7,202681,'Kyrie Irving',2.959188),(8,203954,'Joel Embiid',2.798739),(9,201142,'Kevin Durant',2.716807),(10,1629008,'Michael Porter Jr.',2.661839),(11,1630174,'Aaron Nesmith',2.647158),(12,1631260,'AJ Green',2.608743),(13,1630163,'LaMelo Ball',2.576754),(14,203994,'Jusuf Nurkić',2.536215),(15,1630178,'Tyrese Maxey',2.519183),(16,1630169,'Tyrese Haliburton',2.373862),(17,1629639,'Tyler Herro',2.353698),(18,203468,'CJ McCollum',2.344965),(19,1641706,'Brandon Miller',2.226628),(20,1630703,'Scoot Henderson',2.210929),(21,1631094,'Paolo Banchero',2.210135),(22,1627777,'Georges Niang',2.180776),(23,203500,'Steven Adams',2.165567),(24,1628384,'OG Anunoby',2.161761),(25,203484,'Kentavious Caldwell-Pope',2.129610)]
ids={x[1] for x in P}; A={i:0 for i in ids}
for yr in [2022,2023,2024,2025,2026]:
 d=pd.read_parquet(f'https://raw.githubusercontent.com/llimllib/nba_data/main/data/players_{yr}.parquet'); d=d[d.player_id.isin(ids)]
 for pid,g in d.groupby('player_id'):
  v=g.loc[g.team_abbreviation.astype(str)=='TOT','fg3a']; A[int(pid)]+=int(v.iloc[0] if len(v) else g.fg3a.sum())
W,H=1920,1080; BG=(13,20,29); PANEL=(20,29,40); WHITE=(245,247,249); MUT=(148,159,171); RULE=(49,62,76); RED=(210,43,48); CYAN=(87,183,214)
im=Image.new('RGB',(W,H),BG); D=ImageDraw.Draw(im)
def F(n,b=False,c=False):
 p='/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf'; return ImageFont.truetype(p,n)
# broadcast masthead
D.rectangle((0,0,W,12),fill=RED); D.text((72,52),'TEAM 3PT% IMPACT',font=F(58,1),fill=WHITE); D.text((74,121),'BIGGEST ON-OFF SWINGS',font=F(26,1),fill=CYAN)
D.text((74,161),'2021-22 — 2025-26   |   3,500+ MINUTES   |   PLAYER 3PA SHOWN',font=F(20,1),fill=MUT)
D.text((1848,70),'@funakistats',font=F(22,1),fill=(204,207,211),anchor='ra')
D.line((72,208,1848,208),fill=RULE,width=2)
# column headings and rows
S=requests.Session(); S.headers.update({'User-Agent':'Mozilla/5.0'})
def head(pid):
 r=S.get(f'https://cdn.nba.com/headshots/nba/latest/1040x760/{pid}.png',timeout=30); r.raise_for_status(); q=Image.open(io.BytesIO(r.content)).convert('RGBA'); bb=q.getchannel('A').getbbox(); q=q.crop(bb) if bb else q; w,h=q.size; q=q.crop((0,0,w,int(h*.70))); scale=min(94/q.width,94/q.height); q=q.resize((int(q.width*scale),int(q.height*scale)),Image.Resampling.LANCZOS); z=Image.new('RGBA',(98,98)); z.alpha_composite(q,((98-q.width)//2,98-q.height)); return z
left=72; gap=18; cw=(1776-gap*4)//5; top=250; rh=145
for c in range(5):
 x=left+c*(cw+gap); D.rectangle((x,top-20,x+cw,top+5*rh+12),fill=PANEL); D.text((x+18,top-2),'RANK / PLAYER',font=F(15,1),fill=MUT); D.text((x+cw-18,top-2),'SWING',font=F(15,1),fill=MUT,anchor='ra')
 for r in range(5):
  rank,pid,name,val=P[c*5+r]; y=top+30+r*rh
  if r: D.line((x+14,y-10,x+cw-14,y-10),fill=RULE,width=1)
  D.text((x+18,y+47),f'{rank:02d}',font=F(20,1),fill=MUT,anchor='lm'); hs=head(pid); im.paste(hs,(x+52,y+3),hs)
  # name fitted to fixed broadcast column
  fs=23
  while D.textbbox((0,0),name,font=F(fs,1))[2] > cw-190 and fs>17: fs-=1
  D.text((x+151,y+31),name,font=F(fs,1),fill=WHITE,anchor='lm')
  D.text((x+151,y+67),f'{A[pid]:,} 3PA',font=F(16,1),fill=MUT,anchor='lm')
  D.text((x+cw-18,y+38),f'+{val:.2f}',font=F(30,1),fill=WHITE,anchor='rm'); D.text((x+cw-18,y+72),'PTS',font=F(14,1),fill=CYAN,anchor='rm')
# lower third explainer
fy=1014; D.rectangle((0,fy,W,H),fill=(9,14,21)); D.rectangle((72,fy+18,78,H-18),fill=RED)
D.text((96,fy+19),'TEAM 3PT% ON − TEAM 3PT% OFF',font=F(18,1),fill=WHITE); D.text((96,fy+44),'Swing shown in percentage points. 3PA = player regular-season three-point attempts during the period.',font=F(15),fill=MUT)
im.save('outputs/top25_team_3pt_swing_headshots_3pa.png','PNG',optimize=True)
