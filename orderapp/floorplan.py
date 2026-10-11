"""Floor plan drawings: rooms + doors / windows / closets / cabinets on their walls.

The plan is stored as JSON on the floorplan (floorplans.plan). Every item on it is also saved as
a measurement for that floorplan, so it counts in "Measurements on file" and can fill order forms.
"""
import json

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


def _clean(plan):
    """Keep only what we draw; numbers as numbers, text trimmed."""
    out = empty_plan()
    for r in plan.get("rooms", [])[:200]:
        try:
            out["rooms"].append({"id": int(r["id"]), "n": str(r.get("n", "Room"))[:60],
                                 "x": float(r["x"]), "y": float(r["y"]), "w": max(1.0, float(r["w"])),
                                 "h": max(1.0, float(r["h"]))})
        except (KeyError, TypeError, ValueError):
            continue
    ids = {r["id"] for r in out["rooms"]}
    for it in plan.get("items", [])[:500]:
        try:
            if int(it["room"]) not in ids or it.get("t") not in ("door", "window", "patio", "bypass", "bifold", "cab"):
                continue
            d = {"id": int(it["id"]), "room": int(it["room"]), "t": it["t"], "wall": it.get("wall", "N")[:1],
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
                    "material": p.get("material", ""), "core": p.get("core", ""),
                    "swing": {"Left": "Left Hand", "Right": "Right Hand"}.get(p.get("hinge", ""), ""),
                    "door_location": p.get("location", "Interior"), "finish": p.get("finish", "")}
            out.append((prod, room, data))
        elif t == "window":
            cov = p.get("covering", "")
            if cov in COVERING:
                out.append((COVERING[cov][0], room, {"width": w, "height": p.get("height", ""),
                                                     "style": p.get("cov_style", ""), "color": p.get("color", "")}))
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
