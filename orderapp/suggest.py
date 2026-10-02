"""Smart suggestions: predict field values from saved measurements and order history."""
from collections import Counter, defaultdict

from .catalog import FORMS, match_option, products_for_form
from .db import loads, q

SKIP_KEYS = {"line_comments", "other_mods"}


def _collect(customer_id=None):
    """Counter of values per logical key from orders + measurements."""
    vals = defaultdict(Counter)
    if customer_id:
        orders = q("SELECT data FROM orders WHERE customer_id=?", (customer_id,))
        meas = q("SELECT data, room FROM measurements WHERE customer_id=?", (customer_id,))
    else:
        orders = q("SELECT data FROM orders ORDER BY id DESC LIMIT 500")
        meas = q("SELECT data, room FROM measurements ORDER BY id DESC LIMIT 2000")
    for o in orders:
        d = loads(o["data"])
        for b in d.get("blocks", []) or []:
            for k, v in b.items():
                if k not in SKIP_KEYS and v not in (None, ""):
                    vals[k][str(v).strip()] += 1
        for k, v in (d.get("header") or {}).items():
            if v and k in ("po",):
                vals["h_" + k][str(v).strip()] += 1
    for m in meas:
        d = loads(m["data"])
        for k, v in d.items():
            if v not in (None, ""):
                vals[k][str(v).strip()] += 1
        if m["room"]:
            vals["room"][m["room"]] += 1
    return vals


def suggestions(form_key, customer_id=None, limit=15):
    """{suggest: {key: [values...]}, defaults: {key: value}} for a form."""
    spec = FORMS[form_key]
    mine = _collect(customer_id) if customer_id else defaultdict(Counter)
    every = _collect(None)
    out, defaults = {}, {}
    for f in spec["blocks"][0]:
        k = f["key"]
        if k in SKIP_KEYS:
            continue
        seq = [v for v, _ in mine[k].most_common()] + [v for v, _ in every[k].most_common()]
        if f.get("suggest"):
            seq += f["suggest"]
        if f["kind"] == "choice":
            # map history onto this form's option labels
            mapped = []
            for v in seq:
                m = match_option(v, f["options"])
                if m and m not in mapped:
                    mapped.append(m)
            seq = mapped
            if mine[k]:
                top = match_option(mine[k].most_common(1)[0][0], f["options"])
                if top:
                    defaults[k] = top
        uniq = []
        for v in seq:
            if v not in uniq:
                uniq.append(v)
        out[k] = uniq[:limit]
    return {"suggest": out, "defaults": defaults}


def to_form_block(form_key, data, room=None, unit=None):
    """Translate saved measurement data into a block for this form."""
    fields = {f["key"]: f for f in FORMS[form_key]["blocks"][0]}
    blk = {}
    data = dict(data or {})
    # imported history only knows a generic "color": use it for this form's finish field
    if data.get("color") and "color" not in fields:
        for alt in ("frame_finish", "finish"):
            if alt in fields and not data.get(alt):
                data[alt] = data["color"]
                break
    for k, v in data.items():
        if v in (None, "") or k not in fields:
            continue
        f = fields[k]
        if f["kind"] == "choice":
            m = match_option(v, f["options"])
            if m:
                blk[k] = m
            elif f.get("other_option"):
                blk[k] = f["other_option"]
                if f.get("other_field") and f["other_field"] in fields:
                    blk[f["other_field"]] = v
            else:
                blk[k] = v  # shown in UI; printed in comments
        elif f["kind"] == "bool":
            blk[k] = "Yes" if str(v).lower() in ("yes", "y", "true", "1", "x") else ""
        else:
            blk[k] = v
    if room:
        if "room" in fields:
            f = fields["room"]
            if f["kind"] == "choice":
                m = match_option(room, f["options"])
                if m:
                    blk["room"] = m
                    if m == "Bedroom":
                        digits = "".join(ch for ch in room if ch.isdigit())
                        if digits and "bedroom_no" in fields:
                            blk["bedroom_no"] = digits
                else:
                    blk["room"] = "Other"
                    blk["room_other"] = room
            else:
                blk["room"] = room
        else:
            blk["_room"] = room
    if unit:
        blk["unit"] = unit
    return blk


def measurements_for_unit(customer_id, unit_number=None, floorplan_id=None, form_key=None):
    """Measurements for a unit (unit-specific records override its floorplan's)."""
    unit = None
    if unit_number:
        unit = q("SELECT * FROM units WHERE customer_id=? AND lower(unit_number)=lower(?)",
                 (customer_id, str(unit_number).strip()), one=True)
        if unit and not floorplan_id:
            floorplan_id = unit["floorplan_id"]
    rows = []
    if floorplan_id:
        rows += list(q("SELECT * FROM measurements WHERE floorplan_id=? AND unit_id IS NULL ORDER BY id",
                       (floorplan_id,)))
    if unit:
        own = list(q("SELECT * FROM measurements WHERE unit_id=? ORDER BY id", (unit["id"],)))
        keys = {(r["product"], (r["room"] or "").lower()) for r in own}
        rows = [r for r in rows if (r["product"], (r["room"] or "").lower()) not in keys] + own
    if form_key:
        prods = set(products_for_form(form_key))
        rows = [r for r in rows if r["product"] in prods]
    return unit, floorplan_id, rows


def missing_fields(form_key, data):
    """Which important slots are still empty (shown as a checklist before sending)."""
    spec = FORMS[form_key]
    miss = []
    h = data.get("header") or {}
    for k in ("name", "po", "date"):
        if k in spec["header"] and not h.get(k):
            miss.append(f"Header: {k.upper() if k == 'po' else k.title()}")
    for i, b in enumerate(data.get("blocks") or [], 1):
        if not any(v for v in b.values()):
            continue
        for f in spec["blocks"][0]:
            if f["key"] in ("qty", "width", "height") and not b.get(f["key"]):
                miss.append(f"Line {i}: {f['label']}")
    return miss
