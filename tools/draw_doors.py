"""Draw generic front-view door pictures for door styles that have no brochure photo.

Run from the repo root:  python tools/draw_doors.py
Writes orderapp/static/option_photos/door_style/<slug>.jpg (146 x 360, same as the brochure crops).
Never overwrites a file that already exists (the brochure photos win), unless --force is given.
These are drawings, not product photos - a real photo uploaded in Settings > Product photos replaces one.
"""
import os
import random
import shutil
import sys

from PIL import Image, ImageDraw, ImageFilter

W, H = 146, 360
S = 4                      # draw big, then shrink (smooth edges)
OUT = os.path.join(os.path.dirname(__file__), "..", "orderapp", "static", "option_photos", "door_style")

# panel layouts as (x0, y0, x1, y1) fractions of the door, plus which panels get an arched top
STILE = 0.15
L, R = STILE, 1 - STILE
MID_L, MID_R = 0.47, 0.53


def layout(n, kind=""):
    if n == 0:
        return []
    if kind == "craftsman":            # one wide panel on top, two tall below
        return [(L, .07, R, .30), (L, .37, MID_L, .93), (MID_R, .37, R, .93)]
    if n == 1:
        return [(L, .06, R, .94)]
    if n == 2:
        return [(L, .06, R, .52), (L, .60, R, .93)]
    if n == 3:
        return [(L, .06, R, .33), (L, .37, R, .63), (L, .67, R, .93)] if kind == "equal" else \
               [(L, .06, R, .40), (L, .46, R, .66), (L, .72, R, .93)]
    if n == 4:
        return [(L, .06, MID_L, .53), (MID_R, .06, R, .53), (L, .60, MID_L, .93), (MID_R, .60, R, .93)]
    if n == 5:
        top, bot, gap = .06, .94, .035
        h = (bot - top - gap * 4) / 5
        return [(L, top + i * (h + gap), R, top + i * (h + gap) + h) for i in range(5)]
    if n == 6:
        return [(L, .05, MID_L, .17), (MID_R, .05, R, .17), (L, .22, MID_L, .55), (MID_R, .22, R, .55),
                (L, .61, MID_L, .94), (MID_R, .61, R, .94)]
    raise ValueError(n)


def arched_rows(boxes):
    """The top row of panels gets the arch."""
    if not boxes:
        return set()
    top = min(b[1] for b in boxes)
    return {i for i, b in enumerate(boxes) if abs(b[1] - top) < 1e-6}


def _shape(d, box, arch, fill=None, outline=None, width=1):
    x0, y0, x1, y1 = box
    if not arch:
        d.rectangle(box, fill=fill, outline=outline, width=width)
        return
    rise = (x1 - x0) * 0.28                        # how high the arch goes
    d.rectangle((x0, y0 + rise, x1, y1), fill=fill, outline=None)
    d.chord((x0, y0, x1, y0 + 2 * rise), 180, 360, fill=fill, outline=None)
    if outline:
        d.line((x0, y0 + rise, x0, y1, x1, y1, x1, y0 + rise), fill=outline, width=width)
        d.arc((x0, y0, x1, y0 + 2 * rise), 180, 360, fill=outline, width=width)


def _rect_px(fbox, dw, dh, ox, oy):
    x0, y0, x1, y1 = fbox
    return (ox + x0 * dw, oy + y0 * dh, ox + x1 * dw, oy + y1 * dh)


def _inset(box, a, arch=False):
    x0, y0, x1, y1 = box
    return (x0 + a, y0 + a, x1 - a, y1 - a)


def draw(spec):
    """spec: panels (0-6), kind ('' / equal / craftsman), profile ('raised' / 'flat'), arch, plank,
    texture (bool), base colour, glass/mirror."""
    big = Image.new("RGB", (W * S, H * S), (250, 250, 248))
    d = ImageDraw.Draw(big)
    base = spec.get("base", (243, 243, 238))
    lite = (255, 255, 255)
    shade = tuple(max(0, c - 40) for c in base)
    deep = tuple(max(0, c - 95) for c in base)
    ox, oy = 2 * S, 2 * S
    dw, dh = W * S - 4 * S, H * S - 4 * S
    # slab
    d.rectangle((ox, oy, ox + dw, oy + dh), fill=base, outline=(150, 150, 150), width=S)
    if spec.get("mirror"):
        m = (ox + 4 * S, oy + 4 * S, ox + dw - 4 * S, oy + dh - 4 * S)
        d.rectangle(m, fill=(196, 208, 214), outline=(140, 140, 140), width=S)
        for k in range(4):     # reflection streaks
            x = m[0] + (0.45 + 0.12 * k) * (m[2] - m[0])
            d.line((x, m[1] + 30 * S, x - 40 * S, m[1] + 150 * S), fill=(228, 236, 240), width=(5 - k) * S)
    boxes = layout(spec.get("panels", 0), spec.get("kind", ""))
    arch = arched_rows(boxes) if spec.get("arch") else set()
    for i, fb in enumerate(boxes):
        b = _rect_px(fb, dw, dh, ox, oy)
        a = i in arch
        if spec.get("profile", "raised") == "raised":
            bev = 6 * S
            _shape(d, (b[0] + 2 * S, b[1] + 2 * S, b[2] + 2 * S, b[3] + 2 * S), a, fill=lite)  # light edge
            _shape(d, b, a, fill=shade)                                            # bevel, shadow side
            _shape(d, (b[0] + 3 * S, b[1] + 3 * S, b[2], b[3]), a, fill=lite)      # bevel, lit side
            _shape(d, _inset(b, bev), a, fill=deep)
            _shape(d, (b[0] + bev + S, b[1] + bev + S, b[2] - bev, b[3] - bev), a, fill=base)  # raised field
            _shape(d, b, a, outline=deep, width=2 * S)
        else:                                                                       # flat shaker panel
            _shape(d, (b[0], b[1], b[2] + 2 * S, b[3] + 2 * S), a, fill=lite)
            _shape(d, b, a, fill=shade)
            _shape(d, (b[0] + 3 * S, b[1] + 3 * S, b[2], b[3]), a, fill=base)
            _shape(d, b, a, outline=deep, width=S)
        if spec.get("plank"):
            inner = _inset(b, 6 * S)
            n = 6
            for k in range(1, n):
                x = inner[0] + (inner[2] - inner[0]) * k / n
                ytop = inner[1] + ((inner[2] - inner[0]) * 0.28 if a else 0)
                d.line((x, ytop + 2 * S, x, inner[3]), fill=deep, width=S)
                d.line((x + S, ytop + 2 * S, x + S, inner[3]), fill=lite, width=S)
    if spec.get("glass"):
        g = _rect_px((L, .07, R, .45), dw, dh, ox, oy)
        d.rectangle(g, fill=(200, 214, 222), outline=deep, width=S)
    img = big
    if spec.get("texture"):            # light wood-grain emboss
        rnd = random.Random(spec.get("seed", 7))
        grain = Image.new("L", big.size, 0)
        gd = ImageDraw.Draw(grain)
        for _ in range(220):
            x = rnd.uniform(0, big.size[0])
            y = rnd.uniform(0, big.size[1])
            gd.line((x, y, x + rnd.uniform(-6, 6) * S, y + rnd.uniform(40, 140) * S), fill=rnd.randint(10, 26),
                    width=S)
        grain = grain.filter(ImageFilter.GaussianBlur(S))
        img = Image.composite(Image.new("RGB", big.size, deep), big, grain)
    img = img.resize((W, H), Image.LANCZOS)
    if spec.get("knob", True):
        dd = ImageDraw.Draw(img)
        kx, ky = W - 16, int(H * 0.56)
        dd.ellipse((kx - 5, ky - 5, kx + 5, ky + 5), fill=(170, 160, 140), outline=(110, 100, 85))
    return img


def P(n, **kw):
    return dict(panels=n, **kw)


TEX = dict(texture=True)
STEEL = dict(base=(232, 234, 236))

SPECS = {
    # company form names (Door / Bi-Pass forms)
    "two-panel-embossed": P(2, **TEX),          # Classique
    "four-panel-embossed": P(4, **TEX),         # Carmelle
    "three-panel-embossed": P(3, kind="equal", **TEX),   # Clermont
    "six-panel-steel": P(6, **STEEL),
    "steel-door": P(6, **STEEL),
    "colonist": P(6, **TEX),
    "colonial": P(6, **TEX),
    # designs
    "shaker": P(1, profile="flat"),
    "shaker-craftsman": P(3, kind="craftsman", profile="flat"),
    "shaker-equal": P(3, kind="equal", profile="flat"),
    "arch": P(2, arch=True),
    "arch-plank": P(2, arch=True, plank=True, profile="flat"),
    "plank": P(2, plank=True, profile="flat"),
    "smooth": P(0),
    "flush": P(0),
    "carrara": P(2),                            # 2-panel square top, smooth
    "carrera": P(2),
    "coventry": P(4),                           # classic 4-panel, smooth
    "lincoln-park": P(1, profile="flat"),       # 1-panel shaker
    "riverside": P(5, profile="flat"),          # 5-panel shaker
    "cheyenne": P(2, profile="flat"),           # best guess: 2-panel square-top shaker
    "camden": P(2, arch=True),                  # best guess: 2-panel arched top
    "cremona": P(2, arch=True, profile="flat"),  # best guess: 2-panel arch, flat panels
    "roman": P(2, arch=True),                   # best guess: 2-panel arch
    "rockport": P(1),                           # best guess: 1 raised panel, smooth
    "mirrored": dict(panels=0, mirror=True, knob=False),
    # panel counts on their own
    "1-panel": P(1), "3-panel": P(3), "4-panel": P(4), "6-panel": P(6),
}
# panels + design together (what the order editor looks up first: '4 Panel' + 'Shaker' -> 4-panel-shaker)
for n in range(1, 7):
    SPECS[f"{n}-panel-shaker"] = P(n, profile="flat")
    SPECS[f"{n}-panel-smooth"] = P(n)
    SPECS[f"{n}-panel-arch"] = P(n, arch=True)
    SPECS[f"{n}-panel-colonial"] = P(n, **TEX)
for n in (1, 2, 3):
    SPECS[f"{n}-panel-plank"] = P(n, plank=True, profile="flat")
    SPECS[f"{n}-panel-arch-plank"] = P(n, arch=True, plank=True, profile="flat")
SPECS["3-panel-shaker-equal"] = P(3, kind="equal", profile="flat")
SPECS["3-panel-shaker-craftsman"] = P(3, kind="craftsman", profile="flat")

# same design as a brochure photo -> use the real photo instead of a drawing
COPIES = {"shaker": "1-panel-shaker", "lincoln-park": "1-panel-shaker", "shaker-craftsman": "3-panel-shaker-craftsman",
          "shaker-equal": "3-panel-shaker-equal", "riverside": "5-panel-shaker", "arch": "2-panel-arch",
          "arch-plank": "2-panel-arch-plank", "smooth": "hc-primecoat", "flush": "hc-primecoat",
          "carrara": "2-panel", "carrera": "2-panel", "colonist": "6-panel", "2-panel-smooth": "2-panel",
          "5-panel-smooth": "5-panel", "6-panel-smooth": "6-panel", "6-panel-colonial": "6-panel"}

# styles that are drawn from a guess at the design (show these to the user)
GUESSES = ["cheyenne", "camden", "cremona", "roman", "rockport"]


def main():
    force = "--force" in sys.argv     # redraws the drawings, never the brochure photos
    brochure = {"1-panel-shaker", "2-panel", "2-panel-arch", "2-panel-arch-plank", "2-panel-shaker",
                "3-panel-shaker-craftsman", "3-panel-shaker-equal", "5-panel", "5-panel-shaker", "6-panel",
                "hc-primecoat"}
    os.makedirs(OUT, exist_ok=True)
    made = []
    for sl, spec in SPECS.items():
        p = os.path.join(OUT, sl + ".jpg")
        if sl in brochure or (os.path.exists(p) and not force):
            continue
        if sl in COPIES:
            shutil.copyfile(os.path.join(OUT, COPIES[sl] + ".jpg"), p)
        else:
            draw(spec).save(p, quality=90)
        made.append(sl)
    print(f"drew {len(made)}: {', '.join(made)}")


if __name__ == "__main__":
    main()
