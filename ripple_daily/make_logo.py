"""High-res redraw of the 오늘의 코인뉴스 globe logo.
Usage: python3 make_logo.py <out_dir>   (needs: pip install global-land-mask)
Writes <out_dir>/coinnews_logo_transparent.png and coinnews_logo_hd.png"""
import sys, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
from global_land_mask import globe

S = 2400                      # canvas (square)
R = int(S * 0.40)             # globe radius
C = S // 2
LAT0, LON0 = np.radians(18), np.radians(12)
BLACK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"
BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

def blur(img, r): return img.filter(ImageFilter.GaussianBlur(r))

# ---------- sphere geometry ----------
y, x = np.mgrid[0:S, 0:S].astype(np.float32)
X = (x - C) / R; Y = -(y - C) / R
rr = X**2 + Y**2
inside = rr <= 1.0
Z = np.sqrt(np.clip(1 - rr, 0, 1))

# inverse orthographic -> lat/lon
rho = np.sqrt(rr) + 1e-9
cc = np.arcsin(np.clip(rho, 0, 1))
lat = np.arcsin(np.clip(np.cos(cc) * np.sin(LAT0) + Y * np.sin(cc) * np.cos(LAT0) / rho, -1, 1))
lon = LON0 + np.arctan2(X * np.sin(cc), rho * np.cos(LAT0) * np.cos(cc) - Y * np.sin(LAT0) * np.sin(cc))
latd = np.degrees(lat); lond = (np.degrees(lon) + 180) % 360 - 180

land = np.zeros((S, S), bool)
idx = np.where(inside)
land[idx] = globe.is_land(np.clip(latd[idx], -89.9, 89.9), np.clip(lond[idx], -179.9, 179.9))

# ---------- ocean shading ----------
lx, ly, lz = -0.45, 0.55, 0.70                       # light from upper-left/front
ndl = np.clip(X * lx + Y * ly + Z * lz, 0, 1)
limb = Z ** 0.55
deep = np.array([3, 18, 78], np.float32)
mid = np.array([14, 78, 210], np.float32)
bright = np.array([60, 170, 255], np.float32)
t = np.clip(0.15 + 0.85 * (0.55 * limb + 0.45 * ndl), 0, 1)[..., None]
ocean = deep * (1 - t) + mid * t
ocean = ocean + (bright - mid) * np.clip((ndl - 0.55) / 0.45, 0, 1)[..., None] * 0.55

# graticule (every 15 deg) — thin glowing lines
def lines(v, step, w):
    d = np.abs(((v + step / 2) % step) - step / 2)
    return np.clip(1 - d / w, 0, 1)
grat = np.maximum(lines(latd, 15, 0.28), lines(lond, 15, 0.28 / np.clip(np.cos(lat), 0.15, 1)))
grat *= limb ** 0.8

# land: dot-matrix + soft fill + bright coastline
dot_step = 1.25
gy = ((latd + 90) / dot_step); gx = ((lond + 180) / dot_step)
dy = gy - np.round(gy); dx = (gx - np.round(gx)) * np.clip(np.cos(lat), 0.2, 1)
dots = np.clip(1 - np.sqrt(dx**2 + dy**2) / 0.33, 0, 1)
land_f = land.astype(np.float32)
land_soft = np.array(blur(Image.fromarray((land_f * 255).astype(np.uint8)), 3), np.float32) / 255
coast = np.clip(np.abs(land_soft - land_f) * 2.2, 0, 1) * land_f
land_glow = np.array(blur(Image.fromarray((land_f * 255).astype(np.uint8)), 22), np.float32) / 255

cyan = np.array([110, 235, 255], np.float32)
white = np.array([235, 252, 255], np.float32)
img = ocean.copy()
img += cyan * 0.22 * land_glow[..., None] * limb[..., None]
img = img * (1 - 0.55 * land_f[..., None]) + (cyan * 0.85 + ocean * 0.15) * 0.55 * land_f[..., None] * (0.55 + 0.45 * limb[..., None])
img += white * 0.55 * (dots * land_f * limb)[..., None]
img += white * 0.45 * (coast * limb)[..., None]
img += np.array([90, 190, 255], np.float32) * 0.20 * grat[..., None]

# specular highlight + rim light
spec = np.exp(-(((X + 0.42) ** 2) / 0.10 + ((Y - 0.48) ** 2) / 0.06))
img += 255 * 0.22 * spec[..., None]
rim = np.clip((np.sqrt(rr) - 0.86) / 0.14, 0, 1) ** 2.2
img = img * (1 - 0.35 * rim[..., None]) + np.array([90, 200, 255], np.float32) * 0.55 * rim[..., None]
img = np.clip(img, 0, 255)

globe_rgb = np.zeros((S, S, 4), np.uint8)
globe_rgb[..., :3] = img.astype(np.uint8)
# anti-aliased edge
edge_a = np.clip((1 - np.sqrt(rr)) * R + 0.5, 0, 1)
globe_rgb[..., 3] = (edge_a * 255).astype(np.uint8)
G = Image.fromarray(globe_rgb, "RGBA")

# ---------- outer atmosphere glow ----------
base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
halo = Image.new("L", (S, S), 0)
ImageDraw.Draw(halo).ellipse([C - R - 18, C - R - 18, C + R + 18, C + R + 18], fill=255)
halo = blur(halo, 34)
glow = Image.new("RGBA", (S, S), (40, 150, 255, 0)); glow.putalpha(halo.point(lambda v: int(v * 0.85)))
base = Image.alpha_composite(base, glow)
base = Image.alpha_composite(base, G)

# light streak rings (orbit arcs)
streak = Image.new("RGBA", (S, S), (0, 0, 0, 0))
sd = ImageDraw.Draw(streak)
for (rx, ry, rot_start, rot_end, w, a) in [
    (1.02, 1.02, 200, 340, 10, 210), (0.95, 0.95, 215, 300, 6, 150), (1.05, 1.05, 20, 75, 6, 130)]:
    box = [C - R * rx, C - R * ry, C + R * rx, C + R * ry]
    sd.arc(box, rot_start, rot_end, fill=(170, 235, 255, a), width=w)
streak = Image.alpha_composite(blur(streak, 10), blur(streak, 2))
base = Image.alpha_composite(base, streak)

# ---------- news banner ----------
bl, br = C - int(0.86 * R), C + int(0.86 * R)
w_top, w_bot = C - int(0.30 * R), C - int(0.175 * R)
r_top, r_bot = w_bot, C + int(0.20 * R)

shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(shadow).rectangle([bl + 6, w_top + 22, br + 16, r_bot + 30], fill=(0, 8, 40, 170))
base = Image.alpha_composite(base, blur(shadow, 22))

ban = Image.new("RGBA", (S, S), (0, 0, 0, 0))
bd = ImageDraw.Draw(ban)
# white strip with subtle vertical gradient
for yy in range(w_top, w_bot):
    k = (yy - w_top) / max(1, w_bot - w_top)
    c = int(255 - 26 * k)
    bd.line([(bl, yy), (br, yy)], fill=(c, c, min(255, c + 4), 255))
# red bar gradient
for yy in range(r_top, r_bot):
    k = (yy - r_top) / max(1, r_bot - r_top)
    r_ = int(226 - 80 * k); g_ = int(36 - 22 * k); b_ = int(44 - 22 * k)
    bd.line([(bl, yy), (br, yy)], fill=(r_, g_, b_, 255))
# glossy top highlight on red bar
gloss = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(gloss).rectangle([bl, r_top, br, r_top + int((r_bot - r_top) * 0.42)], fill=(255, 255, 255, 46))
ban = Image.alpha_composite(ban, gloss)
bd = ImageDraw.Draw(ban)
bd.line([(bl, r_top), (br, r_top)], fill=(120, 10, 16, 255), width=4)
bd.line([(bl, r_bot - 3), (br, r_bot - 3)], fill=(90, 6, 10, 255), width=6)
# accent block on the left edge (like the original's side tab)
bd.rectangle([bl - 34, r_top + 18, bl - 14, r_bot - 18], fill=(200, 24, 34, 255))
bd.rectangle([bl - 34, w_top + 10, bl - 14, w_bot - 6], fill=(236, 240, 248, 255))
base = Image.alpha_composite(base, ban)

# small caption on white strip
cap = "TODAY  COIN  NEWS"
f_cap = ImageFont.truetype(BOLD, int((w_bot - w_top) * 0.58), index=1)
d = ImageDraw.Draw(base)
tw = d.textlength(cap, font=f_cap)
d.text(((bl + br) / 2 - tw / 2, (w_top + w_bot) / 2), cap, font=f_cap, fill=(20, 30, 70, 255), anchor="lm")

# main title
t1, t2 = "오늘의", "코인뉴스"
fsize = int((r_bot - r_top) * 0.70)
while True:
    f_t = ImageFont.truetype(BLACK, fsize, index=1)
    gap = int(fsize * 0.38)
    w1 = d.textlength(t1, font=f_t); w2 = d.textlength(t2, font=f_t)
    total = w1 + gap + w2
    if total <= 0.84 * (br - bl): break
    fsize -= 4
x0 = (bl + br) / 2 - total / 2
ymid = (r_top + r_bot) / 2 + fsize * 0.03
# 오늘의: dark navy with thin light outline
d.text((x0, ymid), t1, font=f_t, fill=(16, 24, 64, 255), anchor="lm",
       stroke_width=3, stroke_fill=(250, 150, 150, 255))
# 코인뉴스: white with glow
gl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(gl).text((x0 + w1 + gap, ymid), t2, font=f_t, fill=(255, 220, 230, 255), anchor="lm")
base = Image.alpha_composite(base, blur(gl, 18))
d = ImageDraw.Draw(base)
d.text((x0 + w1 + gap, ymid), t2, font=f_t, fill=(255, 255, 255, 255), anchor="lm",
       stroke_width=3, stroke_fill=(120, 8, 16, 255))

# lens flare behind 코인뉴스 start
fl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
fx, fy = br - int(0.18 * R), r_top
ImageDraw.Draw(fl).rectangle([fx - 360, fy - 2, fx + 260, fy + 2], fill=(255, 240, 250, 170))
base = Image.alpha_composite(base, blur(fl, 14))

# ---------- outputs ----------
bbox = base.getbbox()
logo = base.crop(bbox)
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(OUT, exist_ok=True)
logo.save(os.path.join(OUT, "coinnews_logo_transparent.png"), optimize=True)

# version on light-gray background, same proportions as the original (160x102)
W, H = 2560, 1632
bg = Image.new("RGBA", (W, H), (241, 241, 241, 255))
scale = (H * 0.86) / logo.height
lg = logo.resize((int(logo.width * scale), int(logo.height * scale)), Image.LANCZOS)
bg.alpha_composite(lg, ((W - lg.width) // 2, (H - lg.height) // 2))
bg.convert("RGB").save(os.path.join(OUT, "coinnews_logo_hd.png"), optimize=True)
print(logo.size, bg.size)
