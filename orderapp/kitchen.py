"""Kitchen / bath measuring: countertop runs (A, B, C...), sinks, appliances, base and upper cabinets.

A layout is one JSON document per room. From it the app draws:
  - a top-view diagram: every run lettered A, B, C... with the measured (wall) edge, depth, finished ends, sink /
    cooktop cut-outs and appliance gaps - so the shop knows exactly which side is which
  - an elevation of each run: base cabinets below the counter, upper cabinets above, with widths
  - the measure sheet: countertop pieces (a range or fridge gap splits a run into pieces), cut-outs, cabinet lists
How it's measured (industry practice, e.g. Home Depot / Floform and Slabwise guides): run by run along the BACK
(wall) edge, into each corner; depth from the wall to the front edge; note every finished end, appliance gap,
sink / cooktop center from the run's left end (standing facing the wall), and the backsplash.
"""
import json
import math
import re

from .db import dumps, loads, now, q, x

KITCHEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS kitchen_layouts (
    id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE, name TEXT,
    unit TEXT, floorplan_id INTEGER REFERENCES floorplans(id) ON DELETE SET NULL, room TEXT DEFAULT 'Kitchen',
    data TEXT NOT NULL DEFAULT '{}', pdf_path TEXT, created TEXT, updated TEXT
);
"""

SHAPES = {"straight": "Straight (one wall)", "l": "L-shape (2 walls)", "u": "U-shape (3 walls)",
          "galley": "Galley (2 facing walls)"}
SHAPE_RUNS = {"straight": ["B"], "l": ["A", "B"], "u": ["A", "B", "C"], "galley": ["A", "B"]}
RUN_WALL = {"A": "left wall", "B": "back wall", "C": "right wall"}
ENDS = ["Wall", "Finished end", "Appliance", "Corner"]
ITEM_TYPES = {   # counter items along a run: gap = no counter there (splits the top into pieces)
    "sink": {"label": "Sink", "gap": False}, "cooktop": {"label": "Cooktop (drop-in)", "gap": False},
    "range": {"label": "Range / stove", "gap": True}, "dishwasher": {"label": "Dishwasher", "gap": False},
    "fridge": {"label": "Refrigerator", "gap": True}, "opening": {"label": "Open space / gap", "gap": True},
}
BASE_TYPES = ["Base", "Sink base", "Drawer base", "Corner / lazy susan", "Blind corner", "Filler", "Vanity"]
UPPER_TYPES = ["Wall cabinet", "Corner wall cabinet", "Over fridge", "Over range / hood", "Microwave",
               "Window (no cabinet)", "Open / gap", "Filler"]
MATERIALS = ["Laminate", "Synthetic", "Thinscape", "Custom", "Quartz / stone"]
SPLASH = ['None', '4" backsplash', 'Full height', 'Coved (laminate)']

DEFAULT = {"shape": "l", "room": "Kitchen", "material": "Laminate", "color": "", "edge": "", "notes": "",
           "island": None, "peninsula": None,
           "runs": {k: {"length": "", "depth": "25-1/2", "left": "Wall", "right": "Wall", "splash": '4" backsplash',
                        "splash_h": "", "items": [], "bases": [], "uppers": []} for k in "ABC"}}


def init(con):
    con.executescript(KITCHEN_SCHEMA)


# ------------------------------------------------------------------ numbers
def num(v):
    """'25-1/2' / '25 1/2' / '25.5' / '25 1/2"' -> 25.5 ; '' -> None"""
    s = str(v or "").replace('"', "").replace("in", "").strip()
    if not s:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[- ]\s*(\d+)/(\d+)$", s)
    if m:
        return float(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.match(r"^(\d+)/(\d+)$", s)
    if m:
        return int(m.group(1)) / int(m.group(2))
    m = re.match(r"^(\d+)'\s*-?\s*(\d+(?:\.\d+)?)?$", s)
    if m:
        return int(m.group(1)) * 12 + float(m.group(2) or 0)
    try:
        return float(s)
    except ValueError:
        return None


def fin(v):
    """25.5 -> 25-1/2\" (to the nearest 1/16)."""
    if v is None:
        return ""
    neg = v < 0
    v = abs(v)
    whole = int(v)
    f = round((v - whole) * 16)
    if f == 16:
        whole, f = whole + 1, 0
    s = str(whole)
    if f:
        g = math.gcd(f, 16)
        s += f"-{f // g}/{16 // g}"
    return ("-" if neg else "") + s + '"'


# ------------------------------------------------------------------ data
def normalize(d):
    d = dict(DEFAULT, **{k: v for k, v in (d or {}).items() if v is not None or k in ("island", "peninsula")})
    if d.get("shape") not in SHAPES:
        d["shape"] = "l"
    runs = {}
    for k in "ABC":
        r = dict(DEFAULT["runs"][k], **((d.get("runs") or {}).get(k) or {}))
        for lst in ("items", "bases", "uppers"):
            r[lst] = [dict(i) for i in (r.get(lst) or []) if isinstance(i, dict)]
        runs[k] = r
    d["runs"] = runs
    for extra in ("island", "peninsula"):
        e = d.get(extra)
        if e:
            e = dict({"length": "", "depth": "25-1/2" if extra == "peninsula" else "36", "overhang": "",
                      "items": [], "bases": [], "uppers": [], "splash": "None",
                      "left": "Finished end" if extra == "island" else "Corner", "right": "Finished end"}, **e)
            d[extra] = e
    return d


def active_runs(d):
    keys = list(SHAPE_RUNS[d["shape"]])
    if d.get("peninsula") and d["shape"] != "galley":
        keys.append("P")
    if d.get("island") and d["shape"] != "galley":
        keys.append("I")
    return keys


def run(d, k):
    if k == "P":
        return d["peninsula"]
    if k == "I":
        return d["island"]
    return d["runs"][k]


# ------------------------------------------------------------------ geometry (inches, y down, top view)
def geometry(d):
    """{key: {p0, u, n, L, D, label}}. p0 = left end of the measured (back) edge standing facing the wall,
    u = along the run (left -> right), n = from the back edge toward the front."""
    g = {}
    shape = d["shape"]
    L = {k: num(run(d, k).get("length")) or 96.0 for k in active_runs(d)}
    D = {k: num(run(d, k).get("depth")) or (36.0 if k == "I" else 25.5) for k in active_runs(d)}
    if shape == "straight":
        g["B"] = ((0, 0), (1, 0), (0, 1))
        width = L["B"]
    elif shape == "galley":
        g["A"] = ((0, 0), (1, 0), (0, 1))
        aisle = 42.0
        yb = D["A"] + aisle + D["B"]
        g["B"] = ((max(L["A"], L["B"]), yb), (-1, 0), (0, -1))
        width = max(L["A"], L["B"])
    else:
        width = L["B"]
        g["B"] = ((0, 0), (1, 0), (0, 1))
        g["A"] = ((0, L["A"]), (0, -1), (1, 0))
        if shape == "u":
            g["C"] = ((width, 0), (0, 1), (-1, 0))
    out = {}
    for k, (p0, u, n) in g.items():
        out[k] = {"p0": p0, "u": u, "n": n, "L": L[k], "D": D[k]}
    if "P" in L and shape != "galley":   # peninsula: off the open end of the last side run
        if shape == "l":       # bottom of the left-wall run, reaching right
            p0, u, n = out["A"]["p0"], (1, 0), (0, -1)
        elif shape == "u":     # bottom of the right-wall run, reaching left
            c = out["C"]
            p0, u, n = (c["p0"][0], c["p0"][1] + c["L"]), (-1, 0), (0, -1)
        else:                  # straight: right end of the back wall, reaching down into the room
            p0, u, n = (width, 0), (0, 1), (-1, 0)
        out["P"] = {"p0": p0, "u": u, "n": n, "L": L["P"], "D": D["P"]}
    if "I" in L and shape != "galley":
        left = out["A"]["D"] if "A" in out else 0
        right = width - (out["C"]["D"] if "C" in out else 0)
        top = out["B"]["D"] + 42
        x0 = left + ((right - left) - L["I"]) / 2
        out["I"] = {"p0": (x0, top), "u": (1, 0), "n": (0, 1), "L": L["I"], "D": D["I"]}
    for k, r in out.items():
        r["label"] = k
    return out


def _pt(r, t, s):
    """Point at distance t along the run and s in from the back edge."""
    return (r["p0"][0] + r["u"][0] * t + r["n"][0] * s, r["p0"][1] + r["u"][1] * t + r["n"][1] * s)


def _corners(r):
    return [_pt(r, 0, 0), _pt(r, r["L"], 0), _pt(r, r["L"], r["D"]), _pt(r, 0, r["D"])]


# ------------------------------------------------------------------ countertop pieces
def pieces(d):
    """Countertop pieces: a range / fridge / opening splits a run. [{name, run, from, length, depth, left, right,
    splash, cutouts:[...]}]"""
    out = []
    for k in active_runs(d):
        r = run(d, k)
        L = num(r.get("length"))
        if not L:
            continue
        D = num(r.get("depth")) or 25.5
        gaps = sorted([(num(i.get("from")) or 0, num(i.get("width")) or 0, i) for i in r.get("items", [])
                       if ITEM_TYPES.get(i.get("type"), {}).get("gap")], key=lambda g: g[0])
        cuts = [i for i in r.get("items", []) if i.get("type") in ("sink", "cooktop")]
        start, seg, n = 0.0, [], 0
        left = r.get("left") or ("Finished end" if k in ("I", "P") else "Wall")
        for g0, gw, gi in gaps + [(L, 0, None)]:
            if g0 - start > 0.5:
                seg.append((start, g0 - start, left, "Appliance" if gi else (r.get("right") or "Wall")))
            start = g0 + gw
            left = "Appliance"
        for s0, sl, le, ri in seg:
            n += 1
            name = k if len(seg) == 1 else f"{k}{n}"
            pc = []
            for c in cuts:
                cf = num(c.get("from")) or 0
                cw = num(c.get("width")) or 0
                if s0 - 0.1 <= cf and cf + cw <= s0 + sl + 0.1:
                    pc.append({"type": c["type"], "center_from_left": round(cf - s0 + cw / 2, 3), "width": cw,
                               "size": c.get("size", ""), "notes": c.get("notes", "")})
            out.append({"name": name, "run": k, "from": s0, "length": sl, "depth": D, "left": le, "right": ri,
                        "splash": r.get("splash", ""), "splash_h": r.get("splash_h", ""), "cutouts": pc,
                        "overhang": r.get("overhang", "")})
    return out


def sqft(d):
    return round(sum(p["length"] * p["depth"] for p in pieces(d)) / 144, 1)


# ------------------------------------------------------------------ drawing primitives
def top_view(d):
    """Primitives for the top view: [(kind, ...)] in inches. kinds: poly, line, dim, text, circle, label."""
    geo = geometry(d)
    prims = []
    for k, r in geo.items():
        rr = run(d, k)
        pts = _corners(r)
        prims.append(("poly", pts, "counter"))
        # measured (back) edge: heavy line; island / peninsula: no wall
        if k not in ("I",):
            prims.append(("line", pts[0], pts[1], "wall"))
        # dimension along the back edge, outside the wall
        off = -7
        a, b = _pt(r, 0, off), _pt(r, r["L"], off)
        prims.append(("dim", a, b, fin(r["L"]) if num(rr.get("length")) else "length ?"))
        # depth dimension at the left end
        prims.append(("dim", _pt(r, -6 if k != "I" else -6, 0), _pt(r, -6, r["D"]), fin(r["D"])))
        # ends
        for end, t in (("left", 0), ("right", r["L"])):
            kind = rr.get(end) or ("Finished end" if k in ("I", "P") else "Wall")
            if kind == "Finished end":
                prims.append(("line", _pt(r, t, 0), _pt(r, t, r["D"]), "finished"))
            elif kind == "Appliance":
                prims.append(("text", _pt(r, t + (5 if t == 0 else -5), r["D"] / 2), "APP", "tiny"))
        # front edge marker
        prims.append(("line", pts[3], pts[2], "front"))
        # items
        for it in rr.get("items", []):
            t0, w = num(it.get("from")), num(it.get("width"))
            if t0 is None or not w:
                continue
            typ = it.get("type")
            if typ == "sink":
                c = _pt(r, t0 + w / 2, r["D"] * 0.45)
                bowls = 2 if "double" in (it.get("size", "") + it.get("notes", "")).lower() else 1
                prims.append(("sink", _pt(r, t0, r["D"] * 0.18), _pt(r, t0 + w, r["D"] * 0.75), bowls))
                prims.append(("text", _pt(r, t0 + w / 2, r["D"] * 0.95), f"sink ctr {fin(t0 + w / 2)}", "small"))
                del c
            elif typ == "cooktop":
                prims.append(("cooktop", _pt(r, t0, r["D"] * 0.15), _pt(r, t0 + w, r["D"] * 0.85)))
            elif typ in ("range", "fridge", "opening"):
                prims.append(("gap", _corners({**r, "p0": _pt(r, t0, 0), "L": w}),
                              {"range": "RANGE", "fridge": "FRIDGE", "opening": "OPEN"}[typ] + f" {fin(w)}"))
            elif typ == "dishwasher":
                prims.append(("under", _pt(r, t0, r["D"] * 0.1), _pt(r, t0 + w, r["D"] * 0.9), "DW"))
        # the letter goes on the biggest clear stretch of counter (not on a sink, range or fridge)
        busy = sorted([(num(i.get("from")) or 0, (num(i.get("from")) or 0) + (num(i.get("width")) or 0))
                       for i in rr.get("items", []) if num(i.get("width"))])
        free, cur = [], 0.0
        for a_, b_ in busy:
            if a_ > cur:
                free.append((cur, a_))
            cur = max(cur, b_)
        if cur < r["L"]:
            free.append((cur, r["L"]))
        a_, b_ = max(free, key=lambda f: f[1] - f[0]) if free else (0, r["L"])
        prims.append(("label", _pt(r, (a_ + b_) / 2, r["D"] / 2), k))
    return prims


def bounds(prims):
    xs, ys = [], []
    for p in prims:
        pts = []
        if p[0] in ("poly", "gap"):
            pts = p[1]
        elif p[0] in ("line", "dim", "sink", "cooktop", "under"):
            pts = [p[1], p[2]]
        elif p[0] in ("text", "label"):
            pts = [p[1]]
        for x_, y_ in pts:
            xs.append(x_)
            ys.append(y_)
    if not xs:
        return 0, 0, 100, 100
    return min(xs) - 14, min(ys) - 14, max(xs) + 14, max(ys) + 14


class _Canvas:
    """Draw primitives to SVG or to a PDF page (same code, two back ends)."""

    def __init__(self, box, prims, mode, page=None):
        self.mode, self.page, self.parts = mode, page, []
        x0, y0, x1, y1 = bounds(prims)
        bw, bh = box[2] - box[0], box[3] - box[1]
        self.k = min(bw / max(x1 - x0, 1), bh / max(y1 - y0, 1))
        self.ox = box[0] + (bw - (x1 - x0) * self.k) / 2 - x0 * self.k
        self.oy = box[1] + (bh - (y1 - y0) * self.k) / 2 - y0 * self.k

    def P(self, p):
        return (self.ox + p[0] * self.k, self.oy + p[1] * self.k)

    # --- low level
    def poly(self, pts, stroke="#333", fill="none", w=1, dash=None):
        q_ = [self.P(p) for p in pts]
        if self.mode == "svg":
            self.parts.append(f'<polygon points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in q_)}" fill="{fill}" '
                              f'stroke="{stroke}" stroke-width="{w}"' + (f' stroke-dasharray="{dash}"' if dash else "")
                              + "/>")
        else:
            import fitz
            sh = self.page.new_shape()
            sh.draw_polyline(q_ + [q_[0]])
            sh.finish(color=_rgb(stroke), fill=None if fill == "none" else _rgb(fill), width=w,
                      dashes="[3 2] 0" if dash else None)
            sh.commit()
            del fitz

    def line(self, a, b, stroke="#333", w=1, dash=None):
        a, b = self.P(a), self.P(b)
        if self.mode == "svg":
            self.parts.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="{stroke}" '
                              f'stroke-width="{w}"' + (f' stroke-dasharray="{dash}"' if dash else "") + "/>")
        else:
            self.page.draw_line(a, b, color=_rgb(stroke), width=w, dashes="[3 2] 0" if dash else None)

    def circle(self, c, r_in, stroke="#333", fill="none", w=1, raw_r=None):
        cx, cy = self.P(c)
        rr = raw_r if raw_r else r_in * self.k
        if self.mode == "svg":
            self.parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rr:.1f}" fill="{fill}" stroke="{stroke}" '
                              f'stroke-width="{w}"/>')
        else:
            self.page.draw_circle((cx, cy), rr, color=_rgb(stroke), fill=None if fill == "none" else _rgb(fill),
                                  width=w)

    def text(self, c, s, size=9, bold=False, color="#111", anchor="middle", rot=0):
        cx, cy = self.P(c)
        if self.mode == "svg":
            tr = f' transform="rotate({rot} {cx:.1f} {cy:.1f})"' if rot else ""
            self.parts.append(f'<text x="{cx:.1f}" y="{cy + size * 0.35:.1f}" font-size="{size}" text-anchor="{anchor}"'
                              f' font-family="Helvetica,Arial,sans-serif" fill="{color}"'
                              + (' font-weight="bold"' if bold else "") + f"{tr}>{_esc(s)}</text>")
        else:
            import fitz
            font = "hebo" if bold else "helv"
            tw = fitz.get_text_length(s, fontname=font, fontsize=size)
            if rot:
                self.page.insert_text((cx - size * 0.35, cy + tw / 2), s, fontsize=size, fontname=font,
                                      color=_rgb(color), rotate=90)
            else:
                self.page.insert_text((cx - (tw / 2 if anchor == "middle" else 0), cy + size * 0.35), s,
                                      fontsize=size, fontname=font, color=_rgb(color))

    # --- primitives
    def draw(self, prims):
        for p in prims:
            kind = p[0]
            if kind == "poly":
                self.poly(p[1], stroke="#555", fill="#f3eee4", w=0.8)
            elif kind == "line":
                style = p[3]
                if style == "wall":
                    self.line(p[1], p[2], "#111", 3.2)
                elif style == "finished":
                    self.line(p[1], p[2], "#c2410c", 2.6)
                elif style == "front":
                    self.line(p[1], p[2], "#555", 0.8)
            elif kind == "dim":
                a, b, label = p[1], p[2], p[3]
                self.line(a, b, "#1d4ed8", 0.8)
                for e in (a, b):
                    self.circle(e, 0, "#1d4ed8", "#1d4ed8", 0.5, raw_r=1.6)
                mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                vertical = abs(a[0] - b[0]) < abs(a[1] - b[1])
                self.text((mx, my), label, 8.5, True, "#1d4ed8", rot=-90 if vertical and self.mode == "svg" else
                          (90 if vertical else 0))
            elif kind == "sink":
                a, b, bowls = p[1], p[2], p[3]
                xs, ys = sorted([a[0], b[0]]), sorted([a[1], b[1]])
                self.poly([(xs[0], ys[0]), (xs[1], ys[0]), (xs[1], ys[1]), (xs[0], ys[1])], "#0369a1", "#e0f2fe", 1)
                if bowls == 2:
                    if xs[1] - xs[0] > ys[1] - ys[0]:
                        mid = (xs[0] + xs[1]) / 2
                        self.line((mid, ys[0]), (mid, ys[1]), "#0369a1", 0.8)
                    else:
                        mid = (ys[0] + ys[1]) / 2
                        self.line((xs[0], mid), (xs[1], mid), "#0369a1", 0.8)
                self.circle(((xs[0] + xs[1]) / 2, (ys[0] + ys[1]) / 2), 1.2, "#0369a1", "#0369a1", 0.5)
            elif kind == "cooktop":
                a, b = p[1], p[2]
                xs, ys = sorted([a[0], b[0]]), sorted([a[1], b[1]])
                self.poly([(xs[0], ys[0]), (xs[1], ys[0]), (xs[1], ys[1]), (xs[0], ys[1])], "#7f1d1d", "#fee2e2", 1)
                for fx in (0.3, 0.7):
                    for fy in (0.3, 0.7):
                        self.circle((xs[0] + (xs[1] - xs[0]) * fx, ys[0] + (ys[1] - ys[0]) * fy),
                                    min(xs[1] - xs[0], ys[1] - ys[0]) * 0.13, "#7f1d1d", "none", 0.8)
            elif kind == "gap":
                self.poly(p[1], "#9ca3af", "#ffffff", 1, dash="4 3")
                cx = sum(t[0] for t in p[1]) / 4
                cy = sum(t[1] for t in p[1]) / 4
                self.text((cx, cy), p[2], 7.5, True, "#6b7280")
            elif kind == "under":
                a, b = p[1], p[2]
                xs, ys = sorted([a[0], b[0]]), sorted([a[1], b[1]])
                self.poly([(xs[0], ys[0]), (xs[1], ys[0]), (xs[1], ys[1]), (xs[0], ys[1])], "#6b7280", "none", 0.8,
                          dash="3 2")
                self.text(((xs[0] + xs[1]) / 2, (ys[0] + ys[1]) / 2), p[3], 8, True, "#6b7280")
            elif kind == "text":
                self.text(p[1], p[2], 7 if p[3] == "tiny" else 8, False, "#374151")
            elif kind == "label":
                self.circle(p[1], 0, "#c2410c", "#ffffff", 1.6, raw_r=11)
                self.text(p[1], p[2], 13, True, "#c2410c")

    def svg(self, w, h):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" '
                f'aria-label="Countertop layout">' + "".join(self.parts) + "</svg>")


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _rgb(c):
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def top_svg(d, w=640, h=420):
    d = normalize(d)
    prims = top_view(d)
    cv = _Canvas((10, 10, w - 10, h - 10), prims, "svg")
    cv.draw(prims)
    return cv.svg(w, h)


# ------------------------------------------------------------------ elevations (front view of each run)
def elevation_prims(d, k):
    """Front view of one run: base cabinets (0..34.5), counter (34.5..36), uppers (54..wall height)."""
    r = run(d, k)
    L = num(r.get("length")) or 96.0
    prims = [("poly", [(0, 0), (L, 0), (L, 1.5), (0, 1.5)], "counter")]
    x0 = 0.0
    for b in r.get("bases", []):
        w = num(b.get("width"))
        if not w:
            continue
        prims.append(("box", (x0, 1.5), (x0 + w, 36), b.get("type") or "Base", fin(w)))
        x0 += w
    x0 = 0.0
    for u in r.get("uppers", []):
        w = num(u.get("width"))
        if not w:
            continue
        h = num(u.get("height")) or 30
        at = num(u.get("from"))
        if at is not None:
            x0 = at
        prims.append(("ubox", (x0, -18 - h), (x0 + w, -18), u.get("type") or "Wall cabinet", fin(w), fin(h)))
        x0 += w
    for it in r.get("items", []):
        t0, w = num(it.get("from")), num(it.get("width"))
        if t0 is None or not w:
            continue
        if it.get("type") in ("range", "fridge", "dishwasher", "opening"):
            prims.append(("appl", (t0, 1.5 if it["type"] == "dishwasher" else -34.5 if it["type"] == "fridge" else 0),
                          (t0 + w, 36), ITEM_TYPES[it["type"]]["label"], fin(w)))
        elif it.get("type") == "sink":
            prims.append(("mark", (t0 + w / 2, -1), f"sink ctr {fin(t0 + w / 2)}"))
    prims.append(("dim", (0, 40), (L, 40), fin(L)))
    return prims, L


def draw_elevation(page_or_none, d, k, box, mode="pdf"):
    prims, L = elevation_prims(d, k)
    x0, y0, x1, y1 = box
    top = -60.0
    bot = 44.0
    kx = (x1 - x0) / max(L + 4, 1)
    ky = (y1 - y0) / (bot - top)
    kk = min(kx, ky)
    ox = x0 + ((x1 - x0) - (L + 4) * kk) / 2 + 2 * kk
    oy = y0 - top * kk

    def P(p):
        return (ox + p[0] * kk, oy + p[1] * kk)
    out = []
    import fitz
    pg = page_or_none

    def rect(a, b, stroke, fill, w=0.8, dash=None):
        if mode == "svg":
            (ax, ay), (bx, by) = P(a), P(b)
            out.append(f'<rect x="{min(ax, bx):.1f}" y="{min(ay, by):.1f}" width="{abs(bx - ax):.1f}" '
                       f'height="{abs(by - ay):.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{w}"'
                       + (f' stroke-dasharray="{dash}"' if dash else "") + "/>")
        else:
            pg.draw_rect(fitz.Rect(P(a), P(b)), color=_rgb(stroke), fill=None if fill == "none" else _rgb(fill),
                         width=w, dashes="[3 2] 0" if dash else None)

    def text(c, s, size=7, bold=False, color="#111"):
        cx, cy = P(c)
        if mode == "svg":
            out.append(f'<text x="{cx:.1f}" y="{cy:.1f}" font-size="{size}" text-anchor="middle" font-family='
                       f'"Helvetica,Arial,sans-serif" fill="{color}"' + (' font-weight="bold"' if bold else "")
                       + f">{_esc(s)}</text>")
        else:
            font = "hebo" if bold else "helv"
            tw = fitz.get_text_length(s, fontname=font, fontsize=size)
            pg.insert_text((cx - tw / 2, cy), s, fontsize=size, fontname=font, color=_rgb(color))
    for p in prims:
        if p[0] == "poly":
            rect(p[1][0], p[1][2], "#555", "#d6c7a8", 0.8)
        elif p[0] == "box":
            rect(p[1], p[2], "#444", "#ffffff", 0.8)
            text(((p[1][0] + p[2][0]) / 2, 17), p[4], 7, True)
            text(((p[1][0] + p[2][0]) / 2, 25), p[3][:14], 5.8, False, "#555")
        elif p[0] == "ubox":
            rect(p[1], p[2], "#444", "#eef2ff", 0.8)
            text(((p[1][0] + p[2][0]) / 2, (p[1][1] + p[2][1]) / 2), p[4] + " x " + p[5], 6.5, True)
            text(((p[1][0] + p[2][0]) / 2, (p[1][1] + p[2][1]) / 2 + 7), p[3][:16], 5.8, False, "#555")
        elif p[0] == "appl":
            rect(p[1], p[2], "#9ca3af", "none", 0.8, dash="4 3")
            text(((p[1][0] + p[2][0]) / 2, (p[1][1] + p[2][1]) / 2), p[3], 6.5, True, "#6b7280")
            text(((p[1][0] + p[2][0]) / 2, (p[1][1] + p[2][1]) / 2 + 7), p[4], 6.5, False, "#6b7280")
        elif p[0] == "mark":
            text(p[1], p[2], 6.2, False, "#0369a1")
        elif p[0] == "dim":
            (ax, ay), (bx, by) = P(p[1]), P(p[2])
            if mode == "svg":
                out.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="#1d4ed8"/>')
            else:
                pg.draw_line((ax, ay), (bx, by), color=_rgb("#1d4ed8"), width=0.8)
            text(((p[1][0] + p[2][0]) / 2, 43.5), p[3], 8, True, "#1d4ed8")
    return "".join(out)


# ------------------------------------------------------------------ storage
def get(lid):
    r = q("SELECT * FROM kitchen_layouts WHERE id=?", (lid,), one=True)
    if not r:
        return None
    out = dict(r)
    out["data"] = normalize(loads(r["data"], {}))
    return out


def save(lid, data, name=None, unit=None):
    data = normalize(data)
    x("UPDATE kitchen_layouts SET data=?, name=coalesce(?, name), unit=coalesce(?, unit), room=?, updated=? "
      "WHERE id=?", (dumps(data), name, unit, data.get("room") or "Kitchen", now(), lid))
    return data


def create(customer_id, name="Kitchen", unit="", floorplan_id=None, shape="l", room="Kitchen"):
    data = normalize({"shape": shape, "room": room})
    return x("""INSERT INTO kitchen_layouts(customer_id, name, unit, floorplan_id, room, data, created, updated)
                VALUES (?,?,?,?,?,?,?,?)""", (customer_id, name, unit, floorplan_id, room, dumps(data), now(), now()))


def for_customer(cid):
    return q("SELECT * FROM kitchen_layouts WHERE customer_id=? ORDER BY updated DESC", (cid,))


def as_json(d):
    return json.dumps(d)


# ------------------------------------------------------------------ PDF (company form look)
NAVY = (0.0, 0.0, 0.4)
INK = (0.1, 0.1, 0.1)
MUTED = (0.38, 0.38, 0.38)
LINE = (0.55, 0.55, 0.55)
L_, R_ = 36, 576


def _page(doc, title):
    import fitz
    from .manager_forms import COMPANY
    pg = doc.new_page(width=612, height=792)
    src = fitz.open(COMPANY)
    pg.show_pdf_page(fitz.Rect(36, 18, 270, 104), src, 0, clip=fitz.Rect(68, 22, 337, 121))
    pg.draw_rect(fitz.Rect(300, 22, R_, 46), color=None, fill=(0, 0, 0))
    size = 14
    while fitz.get_text_length(title, fontname="tiro", fontsize=size) > R_ - 310 and size > 9:
        size -= 0.5
    tw = fitz.get_text_length(title, fontname="tiro", fontsize=size)
    pg.insert_text((300 + (R_ - 300 - tw) / 2, 39), title, fontsize=size, fontname="tiro", color=(1, 1, 1))
    pg.insert_text((L_, 772), "Apartment Interior Supply  ·  5325 S. Kyrene Rd., Suite 103, Tempe, AZ 85283  ·  "
                   "Phone (480) 964-7600  ·  Fax (480) 964-7610", fontsize=7, fontname="helv", color=MUTED)
    return pg


def _t(pg, x, y, s, size=8, bold=False, color=INK, right=False, center=False):
    import fitz
    font = "hebo" if bold else "helv"
    tw = fitz.get_text_length(str(s), fontname=font, fontsize=size)
    if right:
        x -= tw
    elif center:
        x -= tw / 2
    pg.insert_text((x, y), str(s), fontsize=size, fontname=font, color=color)
    return tw


def _field(pg, x, y, label, value, w):
    _t(pg, x, y, label, 7, True, MUTED)
    pg.draw_line((x, y + 12), (x + w, y + 12), color=LINE, width=0.6)
    _t(pg, x + 2, y + 10, value or "", 9)


def _bar(pg, y, title):
    import fitz
    pg.draw_rect(fitz.Rect(L_, y, R_, y + 14), color=None, fill=NAVY)
    _t(pg, L_ + 6, y + 10, title, 9, True, (1, 1, 1))
    return y + 14


def _table(pg, y, cols, rows, size=7.5, row_h=13, blank_rows=0):
    """cols = [(title, width)], rows = [[cells]]. Returns the y under the table."""
    x = L_
    import fitz
    pg.draw_rect(fitz.Rect(L_, y, R_, y + row_h), color=None, fill=(0.92, 0.92, 0.95))
    for title, w in cols:
        _t(pg, x + 3, y + row_h - 4, title, 6.8, True)
        x += w
    y += row_h
    for cells in rows + [[""] * len(cols)] * blank_rows:
        x = L_
        for (title, w), c in zip(cols, cells):
            s = str(c or "")
            sz = size
            while fitz.get_text_length(s, fontname="helv", fontsize=sz) > w - 5 and sz > 5:
                sz -= 0.25
            _t(pg, x + 3, y + row_h - 4, s, sz)
            x += w
        pg.draw_line((L_, y + row_h), (R_, y + row_h), color=LINE, width=0.4)
        y += row_h
    x = L_
    for title, w in cols[:-1]:
        x += w
        pg.draw_line((x, y - row_h * (len(rows) + blank_rows) - row_h), (x, y), color=LINE, width=0.4)
    pg.draw_rect(fitz.Rect(L_, y - row_h * (len(rows) + blank_rows + 1), R_, y), color=INK, width=0.8)
    return y


def _legend(pg, x, y):
    import fitz
    items = [((0.07, 0.07, 0.07), 3.2, None, "Wall = the measured (back) edge"),
             ((0.76, 0.25, 0.05), 2.6, None, "Finished end"),
             ((0.6, 0.6, 0.6), 1, "[3 2] 0", "Appliance / gap (no top)"),
             ((0.11, 0.31, 0.85), 0.8, None, "Dimensions")]
    for color, w, dash, lab in items:
        pg.draw_line((x, y - 3), (x + 18, y - 3), color=color, width=w, dashes=dash)
        x += 22 + _t(pg, x + 22, y, lab, 7) + 12
    del fitz


def make_pdf(layout, customer=None, out_path=None):
    """The filled measure sheet: page 1 top view + countertop pieces + cut-outs, page 2 cabinet elevations."""
    import fitz
    d = normalize(layout["data"] if "data" in layout else layout)
    doc = fitz.open()
    pg = _page(doc, f"{d.get('room') or 'Kitchen'} Countertop & Cabinet Measure Sheet")
    c = customer or {}
    _field(pg, 300, 58, "PROPERTY", c.get("name", ""), 170)
    _field(pg, 480, 58, "UNIT / FLOORPLAN", layout.get("unit", "") if isinstance(layout, dict) else "", 96)
    _field(pg, 300, 82, "ROOM / NAME", f"{d.get('room', '')} - {layout.get('name', '')}".strip(" -")
           if isinstance(layout, dict) else d.get("room", ""), 170)
    _field(pg, 480, 82, "DATE", (layout.get("updated") or now())[:10] if isinstance(layout, dict) else "", 96)
    y = 112
    _field(pg, L_, y, "COUNTERTOP", d.get("material", ""), 120)
    _field(pg, L_ + 132, y, "COLOR / PATTERN", d.get("color", ""), 160)
    _field(pg, L_ + 304, y, "EDGE", d.get("edge", ""), 100)
    _field(pg, L_ + 416, y, "LAYOUT", SHAPES.get(d["shape"], ""), 124)
    y = _bar(pg, 140, "TOP VIEW - each run is lettered; measure along the wall (back) edge, into the corners")
    box = (L_ + 6, y + 6, R_ - 6, y + 300)
    pg.draw_rect(fitz.Rect(L_, y, R_, y + 306), color=INK, width=0.8)
    prims = top_view(d)
    cv = _Canvas(box, prims, "pdf", pg)
    cv.draw(prims)
    y += 306
    _legend(pg, L_ + 4, y + 12)
    y = _bar(pg, y + 20, "COUNTERTOP PIECES (a range or fridge splits a run into pieces)")
    rows = []
    for p in pieces(d):
        cut = "; ".join(f"{cc['type']} center {fin(cc['center_from_left'])} from left"
                        + (f" ({cc['size']})" if cc.get("size") else "") for cc in p["cutouts"])
        rows.append([p["name"], fin(p["length"]), fin(p["depth"]), p["left"], p["right"],
                     (p["splash"] or "") + (f" {p['splash_h']}" if p.get("splash_h") else ""), cut])
    y = _table(pg, y, [("Piece", 34), ("Length (wall edge)", 74), ("Depth", 50), ("Left end", 64),
                       ("Right end", 64), ("Backsplash", 80), ("Sink / cooktop cut-outs", 174)], rows,
               blank_rows=max(0, 6 - len(rows)))
    _t(pg, L_, y + 12, f"Total about {sqft(d)} sq ft of top.  Left / right = standing at the counter facing the wall. "
       f"Measured to the nearest 1/16\".", 7.5, False, MUTED)
    if d.get("notes"):
        pg.insert_textbox(fitz.Rect(L_, y + 18, R_, y + 60), "Notes: " + d["notes"], fontsize=8, fontname="helv")
    # page 2: cabinets
    pg2 = _page(doc, "Cabinets - base & upper, by run")
    y = 112
    for k in active_runs(d):
        r = run(d, k)
        if y > 540:
            pg2 = _page(doc, "Cabinets - base & upper, by run (cont.)")
            y = 112
        label = {"A": "Run A - left wall", "B": "Run B - back wall", "C": "Run C - right wall", "P": "Peninsula P",
                 "I": "Island I"}.get(k, k)
        if d["shape"] == "galley":
            label = {"A": "Run A - first wall", "B": "Run B - facing wall"}.get(k, label)
        y = _bar(pg2, y, f"{label}  ·  {fin(num(r.get('length')))} long")
        draw_elevation(pg2, d, k, (L_ + 6, y + 4, R_ - 6, y + 138))
        y += 142
        bases = [[str(i + 1), b.get("type", ""), fin(num(b.get("width"))), b.get("notes", "")]
                 for i, b in enumerate(r.get("bases", []))]
        ups = [[str(i + 1), u.get("type", ""), fin(num(u.get("width"))), fin(num(u.get("height"))), u.get("notes", "")]
               for i, u in enumerate(r.get("uppers", []))]
        y0 = y
        _mini_table(pg2, L_, y0, [("#", 16), ("Base cabinet (left to right)", 120), ("Width", 50), ("Notes", 80)], bases)
        y1 = _mini_table(pg2, L_ + 274, y0, [("#", 16), ("Upper (left to right)", 100), ("Width", 44), ("Height", 44),
                                              ("Notes", 62)], ups)
        y = max(y1, y0 + 13 * (max(len(bases), len(ups), 1) + 1)) + 14
    if out_path:
        doc.save(out_path, garbage=3, deflate=True)
    data = doc.tobytes()
    doc.close()
    return data


def _mini_table(pg, x0, y, cols, rows, row_h=12):
    import fitz
    x = x0
    w_all = sum(w for _, w in cols)
    pg.draw_rect(fitz.Rect(x0, y, x0 + w_all, y + row_h), color=None, fill=(0.92, 0.92, 0.95))
    for title, w in cols:
        _t(pg, x + 3, y + row_h - 3.5, title, 6.5, True)
        x += w
    y += row_h
    for cells in rows or [["", "(none listed)"] + [""] * (len(cols) - 2)]:
        x = x0
        for (title, w), c in zip(cols, cells):
            _t(pg, x + 3, y + row_h - 3.5, str(c or "")[:40], 7)
            x += w
        pg.draw_line((x0, y + row_h), (x0 + w_all, y + row_h), color=LINE, width=0.4)
        y += row_h
    return y


def blank_pdf():
    """Printable measuring sheet for the field: how to measure, sketch grid with the run letters, blank tables."""
    import fitz
    doc = fitz.open()
    pg = _page(doc, "Countertop & Cabinet Measure Sheet")
    for i, (lab, w) in enumerate((("PROPERTY", 170), ("UNIT / FLOORPLAN", 96))):
        _field(pg, 300 + i * 180, 58, lab, "", w)
    _field(pg, 300, 82, "ROOM", "", 170)
    _field(pg, 480, 82, "DATE", "", 96)
    y = _bar(pg, 112, "HOW TO MEASURE")
    steps = ["1  Letter the runs: A = left wall, B = back wall, C = right wall, P = peninsula, I = island (see sketches).",
             "2  Measure each run along the WALL (back) edge, at counter height, into each corner, to the nearest 1/16\".",
             "3  Depth: wall to the front edge of the top (most kitchen tops 25-1/2\", vanity tops 22-1/2\").",
             "4  Each end: Wall, Finished end (open side - add 3/4\" past the cabinet), Appliance, or Corner.",
             "5  Sink & cooktop: from the run's LEFT end (facing the wall) to the CENTER of the sink / cooktop.",
             "6  Range, fridge or open space: where it starts from the left end and how wide - the top stops there.",
             "7  Backsplash: none, 4\", full height (give the height) or coved. Check the corner is square (3-4-5 = 60\").",
             "8  Cabinets: list base cabinets and uppers left to right on each run with their widths (uppers: height too)."]
    for s_ in steps:
        y += 11
        _t(pg, L_ + 6, y, s_, 7.6)
    y += 8
    # little lettered sketches of the usual layouts
    sk = [("straight", "Straight"), ("l", "L-shape"), ("u", "U-shape"), ("galley", "Galley")]
    w_each = (R_ - L_) / 4
    for i, (shape, lab) in enumerate(sk):
        dd = normalize({"shape": shape, "runs": {"A": {"length": "84"}, "B": {"length": "120"}, "C": {"length": "84"}}})
        prims = [p for p in top_view(dd) if p[0] in ("poly", "line", "label")]
        cv = _Canvas((L_ + i * w_each + 8, y + 4, L_ + (i + 1) * w_each - 8, y + 92), prims, "pdf", pg)
        cv.draw(prims)
        _t(pg, L_ + i * w_each + w_each / 2, y + 104, lab, 8, True, center=True)
    y += 112
    y = _bar(pg, y, "SKETCH THE ROOM (mark A, B, C..., the sink, range, fridge, dishwasher and windows)")
    pg.draw_rect(fitz.Rect(L_, y, R_, y + 200), color=INK, width=0.8)
    for gx in range(int(L_) + 18, int(R_), 18):
        pg.draw_line((gx, y), (gx, y + 200), color=(0.88, 0.88, 0.9), width=0.3)
    for gy in range(int(y) + 18, int(y + 200), 18):
        pg.draw_line((L_, gy), (R_, gy), color=(0.88, 0.88, 0.9), width=0.3)
    y += 206
    y = _bar(pg, y, "COUNTERTOP RUNS")
    _table(pg, y, [("Run", 30), ("Length (wall edge)", 74), ("Depth", 46), ("Left end", 62), ("Right end", 62),
                   ("Backsplash", 70), ("Sink / cooktop center from left", 120), ("Range / fridge gap", 76)],
           [[k, "", "", "", "", "", "", ""] for k in ("A", "B", "C", "P", "I")])
    pg2 = _page(doc, "Cabinets - base & upper, by run")
    y = 112
    for k in ("A", "B", "C"):
        y = _bar(pg2, y, f"Run {k}: list left to right (facing the wall)")
        y0 = y
        _mini_table(pg2, L_, y0, [("#", 16), ("Base cabinet type", 120), ("Width", 50), ("Notes", 80)],
                    [[str(i), "", "", ""] for i in range(1, 9)])
        y = _mini_table(pg2, L_ + 274, y0, [("#", 16), ("Upper / window / hood", 100), ("Width", 44),
                                             ("Height", 44), ("Notes", 62)], [[str(i), "", "", "", ""]
                                                                              for i in range(1, 9)]) + 14
    _t(pg2, L_, y + 4, "Base types: base, sink base, drawer base, corner / lazy susan, blind corner, filler, vanity.  "
       "Uppers: wall, corner, over fridge, over range / hood, microwave, window.", 7.5, False, MUTED)
    out = doc.tobytes()
    doc.close()
    return out
