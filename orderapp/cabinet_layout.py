"""Cabinet & countertop layout, box by box (v2 of the kitchen measure sheet).

How Taylor draws it on paper: each wall is a row of LOWER boxes (base cabinets, sink base, drawer base) with the
spaces for the dishwasher / stove / fridge left open, and a row of UPPER boxes above (wall cabinets - the one over
the stove is shorter, hanging from the same top line). Every box gets its width, height and depth.

Here every box is entered one by one (left to right, standing facing the wall) and the app draws the elevation with
dimension lines like the paper sketches: widths under the lowers and over the uppers, heights at the ends and on any
box that's a different height. Appliance spaces are outlined red, sink bases blue (same as the sketches).
The countertop is worked out from the lowers: one piece per stretch of counter (a stove, fridge or open space
splits it), depth = the boxes' depth + the front overhang, with the backsplash rise.

Data (kitchen_layouts.data with "v": 2):
  {"v": 2, "room", "notes", "top": {material, color, overhang, splash, edge}, "cab": {material, color},
   "walls": [{"name", "lowers": [box...], "uppers": [box...]}]}
  box = {"type", "w", "h", "d", "doors", "drawers": "Yes"/"No", "drawer_n", "drawer_h", "hinge", "label", "notes"}
"""
import copy

from .kitchen import fin, num

V = 2
LOWER_TYPES = {   # type: (short label, default w, h, d, kind)  kind: cab / sink / space / tall / filler
    "Base cabinet": ("BASE", 18, 34.5, 24, "cab"),
    "Sink base": ("SINK BASE", 36, 34.5, 24, "sink"),
    "Drawer base": ("DRAWER BASE", 18, 34.5, 24, "cab"),
    "Corner base": ("CORNER BASE", 36, 34.5, 24, "cab"),
    "Tall / pantry": ("PANTRY", 18, 84, 24, "tall"),
    "Filler": ("FILLER", 3, 34.5, 24, "filler"),
    "Dishwasher space": ("DISHWASHER", 24, 34.5, 24, "space-counter"),
    "Stove / range space": ("STOVE", 30, 36, 25, "space"),
    "Fridge space": ("FRIDGE", 36, 70, 30, "space"),
    "Open space": ("OPEN", 24, 34.5, 24, "space"),
}
UPPER_TYPES = {
    "Wall cabinet": ("WALL", 30, 30, 12, "cab"),
    "Over-stove cabinet": ("OVER STOVE", 30, 18, 12, "cab"),
    "Over-fridge cabinet": ("OVER FRIDGE", 36, 15, 12, "cab"),
    "Corner wall cabinet": ("CORNER WALL", 24, 30, 12, "cab"),
    "Microwave space": ("MICROWAVE", 30, 16, 15, "space"),
    "Hood / open space": ("HOOD / OPEN", 30, 30, 12, "space"),
    "Window (no cabinet)": ("WINDOW", 36, 30, 0, "space"),
    "Filler": ("FILLER", 3, 30, 12, "filler"),
}
TOP_MATERIALS = ["Peel & stick laminate over MDF", "Composite", "Laminate", "Synthetic", "Thinscape"]
CAB_MATERIALS = ["MDF", "Particle Board", "Plywood", "Solid Wood", "Thermofoil"]
BLANK_ORDER_FORM = "OF-Cabinet Order Form (Kitchen Layout).pdf"   # company forms library: the paper version
DEFAULT_CAB = {"style": "", "material": "", "color": "", "hw": "", "hw_custom": ""}   # material, color, hardware
CAB_HARDWARE = ["Satin Square", "Custom"]        # stocked; Custom = special order (described in hw_custom)
# peel & stick color list: left blank for now (Taylor, 10/26 - about 500 samples); colors are typed in
LAMINATE_COLORS = []
UPPER_GAP = 18.0          # counter to bottom of the uppers (standard)
TOP_THICK = 1.5           # countertop thickness drawn on the elevation
DEFAULT_TOP = {"material": TOP_MATERIALS[0], "color": "", "overhang": "1", "splash": "4", "edge": ""}

RED, BLUE, INK, GREY, DIM = (0.85, 0.12, 0.12), (0.05, 0.55, 0.85), (0.08, 0.08, 0.08), (0.55, 0.55, 0.55), \
    (0.15, 0.3, 0.75)
TOP_FILL = (0.86, 0.80, 0.68)


# ------------------------------------------------------------------ data
def is_v2(d):
    return isinstance(d, dict) and d.get("v") == V


def new_box(row, typ):
    t = (LOWER_TYPES if row == "lowers" else UPPER_TYPES).get(typ)
    if not t:
        typ = "Base cabinet" if row == "lowers" else "Wall cabinet"
        t = (LOWER_TYPES if row == "lowers" else UPPER_TYPES)[typ]
    _, w, h, dp, kind = t
    b = {"type": typ, "w": str(w), "h": f"{h:g}", "d": str(dp), "doors": "", "drawers": "No", "drawer_n": "",
         "drawer_h": "", "hinge": "", "label": "", "notes": ""}
    if kind in ("cab", "sink", "tall"):
        b["doors"] = "2" if w >= 24 else "1"
    if typ == "Drawer base":
        b.update(drawers="Yes", drawer_n="3", drawer_h="", doors="0")
    elif typ in ("Base cabinet",):
        b.update(drawers="Yes", drawer_n="1", drawer_h="6")
    return b


def starter():
    """A typical wall to start from (like the sketches): base, dishwasher, sink base, base / uppers over them."""
    lowers = [new_box("lowers", t) for t in ("Base cabinet", "Dishwasher space", "Sink base", "Base cabinet")]
    uppers = [new_box("uppers", "Wall cabinet") for _ in range(2)] + [new_box("uppers", "Window (no cabinet)"),
                                                                       new_box("uppers", "Wall cabinet")]
    uppers[0]["w"], uppers[1]["w"], uppers[2]["w"], uppers[3]["w"] = "18", "24", "36", "18"
    return {"v": V, "room": "Kitchen", "notes": "", "top": dict(DEFAULT_TOP), "cab": dict(DEFAULT_CAB),
            "walls": [{"name": "Wall 1", "lowers": lowers, "uppers": uppers}]}


STD_LOWER_DEPTH = {"Kitchen": "25-1/2", "Bath vanity": "22-1/2"}   # Taylor, 10/26 - changeable per box


def order_starter():
    """A new cabinet order: one wall with one base cabinet (kitchen standard depth) - add boxes as needed."""
    d = starter()
    b = new_box("lowers", "Base cabinet")
    b["d"] = STD_LOWER_DEPTH["Kitchen"]
    d["walls"] = [{"name": "Wall 1", "uppers": [], "lowers": [b]}]
    return d


def normalize(d):
    d = copy.deepcopy(d) if is_v2(d) else starter()
    d.setdefault("room", "Kitchen")
    d.setdefault("notes", "")
    d["top"] = dict(DEFAULT_TOP, **(d.get("top") or {}))
    d["cab"] = dict(DEFAULT_CAB, **(d.get("cab") or {}))
    walls = []
    for i, w in enumerate(d.get("walls") or []):
        w = dict(w)
        w["name"] = (w.get("name") or f"Wall {i + 1}").strip()
        for row in ("lowers", "uppers"):
            types = LOWER_TYPES if row == "lowers" else UPPER_TYPES
            boxes = []
            for b in w.get(row) or []:
                b = dict(new_box(row, b.get("type")), **{k: ("" if v is None else str(v)) for k, v in b.items()})
                if b["type"] not in types:
                    b["type"] = next(iter(types))
                boxes.append(b)
            w[row] = boxes
        walls.append(w)
    d["walls"] = walls or starter()["walls"]
    return d


def kind(row, b):
    return (LOWER_TYPES if row == "lowers" else UPPER_TYPES)[b["type"]][4]


def short(row, b):
    return (b.get("label") or "").strip() or (LOWER_TYPES if row == "lowers" else UPPER_TYPES)[b["type"]][0]


def dims(row, b):
    t = (LOWER_TYPES if row == "lowers" else UPPER_TYPES)[b["type"]]
    w, h, dp = num(b.get("w")), num(b.get("h")), num(b.get("d"))
    return (w if w is not None else t[1]), (h if h is not None else t[2]), (dp if dp is not None else t[3])


def box_id(row, i):
    return ("L" if row == "lowers" else "U") + str(i + 1)


# ------------------------------------------------------------------ countertop
def _sink_spots(p):
    """Countertop form boxes from a layout piece: sink center to each end, front overhang."""
    out = {"overhang": fin(p["overhang"]).rstrip('"')} if p.get("overhang") else {}
    if len(p.get("sink_at") or []) == 1:
        c = p["sink_at"][0]
        out.update(sink_center=fin(c).rstrip('"'), sink_center_r=fin(p["length"] - c).rstrip('"'))
    return out


def countertop(d):
    """Pieces per wall: [{wall, boxes: 'L1-L2', length, depth, sqft, sink, note}]"""
    d = normalize(d)
    over = num(d["top"].get("overhang")) or 0
    out = []
    for w in d["walls"]:
        run, x = [], 0.0

        def close(run=run, w=w):
            if not run:
                return
            length = sum(r[1] for r in run)
            depth = max(r[2] for r in run) + over
            sinks = [r for r in run if r[3] == "sink"]
            out.append({"wall": w["name"], "boxes": run[0][0] + ("-" + run[-1][0] if len(run) > 1 else ""),
                        "length": length, "depth": depth, "sqft": length * depth / 144.0,
                        "sink": ", ".join(f"{s[0]} center {fin(s[4] + s[1] / 2 - run[0][4])} from left end"
                                          for s in sinks),
                        "sink_at": [s[4] + s[1] / 2 - run[0][4] for s in sinks], "overhang": over})
            run.clear()
        for i, b in enumerate(w["lowers"]):
            k = kind("lowers", b)
            bw, bh, bd = dims("lowers", b)
            if k in ("cab", "sink", "filler", "space-counter"):
                run.append((box_id("lowers", i), bw, bd, k, x))
            else:
                close()
            x += bw
        close()
    return out


def schedule(d):
    """Rows for the box list: [id, wall, row, type, W, H, D, doors, drawers, hinge, notes]"""
    d = normalize(d)
    rows = []
    for w in d["walls"]:
        for row in ("uppers", "lowers"):
            for i, b in enumerate(w[row]):
                bw, bh, bd = dims(row, b)
                k = kind(row, b)
                dr = ""
                if k in ("cab", "sink", "tall") and b.get("drawers") == "Yes":
                    n = b.get("drawer_n") or "1"
                    dr = f"{n}" + (f' @ {fin(num(b["drawer_h"]))}' if num(b.get("drawer_h")) else "")
                elif k in ("cab", "sink", "tall"):
                    dr = "No"
                rows.append([box_id(row, i), w["name"], "Upper" if row == "uppers" else "Lower",
                             b["type"] + (f' ({b["label"]})' if b.get("label") else ""), fin(bw), fin(bh),
                             fin(bd) if bd else "", b.get("doors") or ("" if k.startswith("space") else ""),
                             dr, b.get("hinge") or "", b.get("notes") or ""])
    return rows


# ------------------------------------------------------------------ drawing (shared by PDF + on-screen SVG)
def wall_prims(w, box_w, box_h, style=""):
    """Primitives for one wall's elevation, scaled into a box_w x box_h area (points, y down).
    ("rect", x0, y0, x1, y1, stroke, fill, width) / ("line", x0, y0, x1, y1, color, width, dash)
    ("text", x, y, s, size, color, anchor, bold)"""
    lows, ups = w["lowers"], w["uppers"]
    lw = sum(dims("lowers", b)[0] for b in lows)
    uw = sum(dims("uppers", b)[0] for b in ups)
    total = max(lw, uw, 24)
    low_h = max([dims("lowers", b)[1] for b in lows if kind("lowers", b) not in ("tall",) and
                 dims("lowers", b)[1] < 40] or [34.5])
    up_h = max([dims("uppers", b)[1] for b in ups] or [30])
    tall = max([dims("lowers", b)[1] for b in lows] or [0])
    height = max(low_h + TOP_THICK + UPPER_GAP + up_h, tall)
    pad_l, pad_r, pad_t, pad_b = 40, 40, 44, 46        # room for the dimension lines
    s = min((box_w - pad_l - pad_r) / total, (box_h - pad_t - pad_b) / height)
    x0 = pad_l + ((box_w - pad_l - pad_r) - total * s) / 2
    floor = pad_t + height * s
    top_line = floor - (low_h + TOP_THICK + UPPER_GAP + up_h) * s
    P = []
    fs = max(5.0, min(8.0, s * 3.2))

    def X(inches):
        return x0 + inches * s

    def hdim(xa, xb, y, label, above):
        t = 4 if above else -4
        P.append(("line", xa, y, xb, y, DIM, 0.6, None))
        P.append(("line", xa, y - 4, xa, y + 4, DIM, 0.6, None))
        P.append(("line", xb, y - 4, xb, y + 4, DIM, 0.6, None))
        P.append(("text", (xa + xb) / 2, y - 2.5 if above else y + 8.5, label, fs, DIM, "middle", True))
        del t

    def vdim(x, ya, yb, label, side):
        P.append(("line", x, ya, x, yb, DIM, 0.6, None))
        P.append(("line", x - 4, ya, x + 4, ya, DIM, 0.6, None))
        P.append(("line", x - 4, yb, x + 4, yb, DIM, 0.6, None))
        P.append(("vtext", x + (-4 if side == "left" else 9), (ya + yb) / 2, label, fs, DIM, "middle", True))

    # floor line
    P.append(("line", X(0) - 10, floor, X(total) + 10, floor, GREY, 0.8, None))
    # lowers
    x, counter = 0.0, []
    first_h = None
    for i, b in enumerate(lows):
        bw, bh, bd = dims("lowers", b)
        k = kind("lowers", b)
        r = (X(x), floor - bh * s, X(x + bw), floor)
        color = RED if k.startswith("space") else (BLUE if k == "sink" else INK)
        P.append(("rect", *r, color, None, 1.6 if color != INK else 1.2))
        if k in ("cab", "sink", "tall"):
            _fronts(P, r, b, s, fs, style)
        if k == "sink":         # sink bowl in the counter above
            pass
        inner = first_h is not None and abs(bh - first_h) > 0.01 and k != "space-counter"
        _label(P, r, short("lowers", b), box_id("lowers", i), color, fs,
               _drawer_band(b, s) if k in ("cab", "sink", "tall") else 0, "right" if inner else None)
        if k in ("cab", "sink", "filler", "space-counter"):
            counter.append((x, x + bw, k))
        hdim(r[0], r[2], floor + 14, fin(bw), False)
        if first_h is None:
            first_h = bh
        elif abs(bh - first_h) > 0.01 and k != "space-counter":
            vdim(r[2] - 8, r[1], floor, fin(bh), "left")
        x += bw
    if lows:
        vdim(X(0) - 14, floor - dims("lowers", lows[0])[1] * s, floor, fin(dims("lowers", lows[0])[1]), "left")
        hdim(X(0), X(lw), floor + 30, f"Total {fin(lw)}", False)
    # countertop over the counter-bearing lowers (a stove / fridge / open space splits it)
    segs = []
    for a, b_, k in counter:
        if segs and abs(segs[-1][1] - a) < 0.01:
            segs[-1][1] = b_
        else:
            segs.append([a, b_])
    for a, b_ in segs:
        P.append(("rect", X(a), floor - (low_h + TOP_THICK) * s, X(b_), floor - low_h * s, INK, TOP_FILL, 0.8))
    # uppers - hang from the same top line
    x = 0.0
    first_uh = None
    for i, b in enumerate(ups):
        bw, bh, bd = dims("uppers", b)
        k = kind("uppers", b)
        r = (X(x), top_line, X(x + bw), top_line + bh * s)
        color = RED if k == "space" else INK
        dash = "[3 2] 0" if b["type"] in ("Window (no cabinet)", "Hood / open space") else None
        if dash:
            P.append(("line", r[0], r[1], r[2], r[1], color, 1.0, dash))
            P.append(("line", r[0], r[3], r[2], r[3], color, 1.0, dash))
            P.append(("line", r[0], r[1], r[0], r[3], color, 1.0, dash))
            P.append(("line", r[2], r[1], r[2], r[3], color, 1.0, dash))
        else:
            P.append(("rect", *r, color, None, 1.6 if color != INK else 1.2))
        if k == "cab":
            _fronts(P, r, b, s, fs, style)
        inner = first_uh is not None and abs(bh - first_uh) > 0.01
        _label(P, r, short("uppers", b), box_id("uppers", i), color, fs, 0, "left" if inner else None)
        hdim(r[0], r[2], top_line - 14, fin(bw), True)
        if first_uh is None:
            first_uh = bh
        elif abs(bh - first_uh) > 0.01:          # e.g. the shorter cabinet over the stove
            vdim(r[0] + 9, r[1], r[3], fin(bh), "right")
        x += bw
    if ups:
        last = ups[-1]
        vdim(X(uw) + 14, top_line, top_line + dims("uppers", last)[1] * s, fin(dims("uppers", last)[1]), "right")
        if abs(uw - lw) > 0.01:
            hdim(X(0), X(uw), top_line - 30, f"Total {fin(uw)}", True)
    return P


def _label(P, r, name, bid, color, fs, band, reserve=None):
    """Box name + its id, centered in the part of the box under the drawers; on a white pad so lines don't cross it.
    reserve='left'/'right' keeps clear of a height dimension drawn inside that side of the box."""
    x0, y0, x1, y1 = r
    if reserve == "left":
        x0 += 14
    elif reserve == "right":
        x1 -= 14
    cy = (y0 + band + y1) / 2
    size = fs
    w_ = len(name) * size * 0.58
    while w_ > (x1 - x0) - 4 and size > 4.5:
        size -= 0.25
        w_ = len(name) * size * 0.58
    P.append(("rect", (x0 + x1) / 2 - w_ / 2 - 2, cy - size - 1, (x0 + x1) / 2 + w_ / 2 + 2, cy + size + 2,
              None, (1, 1, 1), 0))
    P.append(("text", (x0 + x1) / 2, cy, name, size, color, "middle", True))
    P.append(("text", (x0 + x1) / 2, cy + size + 1, bid, size * 0.9, GREY, "middle", False))


def _drawer_band(b, s):
    if b.get("drawers") != "Yes":
        return 0
    n = int(num(b.get("drawer_n")) or 1)
    dh = num(b.get("drawer_h")) or 6
    return min(n * dh * s, 999) if b["type"] != "Drawer base" else 0


CAB_STYLES = ["Shaker", "Shaker Slim", "Italia", "Bradford", "Elegante", "Elegante Gauntlet Supermatte"]
LIGHT = (0.72, 0.72, 0.72)


def _style_face(P, a, b_, c, d_, style, s):
    """The door / drawer front's look inside its outline (from the style photos):
    Shaker = wide frame around a flat panel; Shaker Slim = narrow frame; Bradford = shaker frame with
    beadboard grooves; Italia = smooth slab with soft rounded edges; Elegante = slab with horizontal wood
    grain; Elegante Gauntlet Supermatte = plain flat slab."""
    st = (style or "").strip().lower()
    w, h = c - a, d_ - b_
    if w < 6 or h < 6 or not st:
        return
    if st in ("shaker", "shaker slim", "bradford"):
        f = (1.1 if st == "shaker slim" else 2.4) * s
        f = min(f, w * 0.28, h * 0.28)
        ia, ib, ic, id_ = a + f, b_ + f, c - f, d_ - f
        P.append(("rect", ia, ib, ic, id_, LIGHT, None, 0.5))
        if st == "bradford" and ic - ia > 4:
            step = max(1.25 * s, 2.2)
            x = ia + step
            while x < ic - step * 0.5:
                P.append(("line", x, ib + 0.8, x, id_ - 0.8, LIGHT, 0.4, None))
                x += step
    elif st == "italia":
        f = min(0.7 * s, w * 0.12, h * 0.12)
        P.append(("rect", a + f, b_ + f, c - f, d_ - f, (0.85, 0.85, 0.85), None, 0.5))
    elif st == "elegante":
        n = max(2, int(h / max(2.0 * s, 3)))
        for i in range(1, n):
            y = b_ + h * i / n
            P.append(("line", a + 1, y, c - 1, y, (0.82, 0.82, 0.82), 0.35, None))


def _fronts(P, r, b, s, fs, style=""):
    """Drawer fronts across the top (or the whole box for a drawer base), doors under them."""
    x0, y0, x1, y1 = r
    inset = 2.0
    y = y0
    if b.get("drawers") == "Yes":
        n = max(1, int(num(b.get("drawer_n")) or 1))
        if b["type"] == "Drawer base":
            dh = (y1 - y0) / n
        else:
            dh = (num(b.get("drawer_h")) or 6) * s
        for _ in range(n):
            P.append(("rect", x0 + inset, y + inset, x1 - inset, min(y + dh, y1) - inset * 0.5, GREY, None, 0.6))
            _style_face(P, x0 + inset, y + inset, x1 - inset, min(y + dh, y1) - inset * 0.5, style, s)
            cx = (x0 + x1) / 2
            P.append(("line", cx - 5, y + dh / 2, cx + 5, y + dh / 2, GREY, 1.2, None))
            y += dh
            if y >= y1 - 1:
                return
    doors = int(num(b.get("doors")) or 0)
    if doors <= 0 or y >= y1 - 4:
        return
    dw = (x1 - x0) / doors
    for k in range(doors):
        a = x0 + k * dw
        P.append(("rect", a + inset, y + inset, a + dw - inset, y1 - inset, GREY, None, 0.6))
        _style_face(P, a + inset, y + inset, a + dw - inset, y1 - inset, style, s)
        hx = a + dw - inset - 4 if (k == 0 and doors == 2) or (doors == 1 and b.get("hinge") == "Left") \
            else a + inset + 4
        if doors == 2 and k == 1:
            hx = a + inset + 4
        upper = b["type"] in UPPER_TYPES and b["type"] not in LOWER_TYPES
        hy = (y1 - inset - 14) if upper else (y + inset + 6)
        P.append(("line", hx, hy, hx, hy + 8, GREY, 1.2, None))


def svg(prims, w, h):
    from html import escape

    def col(c):
        return "none" if c is None else "rgb({},{},{})".format(*(int(v * 255) for v in c))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}" width="100%" '
           f'style="background:#fff;font-family:Helvetica,Arial,sans-serif">']
    for p in prims:
        if p[0] == "rect":
            _, a, b, c, d_, stroke, fill, sw = p
            out.append(f'<rect x="{a:.1f}" y="{b:.1f}" width="{c - a:.1f}" height="{d_ - b:.1f}" '
                       f'stroke="{col(stroke)}" fill="{col(fill)}" stroke-width="{sw}"/>')
        elif p[0] == "line":
            _, a, b, c, d_, color, sw, dash = p
            da = ' stroke-dasharray="3 2"' if dash else ""
            out.append(f'<line x1="{a:.1f}" y1="{b:.1f}" x2="{c:.1f}" y2="{d_:.1f}" stroke="{col(color)}" '
                       f'stroke-width="{sw}"{da}/>')
        elif p[0] in ("text", "vtext"):
            _, a, b, s, size, color, anchor, bold = p
            rot = f' transform="rotate(-90 {a:.1f} {b:.1f})"' if p[0] == "vtext" else ""
            out.append(f'<text x="{a:.1f}" y="{b:.1f}" font-size="{size:.1f}" fill="{col(color)}" '
                       f'text-anchor="{anchor}" font-weight="{"bold" if bold else "normal"}"{rot}>{escape(str(s))}</text>')
    out.append("</svg>")
    return "".join(out)


def to_pdf(page, prims, ox, oy):
    import pymupdf as fitz
    for p in prims:
        if p[0] == "rect":
            _, a, b, c, d_, stroke, fill, sw = p
            page.draw_rect(fitz.Rect(a + ox, b + oy, c + ox, d_ + oy), color=stroke, fill=fill, width=sw)
        elif p[0] == "line":
            _, a, b, c, d_, color, sw, dash = p
            page.draw_line((a + ox, b + oy), (c + ox, d_ + oy), color=color, width=sw, dashes=dash)
        elif p[0] in ("text", "vtext"):
            _, a, b, s, size, color, anchor, bold = p
            font = "hebo" if bold else "helv"
            tw = fitz.get_text_length(str(s), fontname=font, fontsize=size)
            if p[0] == "vtext":
                page.insert_text((a + ox, b + oy + tw / 2), str(s), fontsize=size, fontname=font, color=color,
                                 rotate=90)
            else:
                xx = a - tw / 2 if anchor == "middle" else a
                page.insert_text((xx + ox, b + oy), str(s), fontsize=size, fontname=font, color=color)


def wall_svg(w, width=760, height=330, style=""):
    return svg(wall_prims(w, width, height, style), width, height)


# ------------------------------------------------------------------ PDF
PW, PH = 792, 612        # landscape


def _header(doc, title, sub=""):
    import pymupdf as fitz
    from .manager_forms import COMPANY
    pg = doc.new_page(width=PW, height=PH)
    src = fitz.open(COMPANY)
    pg.show_pdf_page(fitz.Rect(30, 14, 210, 80), src, 0, clip=fitz.Rect(68, 22, 337, 121))
    pg.draw_rect(fitz.Rect(420, 18, PW - 30, 40), color=None, fill=(0, 0, 0))
    size = 14
    while fitz.get_text_length(title, fontname="tiro", fontsize=size) > PW - 470 and size > 8:
        size -= 0.5
    tw = fitz.get_text_length(title, fontname="tiro", fontsize=size)
    pg.insert_text((420 + (PW - 450 - tw) / 2, 34), title, fontsize=size, fontname="tiro", color=(1, 1, 1))
    if sub:
        pg.insert_text((420, 54), sub, fontsize=8, fontname="helv", color=(0.25, 0.25, 0.25))
    pg.insert_text((30, PH - 14), "Apartment Interior Supply  ·  5325 S. Kyrene Rd., Suite 103, Tempe, AZ 85283  ·  "
                   "Phone (480) 964-7600  ·  Fax (480) 964-7610", fontsize=7, fontname="helv",
                   color=(0.38, 0.38, 0.38))
    return pg


def _txt(pg, x, y, s, size=8, bold=False, color=INK):
    pg.insert_text((x, y), str(s), fontsize=size, fontname="hebo" if bold else "helv", color=color)


def _table(pg, x0, y, cols, rows, x1, row_h=12, size=7.2):
    import pymupdf as fitz
    pg.draw_rect(fitz.Rect(x0, y, x1, y + row_h), color=None, fill=(0.9, 0.92, 0.96))
    x = x0
    for t, wdt in cols:
        _txt(pg, x + 3, y + row_h - 3.5, t, 6.6, True)
        x += wdt
    y += row_h
    for cells in rows:
        x = x0
        for (t, wdt), c in zip(cols, cells):
            s_ = str(c or "")
            sz = size
            while fitz.get_text_length(s_, fontname="helv", fontsize=sz) > wdt - 5 and sz > 5:
                sz -= 0.25
            _txt(pg, x + 3, y + row_h - 3.5, s_, sz)
            x += wdt
        pg.draw_line((x0, y + row_h), (x1, y + row_h), color=GREY, width=0.3)
        y += row_h
    pg.draw_rect(fitz.Rect(x0, y - row_h * (len(rows) + 1), x1, y), color=INK, width=0.7)
    return y


def _order_strip(pg, order):
    """Customer / PO / date / contact boxes across the top of a cabinet order page. Returns the y under it."""
    import pymupdf as fitz
    h = order.get("header") or {}
    city = " ".join(p for p in [h.get("city"), h.get("state"), h.get("zip")] if p)
    addr = ", ".join(p for p in [h.get("address"), city] if p)
    contact = "  ·  ".join(p for p in [h.get("contact"), h.get("contact_phone"), h.get("contact_email")] if p)
    rows = [[("Property", h.get("name"), 230), ("Acct #", h.get("acct"), 70), ("Address", addr, 262),
             ("P.O. #", h.get("po"), 85), ("Date", h.get("date"), 85)],
            [("Mgmt Co.", h.get("mgmt"), 170), ("Phone", h.get("phone"), 100),
             ("Contact (who to talk to)", contact, 292), ("Sales Rep", order.get("sales_rep"), 170)]]
    y = 70
    for row in rows:
        x0 = 30
        for lab, val, w in row:
            pg.draw_rect(fitz.Rect(x0, y, x0 + w, y + 22), color=GREY, width=0.6)
            _txt(pg, x0 + 3, y + 7, lab.upper(), 5.8, True, GREY)
            v = str(val or "")
            size = 9
            while size > 5.5 and fitz.get_text_length(v, fontname="helv", fontsize=size) > w - 6:
                size -= 0.5
            _txt(pg, x0 + 3, y + 18, v, size, False, INK)
            x0 += w
        y += 22
    return y + 4


def _order_footer(pg, order):
    """Comments under the last page's box table."""
    c = (order.get("comments") or "").strip()
    if c:
        _txt(pg, 30, PH - 34, "Comments: " + c[:220], 8.5, True)


def _material_line(pg, d, top=True):
    """Cabinet material + color (and the countertop's, when it's on this order) under the logo."""
    c, t = d["cab"], d["top"]
    s = (f"Door style: {c.get('style') or '__________'}      "
         f"Cabinet material: {c.get('material') or '__________'}      Cabinet color: {c.get('color') or '__________'}")
    hw = c.get("hw") or ""
    if hw == "Custom" and c.get("hw_custom"):
        hw = "Custom - " + c["hw_custom"]
    if not top:
        s += f"      Hardware: {hw or '____________'}"
    if top:
        s += f"      Countertop: {t.get('material') or '________'}  ·  {t.get('color') or '________'}"
    import pymupdf as fitz
    size = 8.5 if not top else 8
    while size > 5.5 and fitz.get_text_length(s, fontname="hebo", fontsize=size) > PW - 30 - 214:
        size -= 0.25                          # long style names: shrink to fit the page
    _txt(pg, 214, 63.5, s, size, True)


def make_pdf(layout, customer=None, out_path=None, with_top=True, order=None):
    """One landscape page per wall (elevation + its boxes), then the countertop pieces.
    with_top=False: cabinets only (the countertop has its own order form).
    order={header, comments, sales_rep}: this IS the cabinet order - the customer / PO / contact strip goes on
    every page and the title is Cabinet Order Form."""
    import pymupdf as fitz
    d = normalize(layout["data"] if "data" in layout else layout)
    doc = fitz.open()
    if order is not None:
        with_top = False
    cname = (customer or {}).get("name") or ((order or {}).get("header") or {}).get("name") or ""
    where = " - ".join(p for p in [cname, ("Unit " + layout.get("unit")) if layout.get("unit") else "",
                                   layout.get("name") or d.get("room")] if p)
    rows_all = schedule(d)
    cols = [("Box", 34), ("Row", 40), ("Type", 170), ("Width", 50), ("Height", 50), ("Depth", 46),
            ("Doors", 38), ("Drawers", 70), ("Hinge", 42), ("Notes", PW - 60 - 540)]
    for w in d["walls"]:
        title = ("Cabinet Order Form" if order is not None else
                 "Cabinet & Countertop Layout" if with_top else "Cabinet Order - Kitchen Layout")
        pg = _header(doc, title, f"{where}  ·  {w['name']}")
        _material_line(pg, d, with_top)
        top_y = 66
        if order is not None:
            top_y = _order_strip(pg, order)
        prims = wall_prims(w, PW - 60, 368 - top_y - 2, d["cab"].get("style"))
        pg.draw_rect(fitz.Rect(30, top_y, PW - 30, 368), color=GREY, width=0.5)
        to_pdf(pg, prims, 30, top_y + 1)
        _txt(pg, 34, 364, "Red = appliance / open space (no cabinet)   Blue = sink base   Widths left to right, "
             "standing facing the wall. Inches.", 6.6, False, GREY)
        rows = [[r[0], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]] for r in rows_all if r[1] == w["name"]]
        y, first = 376, True
        while rows or first:
            fit = int((PH - (46 if order is not None else 30) - y) // 11.5) - 1
            chunk, rows = rows[:fit], rows[fit:]
            _table(pg, 30, y, cols, chunk, PW - 30, row_h=11.5)
            first = False
            if rows:
                pg = _header(doc, title, f"{where}  ·  {w['name']} (boxes, continued)")
                y = 66
    if not with_top:
        if order is not None:
            _order_footer(doc[-1], order)
        pdf = doc.tobytes(garbage=3, deflate=True)
        doc.close()
        if out_path:
            with open(out_path, "wb") as fh:
                fh.write(pdf)
        return pdf
    # countertop page
    pg = _header(doc, "Countertop", where)
    t = d["top"]
    _txt(pg, 30, 82, f"Material: {t.get('material') or ''}", 10, True)
    _txt(pg, 30, 96, f"Color: {t.get('color') or '(pick from samples)'}", 10)
    _txt(pg, 300, 82, f"Front overhang: {fin(num(t.get('overhang')) or 0)}", 10)
    _txt(pg, 300, 96, f"Backsplash rise: {fin(num(t.get('splash')) or 0)}", 10)
    if t.get("edge"):
        _txt(pg, 520, 82, f"Edge: {t['edge']}", 10)
    pieces = countertop(d)
    y = _table(pg, 30, 110, [("Piece", 40), ("Wall", 90), ("Over boxes", 80), ("Length", 70), ("Depth", 70),
                             ("Sq ft", 50), ("Sink cut-out", PW - 60 - 400)],
               [[i + 1, p["wall"], p["boxes"], fin(p["length"]), fin(p["depth"]), f"{p['sqft']:.1f}", p["sink"]]
                for i, p in enumerate(pieces)], PW - 30, row_h=14, size=8.5)
    tot = sum(p["sqft"] for p in pieces)
    _txt(pg, 30, y + 16, f"Total: {len(pieces)} piece(s), {tot:.1f} sq ft (before backsplash / waste).", 9, True)
    if (t.get("material") or "").startswith("Peel"):
        splash = num(t.get("splash")) or 0
        need = tot + sum(p["length"] * (splash + 1.5) / 144.0 for p in pieces)
        _txt(pg, 30, y + 30, f"Peel & stick: about {need:.1f} sq ft with the edge and backsplash - a set of two "
             "2' x 8' half sheets covers 32 sq ft.", 8.5)
    if d.get("notes"):
        _txt(pg, 30, y + 48, "Notes: " + d["notes"][:180], 8.5)
    pdf = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    if out_path:
        with open(out_path, "wb") as fh:
            fh.write(pdf)
    return pdf


def blank_pdf(order=False):
    """Paper version (also the customer form's layout page): a grid to sketch on + a box list to fill in.
    order=True: the blank Cabinet Order Form (customer / PO / contact boxes on top, cabinets only)."""
    import pymupdf as fitz
    doc = fitz.open()
    pg = _header(doc, "Cabinet Order Form" if order else "Cabinet & Countertop Layout",
                 "Draw each wall: UPPER boxes on top, LOWER boxes under them, left to right. "
                 "Number them U1, U2... and L1, L2...")
    gy = _order_strip(pg, {}) if order else 66
    g = fitz.Rect(30, gy, PW - 30, 300)
    step = 9
    x = g.x0
    while x <= g.x1:
        pg.draw_line((x, g.y0), (x, g.y1), color=(0.85, 0.88, 0.95), width=0.4)
        x += step
    y = g.y0
    while y <= g.y1:
        pg.draw_line((g.x0, y), (g.x1, y), color=(0.85, 0.88, 0.95), width=0.4)
        y += step
    pg.draw_rect(g, color=GREY, width=0.6)
    _txt(pg, g.x0 + 4, g.y1 - 4, "Mark spaces for the DISHWASHER, STOVE and FRIDGE in red; the SINK BASE in blue.",
         7, False, RED)
    cols = [("Box", 34), ("Upper/Lower", 56), ("Type (base, sink base, drawer base, wall, over stove...)", 230),
            ("Width", 54), ("Height", 54), ("Depth", 54), ("Doors", 40), ("Drawers? how many / tall", 110)]
    cols.append(("Notes", PW - 60 - sum(c[1] for c in cols)))
    _table(pg, 30, 308, cols, [[""] * len(cols) for _ in range(18)], PW - 30, row_h=14)
    if order:
        _txt(pg, 30, PH - 42, "Door style:  [ ] Shaker   [ ] Shaker Slim   [ ] Italia   [ ] Bradford   [ ] Elegante   "
             "[ ] Elegante Gauntlet Supermatte", 8.5, True)
        _txt(pg, 30, PH - 28, "Cabinet material: ____________________     Cabinet color: ____________________"
             "     Hardware:  [ ] Satin Square   [ ] Custom: ____________________", 8.5, True)
    else:
        _txt(pg, 30, PH - 42, "Cabinets:  Material: ______________________     Color: ______________________", 8.5, True)
        _txt(pg, 30, PH - 28, "Countertop:  Material: ____________________     Color: ______________"
             "     Front overhang: ____     Backsplash rise: ____", 8.5, True)
    pdf = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return pdf


def order_lines(d, form_key):
    """Order lines from the layout: cabinet boxes for the cabinet forms (appliance / open spaces skipped),
    countertop pieces for the countertop form."""
    d = normalize(d)
    out = []
    if form_key == "countertop":
        t = d["top"]
        typed = (t.get("material") or "").strip()
        from .catalog import COUNTERTOP_MATERIALS
        mat = ("Peel & Stick Laminate" if typed.lower().startswith("peel") else
               next((m for m in COUNTERTOP_MATERIALS if m.lower() == typed.lower()), "Custom" if typed else ""))
        for p in countertop(d):
            out.append({"ct_material_other": typed if mat == "Custom" else "","qty": "1", "ct_room": "Kitchen" if d.get("room") == "Kitchen" else "Bathroom Vanity",
                        "ct_material": mat, "length": fin(p["length"]).rstrip('"'),
                        "depth": fin(p["depth"]).rstrip('"'),
                        "bs_back": "Yes" if (num(t.get("splash")) or 0) > 0 else "",
                        "sink": "Single Bowl" if p["sink"] else "None", "ct_color": t.get("color") or "",
                        **_sink_spots(p),
                        "line_comments": f"{p['wall']} over {p['boxes']}" + (f"; {p['sink']}" if p["sink"] else "") +
                                         (f"; backsplash {fin(num(t.get('splash')))} rise" if num(t.get("splash")) else "")})
        return out
    for w in d["walls"]:
        for row in ("lowers", "uppers"):
            for i, b in enumerate(w[row]):
                k = kind(row, b)
                if k.startswith("space") or k == "filler":
                    continue
                bw, bh, bd = dims(row, b)
                line = {"qty": "1", "width": fin(bw).rstrip('"'), "height": fin(bh).rstrip('"'),
                        "depth": fin(bd).rstrip('"'),
                        "cab_room": "Bathroom Vanity" if d.get("room") == "Bath vanity" else "Kitchen",
                        "cab_material": d["cab"].get("material") or "", "cab_finish": d["cab"].get("color") or "",
                        "cab_hw": d["cab"].get("hw") or "", "cab_hardware": d["cab"].get("hw_custom") or "",
                        "style": d["cab"].get("style") or "",
                        "unit": d.get("unit") or "",
                        "line_comments": f"{w['name']} {box_id(row, i)} {b['type']}" +
                                         (f" - {b['notes']}" if b.get("notes") else "")}
                if b.get("hinge") in ("Left", "Right", "Pair"):
                    line["hinge_side"] = b["hinge"]
                if form_key == "cabinet_custom":
                    line["cab_kind"] = {"sink": "Sink Base", "tall": "Tall / Pantry"}.get(
                        k, "Wall" if row == "uppers" else "Base")
                    line["doors_qty"] = b.get("doors") or ""
                    line["drawers_qty"] = (b.get("drawer_n") or "1") if b.get("drawers") == "Yes" else "0"
                else:
                    line["cab_item"] = ("Wall Cabinet" if row == "uppers" else
                                        "Sink Base" if k == "sink" else
                                        "Drawer Base" if b["type"] == "Drawer base" else "Base Cabinet")
                    line["drawers"] = "Yes" if b.get("drawers") == "Yes" else "No"
                out.append(line)
    return out


# ------------------------------------------------------------------ customer form (managers sketch + measure)
CUSTOMER_FORM = "Customer Order Form - Kitchen Cabinet Layout.pdf"


def customer_form_pdf(out_path):
    """Fillable one-page landscape form for maintenance managers: sketch grid + a box-by-box list."""
    import pymupdf as fitz
    from .manager_forms import COMPANY, EMAIL_JS, REP_EMAIL
    doc = fitz.open()
    pg = doc.new_page(width=PW, height=PH)
    src = fitz.open(COMPANY)
    pg.show_pdf_page(fitz.Rect(24, 10, 200, 74), src, 0, clip=fitz.Rect(68, 22, 337, 121))
    pg.draw_rect(fitz.Rect(220, 12, PW - 24, 40), color=None, fill=(0, 0, 0))
    _txt(pg, 232, 31, "CUSTOMER ORDER FORM  -  KITCHEN CABINET LAYOUT", 13, True, (1, 1, 1))
    widgets = []

    def field(name, rect, size=8.5):
        w = fitz.Widget()
        w.field_type, w.field_name, w.rect = fitz.PDF_WIDGET_TYPE_TEXT, name, fitz.Rect(rect)
        w.text_font, w.text_fontsize = "Helv", size
        w.fill_color, w.border_color, w.border_width = (0.85, 1, 1), (0.6, 0.6, 0.6), 0.4
        widgets.append(w)

    def check(name, x, y, label):
        w = fitz.Widget()
        w.field_type, w.field_name = fitz.PDF_WIDGET_TYPE_CHECKBOX, name
        w.rect = fitz.Rect(x, y - 8, x + 9, y + 1)
        w.border_color, w.border_width, w.fill_color, w.field_value = (0, 0, 0), 0.6, (1, 1, 1), False
        widgets.append(w)
        _txt(pg, x + 12, y, label, 8)
    x = 220
    for lab, name, wdt in (("PROPERTY:", "Property", 180), ("YOUR NAME:", "Manager Name", 120),
                           ("PHONE:", "Manager Phone", 90)):
        _txt(pg, x, 55, lab, 7.5, True)
        lw = fitz.get_text_length(lab, fontname="hebo", fontsize=7.5)
        field(name, (x + lw + 3, 46, x + lw + 3 + wdt, 58))
        x += lw + wdt + 12
    x = 220
    for lab, name, wdt in (("EMAIL:", "Manager Email", 180), ("UNIT / FLOORPLAN:", "Unit", 80),
                           ("DATE:", "Date", 70), ("PO #:", "PO Number", 60)):
        _txt(pg, x, 72, lab, 7.5, True)
        lw = fitz.get_text_length(lab, fontname="hebo", fontsize=7.5)
        field(name, (x + lw + 3, 63, x + lw + 3 + wdt, 75))
        x += lw + wdt + 12
    # how to
    _txt(pg, 24, 92, "1  Stand facing the wall. Draw the UPPER boxes on top and the LOWER boxes under them, left to "
         "right. Number them U1, U2...  and  L1, L2...", 8, True, (0.0, 0.0, 0.4))
    _txt(pg, 24, 103, "2  Leave the spaces for the DISHWASHER, STOVE and FRIDGE (mark them) and mark the SINK BASE. "
         "3  Measure every box: width, height, depth (inches, to 1/8\").  4  Drawers? How many and how tall.",
         8, True, (0.0, 0.0, 0.4))
    g = fitz.Rect(24, 110, PW - 24, 300)
    step = 9
    xx = g.x0
    while xx <= g.x1:
        pg.draw_line((xx, g.y0), (xx, g.y1), color=(0.85, 0.88, 0.95), width=0.4)
        xx += step
    yy = g.y0
    while yy <= g.y1:
        pg.draw_line((g.x0, yy), (g.x1, yy), color=(0.85, 0.88, 0.95), width=0.4)
        yy += step
    pg.draw_rect(g, color=GREY, width=0.6)
    _txt(pg, g.x0 + 4, g.y0 + 10, "SKETCH  (example: U1 U2 [window] U3 over  L1 [dishwasher] L2-sink L3 [stove] L4)",
         7, False, GREY)
    cols = [("Box", 36), ("Upper/Lower", 60), ("Type (base, sink base, drawer base, wall, over stove...)", 210),
            ("Width", 54), ("Height", 54), ("Depth", 54), ("Doors", 40), ("Drawers? how many / tall", 110)]
    cols.append(("Notes", PW - 48 - sum(c[1] for c in cols)))
    y = 306
    pg.draw_rect(fitz.Rect(24, y, PW - 24, y + 12), color=None, fill=(0.9, 0.92, 0.96))
    xx = 24
    for t, wdt in cols:
        _txt(pg, xx + 3, y + 9, t, 6.6, True)
        xx += wdt
    y += 12
    rows = 16
    for r_ in range(rows):
        xx = 24
        for ci, (t, wdt) in enumerate(cols):
            field(f"Box {r_ + 1} {t.split(' ')[0].split('/')[0]}", (xx + 1, y + 1, xx + wdt - 1, y + 13), 8)
            xx += wdt
        y += 14
    pg.draw_rect(fitz.Rect(24, 306, PW - 24, y), color=INK, width=0.7)
    y += 13
    # cabinets only - the countertop has its own customer form
    xx = 24
    for lab, name, wdt in (("DOOR STYLE:", "Door Style", 105), ("MATERIAL:", "Cabinet Material", 105),
                           ("COLOR:", "Cabinet Color", 95)):
        _txt(pg, xx, y, lab, 8.5, True)
        lw = fitz.get_text_length(lab, fontname="hebo", fontsize=8.5)
        field(name, (xx + lw + 4, y - 9, xx + lw + 4 + wdt, y + 2))
        xx += lw + wdt + 16
    _txt(pg, xx, y, "HARDWARE:", 8.5, True)
    xx += fitz.get_text_length("HARDWARE:", fontname="hebo", fontsize=8.5) + 6
    check("Hardware Satin Square", xx, y, "Satin Square")
    check("Hardware Custom", xx + 78, y, "Custom")
    _txt(pg, 24, PH - 10, f"WHEN DONE: click \"Email Form\" (or save and email it) to {REP_EMAIL}.  Questions? Call "
         "(480) 964-7600.", 8, True, (0.0, 0.0, 0.4))
    b = fitz.Widget()
    b.field_type, b.field_name = fitz.PDF_WIDGET_TYPE_BUTTON, "Email Form 1"
    b.rect = fitz.Rect(PW - 114, PH - 26, PW - 24, PH - 6)
    b.button_caption = "Email Form"
    b.fill_color, b.text_color, b.text_fontsize = (0.2, 0.4, 0.7), (1, 1, 1), 9
    b.script = EMAIL_JS
    widgets.append(b)
    for w in widgets:
        pg.add_widget(w)
    doc.set_metadata({"title": "Customer Order Form - Kitchen Cabinet Layout", "creator": "Apartment Interior Supply"})
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    return out_path
