# Draws the Hivegrid logo (seven hexagons, glowing centre) on a transparent canvas.
# Usage: python3 make-icon.py [output.png]   (needs Pillow)
import math
from PIL import Image, ImageDraw, ImageFilter
N=1024; SS=2; W=N*SS
R=99.0; D=188.0            # hex circumradius, centre spacing (source px)
centres=[(0,0)]+[(D*math.cos(math.radians(a)), -D*math.sin(math.radians(a))) for a in (90,30,-30,-90,-150,150)]
def hexpts(cx,cy,r):
    return [(cx+r*math.cos(math.radians(60*i)), cy+r*math.sin(math.radians(60*i))) for i in range(6)]
# fit cluster (extent incl. outline) into ~97% of the canvas
xs=[p[0] for c in centres for p in hexpts(*c,R)]; ys=[p[1] for c in centres for p in hexpts(*c,R)]
ext=max(max(xs)-min(xs), max(ys)-min(ys))
s=W*0.965/ext
cx0=W/2-(max(xs)+min(xs))/2*s; cy0=W/2-(max(ys)+min(ys))/2*s
def T(p): return (cx0+p[0]*s, cy0+p[1]*s)
def poly(c,r): return [T(p) for p in hexpts(c[0],c[1],r)]
# glow behind everything, from the centre cell
ga=Image.new("L",(W,W),0)
ImageDraw.Draw(ga).polygon(poly(centres[0],R*1.25),fill=200)
ga=ga.filter(ImageFilter.GaussianBlur(W*0.035))
glow=Image.new("RGBA",(W,W),(24,140,255,0)); glow.putalpha(ga)
img=Image.new("RGBA",(W,W),(0,0,0,0))
img.alpha_composite(glow)
d=ImageDraw.Draw(img)
gap=0.90   # shrink so cells keep the source gap
for i,c in enumerate(centres):
    pts=poly(c,R*gap)
    if i==0:
        d.polygon(pts,fill=(14,132,255,255))
    else:
        d.polygon(pts,fill=(7,24,58,255))
    d.line(pts+[pts[0]],fill=(66,150,255,255) if i else (150,205,255,255),width=int(7*SS*1.0),joint="curve")
# bright core on the centre cell
ca=Image.new("L",(W,W),0)
ImageDraw.Draw(ca).polygon(poly(centres[0],R*0.55),fill=230)
ca=ca.filter(ImageFilter.GaussianBlur(W*0.03))
core=Image.new("RGBA",(W,W),(60,175,255,0)); core.putalpha(ca)
mask=Image.new("L",(W,W),0); ImageDraw.Draw(mask).polygon(poly(centres[0],R*gap),fill=255)
img.alpha_composite(Image.composite(core,Image.new("RGBA",(W,W),(0,0,0,0)),mask))
out=img.resize((N,N),Image.LANCZOS)
import sys
out.resize((512,512),Image.LANCZOS).save(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
