"""Order sizes -> the property's units, automatically (no button, no review step, no duplicates).

Every saved order with a unit # and a size on a line stores that size on the unit, so the next order for
that unit fills itself in. The same product + room + size already on the unit is never added twice; a new
color/style for the same size just updates the saved one. A different size in the same room is kept as
another window/door (units often have two bedroom windows).
"""
import re

from .db import dumps, loads, now, q, setting, set_setting, x

SKIP = ("unit", "line_comments", "_room", "_product", "price", "product", "description", "amount", "notes", "qty",
        "item_no", "room")
SIZE_KEYS = ("width", "height", "length", "length_b", "length_c", "depth", "return_depth", "jamb", "rough_width",
             "rough_height", "opening_width", "opening_height", "mirror_length", "mirror_height")


def _n(v):
    s = str(v or "").lower().replace('"', "").replace("in", "").strip()
    return re.sub(r"\s+", " ", s)


def _size(d):
    return tuple((k, _n(d.get(k))) for k in SIZE_KEYS if d.get(k))


def save_order_sizes(oid):
    """Store an order's line sizes on its units. Returns (added, updated)."""
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    if not o or not o["customer_id"] or o["status"] == "Cancelled":
        return 0, 0
    from .catalog import products_for_form
    from .changes import _unit_id
    from .importer import guess_product
    cid = o["customer_id"]
    prods = products_for_form(o["form_key"]) if o["form_key"] else []
    added = updated = 0
    note = f"From order #{oid}"
    mine = {r["id"] for r in q("SELECT id FROM measurements WHERE customer_id=? AND notes=?", (cid, note))}
    touched = set()
    for blk in (loads(o["data"]) or {}).get("blocks") or []:
        if not isinstance(blk, dict):
            continue
        unit = str(blk.get("unit") or "").strip()
        if not unit:
            continue
        d = {k: v for k, v in blk.items() if k not in SKIP and not k.startswith("_") and v not in ("", None)}
        size = _size(d)
        if not size:
            continue
        product = blk.get("product") or guess_product(blk.get("_product", "")) or (prods[0] if prods else "other")
        room = (blk.get("room") or blk.get("_room") or "").strip()
        uid = _unit_id(cid, unit)
        same = None
        for m in q("SELECT * FROM measurements WHERE unit_id=? AND product=? AND lower(coalesce(room,''))=lower(?)",
                   (uid, product, room)):
            if _size(loads(m["data"]) or {}) == size:
                same = m
                break
        if same:
            touched.add(same["id"])
            old = loads(same["data"]) or {}
            new = dict(old, **d)
            if new != old:
                x("UPDATE measurements SET data=?, notes=?, updated=? WHERE id=?", (dumps(new), note, now(), same["id"]))
                updated += 1
            elif same["notes"] != note and not same["verified"]:
                x("UPDATE measurements SET notes=? WHERE id=?", (note, same["id"]))   # newest order vouches for it
            continue
        touched.add(x("""INSERT INTO measurements(customer_id, floorplan_id, unit_id, product, room, data, verified,
                         notes, updated) VALUES (?,?,?,?,?,?,0,?,?)""",
                      (cid, None, uid, product, room, dumps(d), note, now())))
        added += 1
    # a size fixed on this order (typo) replaces the one this order saved before
    for mid in mine - touched:
        x("DELETE FROM measurements WHERE id=? AND notes=? AND coalesce(verified,0)=0", (mid, note))
    return added, updated


def backfill_once():
    """First run after the update: store sizes from orders already in the app (once)."""
    if setting("unit_sizes_backfilled") == "1":
        return 0
    n = 0
    for o in q("SELECT id FROM orders WHERE customer_id IS NOT NULL AND status<>'Cancelled' ORDER BY id"):
        try:
            n += sum(save_order_sizes(o["id"]))
        except Exception:  # noqa: BLE001 - never block start-up over one odd order
            pass
    set_setting("unit_sizes_backfilled", "1")
    return n
