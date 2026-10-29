"""Kitchen shape: Straight, Mirrored (galley - two facing walls), L-Shape (corner) or U-Shape.

Draws the top view of the counter runs for the shape picked, with a box for each measurement where it's taken:
    Straight   one run on the back wall                      back wall length (+ side wall length = depth)
    Mirrored   two runs facing each other (galley)           back wall + facing wall
    L-Shape    back wall + a run down the left wall          back wall + 2nd (left) wall
    U-Shape    back wall + left wall + right wall            back wall + 2nd (left) + 3rd (right) wall
Wall runs are measured along the wall from the corner to the end of the counter.
The same drawing goes on the Countertop Order Form (instead of the single straight wall), in the app while the
form is filled in, and on the cabinet order (the kitchen's walls as Wall 1 = back, 2 = left / facing, 3 = right).
Primitives are the ones cabinet_layout draws with (to_pdf / svg).
"""
from .kitchen import fin, num

SHAPES = ["Straight", "Mirrored", "L-Shape", "U-Shape"]
SHAPE_HELP = {"Straight": "one wall", "Mirrored": "galley - counters on two facing walls",
              "L-Shape": "corner - back wall + left wall", "U-Shape": "back wall + left wall + right wall"}
WALL_NAMES = {"Straight": ["Back wall"], "Mirrored": ["Back wall", "Facing wall"],
              "L-Shape": ["Back wall", "Left wall"], "U-Shape": ["Back wall", "Left wall", "Right wall"]}
AISLE = 42.0          # galley walkway (drawing only)

INK, GREY, WALL, TOP, BLUE, VAL = (0, 0, 0), (0.35, 0.35, 0.35), (0.2, 0.2, 0.2), (0.94, 0.94, 0.94), \
    (0.05, 0.55, 0.85), (0.05, 0.25, 0.75)


def norm(shape):
    s = str(shape or "").strip().lower()
    for k in SHAPES:
        if s == k.lower() or s.startswith(k.lower().split("-")[0]):
            return k
    if s.startswith("galley") or s.startswith("mirror"):
        return "Mirrored"
    return "Straight"


def _n(v, default):
    x = num(v)
    return x if x and x > 0 else default


def plan_prims(shape, v, W, H, labels=None, boxes=True):
    """Top view in a W x H area (points, y down). v: the countertop line's values (length, length2, length3,
    depth, overhang, sink, sink_center, sink_center_r, sink_front, bs_back / bs_left / bs_right).
    labels: wall names to print instead of Back wall / Left wall... (cabinet order).  boxes=False: just the
    shape with the lengths as text (no empty measurement boxes)."""
    shape = norm(shape)
    labels = labels or WALL_NAMES[shape]
    A = _n(v.get("length"), 96.0)
    B = _n(v.get("length2"), 60.0 if shape != "Mirrored" else A)
    C = _n(v.get("length3"), 60.0)
    d = _n(v.get("depth"), 25.5)
    # outline of each run in inches: (x0, y0, x1, y1), plus walls [(x0, y0, x1, y1)] (lines)
    runs, walls = [(0, 0, A, d)], [(0, 0, A, 0)]
    if shape == "Straight":
        walls += [(0, 0, 0, d + 4), (A, 0, A, d + 4)]
        tw, th = A, d
    elif shape == "Mirrored":
        y2 = d + AISLE
        runs.append((0, y2, B, y2 + d))
        walls.append((0, y2 + d, B, y2 + d))
        tw, th = max(A, B), y2 + d
    elif shape == "L-Shape":
        runs.append((0, d, d, max(B, d)))
        walls += [(0, 0, 0, max(B, d))]
        tw, th = A, max(B, d)
    else:   # U
        runs.append((0, d, d, max(B, d)))
        runs.append((A - d, d, A, max(C, d)))
        walls += [(0, 0, 0, max(B, d)), (A, 0, A, max(C, d))]
        tw, th = A, max(B, C, d)
    pad_l, pad_r, pad_t, pad_b = 104, 112, 34, 30
    s = min((W - pad_l - pad_r) / tw, (H - pad_t - pad_b) / th)
    ox = pad_l + ((W - pad_l - pad_r) - tw * s) / 2
    oy = pad_t + ((H - pad_t - pad_b) - th * s) / 2
    X, Y = (lambda i: ox + i * s), (lambda i: oy + i * s)
    P = []
    for x0, y0, x1, y1 in runs:
        P.append(("rect", X(x0), Y(y0), X(x1), Y(y1), INK, TOP, 1.1))
    for x0, y0, x1, y1 in walls:     # walls: thick dark bands on the outside of the runs
        if y0 == y1:
            yy = Y(y0) - 3.5 if y0 == 0 else Y(y0) + 3.5
            P.append(("rect", X(x0) - 3.5, yy - 3.5, X(x1) + 3.5, yy + 3.5, None, WALL, 0))
        else:
            xx = X(x0) - 3.5 if x0 == 0 else X(x0) + 3.5
            P.append(("rect", xx - 3.5, Y(y0), xx + 3.5, Y(y1), None, WALL, 0))
    fs = 6.5

    def box(cx, cy, val, label, wdt=58, label_at="above"):
        val = str(val or "").strip()
        if not boxes and not val:
            return
        x0, x1 = cx - wdt / 2, cx + wdt / 2
        P.append(("rect", x0, cy - 6.5, x1, cy + 6.5, INK, (1, 1, 1), 0.8))
        if val:
            P.append(("text", cx, cy + 3, val + ('"' if val and val[-1].isdigit() else ""), 8.5, VAL, "middle", True))
        if label:
            ly = cy - 8.5 if label_at == "above" else cy + 14.5
            P.append(("text", cx, ly, label, fs, GREY, "middle", False))

    # back wall length over the back wall; the other walls' lengths in the left / right columns
    box(X(A / 2), Y(0) - 18, v.get("length"), f"{labels[0]} length")
    lx, rx = pad_l / 2, W - pad_r / 2
    if shape == "Mirrored":
        box(lx, Y(d + AISLE + d / 2) + 4, v.get("length2"), f"{labels[1]} length", wdt=62)
        P.append(("text", X(max(A, B) / 2), Y(d + AISLE / 2) + 2, "walkway", fs, GREY, "middle", False))
    if shape in ("L-Shape", "U-Shape"):
        box(lx, Y(max(B, d) / 2 + d / 2) + 4, v.get("length2"), f"{labels[1]} length", wdt=62)
    # depth (back wall to front edge) and front overhang: right column, top
    box(rx, pad_t + 6, v.get("depth"), "Depth (wall to front edge)", wdt=58)
    box(rx, pad_t + 42, v.get("overhang"), "Front overhang", wdt=46)
    if shape == "U-Shape":
        box(rx, pad_t + 80, v.get("length3"), f"{labels[2]} length", wdt=62)
    if shape != "U-Shape":
        P.append(("text", X(A / 2), Y(d) + 8, "FRONT EDGE", fs, GREY, "middle", True))
    # sink on the back run
    sink = str(v.get("sink") or "")
    circles = []
    if sink and sink != "None":
        sl = num(v.get("sink_center"))
        sr = num(v.get("sink_center_r"))
        cxi = sl if sl is not None else (A - sr if sr is not None else A / 2)
        cxi = min(max(cxi, (d + 6) if shape in ("L-Shape", "U-Shape") else 6), A - (d + 6 if shape == "U-Shape" else 6))
        r = min(min(d * 0.35, 10) * s, 16)
        circles.append(("circle", X(cxi), Y(d / 2), r, INK, (1, 1, 1), 1.0))
        circles.append(("text", X(cxi), Y(d / 2) + 2, "SINK", 5.5, GREY, "middle", True))
        parts = []
        if v.get("sink_center"):
            parts.append(f"center to left wall {v['sink_center']}\"")
        if v.get("sink_center_r"):
            parts.append(f"to right wall {v['sink_center_r']}\"")
        if v.get("sink_front"):
            parts.append(f"to front edge {v['sink_front']}\"")
        if parts:
            circles.append(("text", 6, H - 17, "Sink: " + ", ".join(parts), 6.8, VAL, "start", True))
    P += circles
    # backsplash per wall (never the front edge)
    bs = []
    for key, nm in (("bs_back", labels[0]), ("bs_left", labels[1] if len(labels) > 1 and shape != "Mirrored" else "Left end"),
                    ("bs_right", labels[2] if len(labels) > 2 else ("Facing wall" if shape == "Mirrored" else "Right end"))):
        if str(v.get(key) or "").lower().startswith("y"):
            bs.append(nm)
    if boxes:
        P.append(("text", 6, H - 6, "Backsplash: " + (", ".join(bs) if bs else "none checked"), 6.8,
                  INK if bs else GREY, "start", bool(bs)))
    P.append(("text", W - 6, H - 6, f"Kitchen shape: {shape}" + (f" ({SHAPE_HELP[shape]})" if shape != "Straight" else ""),
              6.8, INK, "end", True))
    return P


def to_pdf(page, prims, ox, oy):
    """cabinet_layout.to_pdf plus circles and start / end-anchored text, drawn in order."""
    import pymupdf as fitz
    from .cabinet_layout import to_pdf as base
    for p in prims:
        if p[0] == "circle":
            _, cx, cy, r, stroke, fill, w = p
            page.draw_circle((cx + ox, cy + oy), r, color=stroke, fill=fill, width=w)
        elif p[0] == "text" and p[6] in ("end", "start"):
            _, a, b, s_, size, color, anchor, bold = p
            font = "hebo" if bold else "helv"
            tw = fitz.get_text_length(str(s_), fontname=font, fontsize=size)
            page.insert_text((a - (tw if anchor == "end" else 0) + ox, b + oy), str(s_), fontsize=size,
                             fontname=font, color=color)
        else:
            base(page, [p], ox, oy)


def svg(prims, W, H):
    from .cabinet_layout import svg as base
    head = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:g} {H:g}" width="100%" '
    out = []
    for p in prims:
        if p[0] == "circle":
            _, cx, cy, r, stroke, fill, w = p
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" stroke="black" fill="white" stroke-width="{w}"/>')
        else:
            one = base([p], W, H)
            out.append(one[one.index(">") + 1:one.rindex("</svg>")])
    return head + 'style="background:#fff;font-family:Helvetica,Arial,sans-serif">' + "".join(out) + "</svg>"


def countertop_svg(shape, v, W=560, H=200):
    return svg(plan_prims(shape, v, W, H), W, H)


def redraw_countertop(page, area, shape, v):
    """Countertop Order Form, one line: the straight drawing is covered and this shape is drawn in its place
    with what was measured."""
    import pymupdf as fitz
    page.draw_rect(fitz.Rect(area), color=None, fill=(1, 1, 1), overlay=True)
    to_pdf(page, plan_prims(shape, v, area.width, area.height), area.x0, area.y0)


def layout_runs(d):
    """Cabinet layout -> the counter-run values for the overview drawing (each wall's lower boxes, left to right)."""
    from .cabinet_layout import dims
    vals, names = {}, []
    for key, w in zip(("length", "length2", "length3"), d.get("walls") or []):
        total = sum(dims("lowers", b)[0] for b in w.get("lowers") or [])
        vals[key] = fin(total).rstrip('"') if total else ""
        names.append(w.get("name") or "")
    depths = [dims("lowers", b)[2] for w in d.get("walls") or [] for b in w.get("lowers") or []]
    vals["depth"] = fin(max(depths)).rstrip('"') if depths else ""
    return vals, names
