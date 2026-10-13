"""Floor plan drawings: rooms + doors / windows / closets / cabinets on their walls.

The plan is stored as JSON on the floorplan (floorplans.plan). Every item on it is also saved as
a measurement for that floorplan, so it counts in "Measurements on file" and can fill order forms.
"""
import json
import math

from .db import dumps, loads, now, q, x

TAG = "From floor plan"     # measurements made from the drawing carry this note (and get replaced on save)

# item type -> (default product, form to start an order with)
FORM_FOR = {"door": "door", "patio": "sliding_patio_door", "bypass": "closet_bifold", "bifold": "closet_bifold",
            "cab": "cabinet_prefab"}
COVERING = {"Horizontal Blind": ("horizontal_blind", "horizontal_blind"),
            "Vertical Blind": ("vertical_blind", "vertical_blind"),
            "Roller Shade": ("roller_shade", "roller_shade")}


def empty_plan():
    return {"rooms": [], "items": [], "v": 1}


def get_plan(fid):
    r = q("SELECT plan FROM floorplans WHERE id=?", (fid,), one=True)
    try:
        p = json.loads(r["plan"]) if r and r["plan"] else None
    except ValueError:
        p = None
    if not isinstance(p, dict):
        p = empty_plan()
    p.setdefault("rooms", [])
    p.setdefault("items", [])
    return p


CORNERS = ("NW", "NE", "SE", "SW")
WALLS = ("N", "E", "S", "W") + CORNERS
OPENINGS = ("door", "patio", "bypass", "bifold")          # no baseboard across these (either side of the wall)


def walls(r):
    """{wall: (x1, y1, x2, y2, ox, oy, dx, dy)} in feet. Straight-wall offsets are measured from the room's
    corner (left for top/bottom, top for left/right); angled walls from their own start."""
    x, y, w, h = r["x"], r["y"], r["w"], r["h"]
    c = r.get("cut") or {}
    nw, ne, se, sw = (float(c.get(k, 0) or 0) for k in CORNERS)
    out = {"N": (x + nw, y, x + w - ne, y, x, y, 1, 0), "S": (x + sw, y + h, x + w - se, y + h, x, y + h, 1, 0),
           "W": (x, y + nw, x, y + h - sw, x, y, 0, 1), "E": (x + w, y + ne, x + w, y + h - se, x + w, y, 0, 1)}
    diag = {"NW": (x, y + nw, x + nw, y, nw), "NE": (x + w - ne, y, x + w, y + ne, ne),
            "SE": (x + w, y + h - se, x + w - se, y + h, se), "SW": (x + sw, y + h, x, y + h - sw, sw)}
    for k, (x1, y1, x2, y2, cut) in diag.items():
        if cut > 0:
            L = math.hypot(x2 - x1, y2 - y1)
            out[k] = (x1, y1, x2, y2, x1, y1, (x2 - x1) / L, (y2 - y1) / L)
    return out


def _overlap(seg, wall):
    """Length of seg (x1,y1,x2,y2) lying on wall's line inside the wall."""
    x1, y1, x2, y2 = wall[:4]
    L = math.hypot(x2 - x1, y2 - y1)
    if L < 1e-6:
        return 0.0
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    def off_line(px, py):
        return abs((px - x1) * uy - (py - y1) * ux)
    if off_line(seg[0], seg[1]) > 0.15 or off_line(seg[2], seg[3]) > 0.15:
        return 0.0
    a = (seg[0] - x1) * ux + (seg[1] - y1) * uy
    b = (seg[2] - x1) * ux + (seg[3] - y1) * uy
    lo, hi = max(0.0, min(a, b)), min(L, max(a, b))
    return max(0.0, hi - lo)


def item_seg(rooms, it):
    r = rooms.get(it["room"])
    if not r:
        return None
    wl = walls(r).get(it["wall"])
    if not wl:
        return None
    ox, oy, dx, dy = wl[4:]
    a, b = it["off"], it["off"] + it["w"] / 12.0
    return (ox + dx * a, oy + dy * a, ox + dx * b, oy + dy * b)


def baseboard(plan):
    """{room id: linear feet} - walls that aren't open, minus doors/closet openings (on either side of the wall)
    and base cabinets (own room)."""
    rooms = {r["id"]: r for r in plan["rooms"]}
    segs = [(it, item_seg(rooms, it)) for it in plan["items"]]
    out = {}
    for r in plan["rooms"]:
        if r.get("bb") is False:
            continue
        lf = 0.0
        for k, wl in walls(r).items():
            if k in (r.get("open") or []):
                continue
            L = math.hypot(wl[2] - wl[0], wl[3] - wl[1])
            cut = 0.0
            for it, sg in segs:
                if not sg:
                    continue
                if it["t"] in OPENINGS or (it["t"] == "cab" and it["room"] == r["id"]
                                           and (it.get("p") or {}).get("kind") != "Wall"):
                    cut += _overlap(sg, wl)
            lf += max(0.0, L - cut)
        out[r["id"]] = round(lf, 1)
    return out


def _clean(plan):
    """Keep only what we draw; numbers as numbers, text trimmed."""
    out = empty_plan()
    for r in plan.get("rooms", [])[:200]:
        try:
            room = {"id": int(r["id"]), "n": str(r.get("n", "Room"))[:60],
                    "x": float(r["x"]), "y": float(r["y"]), "w": max(1.0, float(r["w"])),
                    "h": max(1.0, float(r["h"]))}
            if r.get("kind") == "hall":
                room["kind"] = "hall"
            lim = min(room["w"], room["h"]) / 2
            cut = {k: min(lim, max(0.0, float(v))) for k, v in (r.get("cut") or {}).items()
                   if k in CORNERS and v not in (None, "") and float(v) > 0}
            if cut:
                room["cut"] = cut
            opn = [k for k in (r.get("open") or []) if k in WALLS]
            if opn:
                room["open"] = opn
            if r.get("bb") is False:
                room["bb"] = False
            out["rooms"].append(room)
        except (KeyError, TypeError, ValueError):
            continue
    ids = {r["id"] for r in out["rooms"]}
    for it in plan.get("items", [])[:500]:
        try:
            if int(it["room"]) not in ids or it.get("t") not in ("door", "window", "patio", "bypass", "bifold", "cab"):
                continue
            d = {"id": int(it["id"]), "room": int(it["room"]), "t": it["t"], "wall": it.get("wall", "N") if it.get("wall") in WALLS else "N",
                 "off": float(it.get("off", 0)), "w": float(it.get("w", 32))}
            props = it.get("p") or {}
            d["p"] = {str(k)[:40]: str(v)[:120] for k, v in props.items() if v not in (None, "")}
            out["items"].append(d)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _fmt_in(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v or "")
    whole = int(f)
    eighths = round((f - whole) * 8)
    if eighths == 8:
        whole, eighths = whole + 1, 0
    frac = {0: "", 1: "1/8", 2: "1/4", 3: "3/8", 4: "1/2", 5: "5/8", 6: "3/4", 7: "7/8"}[eighths]
    return f"{whole} {frac}".strip()


def measurements_for(plan):
    """[(product, room, data, label)] for every item on the plan."""
    rooms = {r["id"]: r["n"] for r in plan["rooms"]}
    out = []
    for it in plan["items"]:
        p, room, t = it.get("p", {}), rooms.get(it["room"], ""), it["t"]
        w = _fmt_in(it["w"])
        if t == "door":
            prod = "entry_door" if p.get("location") == "Exterior" else "interior_door"
            data = {"width": w, "height": p.get("height", "80"), "style": p.get("style", ""),
                    "panels": p.get("panels", ""),
                    "material": p.get("material", ""), "core": p.get("core", ""),
                    "swing": {"Left": "Left Hand", "Right": "Right Hand"}.get(p.get("hinge", ""), ""),
                    "door_location": p.get("location", "Interior"), "finish": p.get("finish", "")}
            out.append((prod, room, data))
        elif t == "window":
            cov = p.get("covering", "")
            if cov in COVERING:
                out.append((COVERING[cov][0], room, {"width": w, "height": p.get("height", ""),
                                                     "style": p.get("cov_style", ""), "color": p.get("color", ""),
                                                     "slat_size": p.get("slat", "") if cov == "Horizontal Blind" else ""}))
            if p.get("screen") == "Yes":
                out.append(("window_screen", room, {"width": "", "height": "", "screen_type": "Bug Screen"}))
        elif t == "patio":
            out.append(("sliding_patio_door", room, {"width": w, "height": p.get("height", "80"),
                                                    "config": p.get("config", ""), "frame": p.get("frame", ""),
                                                    "frame_color": p.get("frame_color", "")}))
            if p.get("covering") == "Vertical Blind":
                out.append(("vertical_blind", room, {"width": w, "height": p.get("height", "80"),
                                                     "color": p.get("color", "")}))
        elif t in ("bypass", "bifold"):
            kind = "Bypass" if t == "bypass" else "Bi-Fold"
            out.append(("closet_door", room, {"width": w, "height": p.get("height", "80"),
                                              "closet_type": f"{kind} ({p.get('size', 'standard')})",
                                              "style": p.get("style", ""), "finish": p.get("finish", "")}))
        elif t == "cab":
            out.append(("cabinet", room, {"width": w, "depth": p.get("depth", ""), "height": p.get("height", ""),
                                          "cab_room": p.get("cab_room", ""), "cab_kind": p.get("kind", ""),
                                          "style": p.get("style", "")}))
            if p.get("countertop"):
                out.append(("countertop", room, {"length": w, "depth": p.get("ct_depth", "25 1/2"),
                                                 "ct_material": p.get("countertop", "")}))
    names = {r["id"]: r["n"] for r in plan["rooms"]}
    for rid, lf in baseboard(plan).items():
        if lf > 0:
            out.append(("baseboard", names[rid], {"trim_type": "Baseboard", "length": f"{lf:g}"}))
    return [(prod, room, {k: v for k, v in d.items() if v not in ("", None)}) for prod, room, d in out]


def save_plan(fid, plan):
    """Store the drawing and replace this floorplan's 'from floor plan' measurements. Returns count."""
    fp = q("SELECT * FROM floorplans WHERE id=?", (fid,), one=True)
    if not fp:
        return 0
    plan = _clean(plan)
    x("UPDATE floorplans SET plan=? WHERE id=?", (json.dumps(plan), fid))
    x("DELETE FROM measurements WHERE floorplan_id=? AND unit_id IS NULL AND notes LIKE ?", (fid, TAG + "%"))
    rows = measurements_for(plan)
    for prod, room, data in rows:
        x("""INSERT INTO measurements(customer_id, floorplan_id, unit_id, product, room, data, verified, notes, updated)
             VALUES (?,?,NULL,?,?,?,0,?,?)""", (fp["customer_id"], fid, prod, room, dumps(data), TAG, now()))
    return len(rows)
