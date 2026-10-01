import re

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..catalog import PRODUCTS, product_fields
from ..changes import gap_fill_from_history
from ..db import CUSTOMER_FIELDS, dumps, loads, now, q, x
from ..security import login_required

bp = Blueprint("customers", __name__)


def _cust_or_404(cid):
    c = q("SELECT * FROM customers WHERE id=?", (cid,), one=True)
    if not c:
        abort(404)
    return c


def missing_customer_info(c, contacts):
    miss = [f for f in ("acct", "address", "city", "state", "zip", "mgmt", "phone") if not c[f]]
    if not c["email"] and not any(k["email"] for k in contacts):
        miss.append("email")
    return miss


@bp.route("/customers")
@login_required
def index():
    s = request.args.get("q", "").strip()
    sql = """SELECT c.*, (SELECT COUNT(*) FROM units u WHERE u.customer_id=c.id) AS n_units,
             (SELECT COUNT(*) FROM orders o WHERE o.customer_id=c.id) AS n_orders,
             (SELECT MAX(o.order_date) FROM orders o WHERE o.customer_id=c.id) AS last_order
             FROM customers c"""
    args = ()
    if s:
        like = f"%{s}%"
        sql += """ WHERE c.name LIKE ? OR c.acct LIKE ? OR c.mgmt LIKE ? OR c.city LIKE ? OR c.address LIKE ?
                   OR c.id IN (SELECT customer_id FROM contacts WHERE name LIKE ? OR email LIKE ?)"""
        args = (like,) * 7
    sql += " ORDER BY c.name COLLATE NOCASE"
    return render_template("customers.html", customers=q(sql, args), search=s)


@bp.route("/customers/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "POST":
        vals = {f: request.form.get(f, "").strip() for f in CUSTOMER_FIELDS}
        if not vals["name"]:
            flash("Name is required.", "error")
            return render_template("customer_form.html", c=vals, new=True)
        cid = x(f"INSERT INTO customers({','.join(CUSTOMER_FIELDS)}, created, updated) "
                f"VALUES ({','.join('?' * len(CUSTOMER_FIELDS))}, ?, ?)",
                [vals[f] for f in CUSTOMER_FIELDS] + [now(), now()])
        flash("Customer added.", "ok")
        return redirect(url_for("customers.view", cid=cid))
    return render_template("customer_form.html", c={}, new=True)


@bp.route("/customers/<int:cid>")
@login_required
def view(cid):
    c = _cust_or_404(cid)
    contacts = q("SELECT * FROM contacts WHERE customer_id=? ORDER BY name", (cid,))
    fps = q("""SELECT f.*, (SELECT COUNT(*) FROM units u WHERE u.floorplan_id=f.id) AS n_units,
               (SELECT COUNT(*) FROM measurements m WHERE m.floorplan_id=f.id AND m.unit_id IS NULL) AS n_meas
               FROM floorplans f WHERE customer_id=? ORDER BY name""", (cid,))
    units = q("""SELECT u.*, f.name AS fp_name,
                 (SELECT COUNT(*) FROM measurements m WHERE m.unit_id=u.id) AS n_own
                 FROM units u LEFT JOIN floorplans f ON f.id=u.floorplan_id
                 WHERE u.customer_id=? ORDER BY u.building, length(u.unit_number), u.unit_number""", (cid,))
    general = q("SELECT * FROM measurements WHERE customer_id=? AND floorplan_id IS NULL AND unit_id IS NULL",
                (cid,))
    orders = q("SELECT * FROM orders WHERE customer_id=? ORDER BY coalesce(order_date, created) DESC LIMIT 50",
               (cid,))
    return render_template("customer.html", c=c, contacts=contacts, fps=fps, units=units, orders=orders,
                           general=_meas_rows(general), missing=missing_customer_info(c, contacts),
                           tab=request.args.get("tab", "info"))


@bp.route("/customers/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def edit(cid):
    c = _cust_or_404(cid)
    if request.method == "POST":
        vals = {f: request.form.get(f, "").strip() for f in CUSTOMER_FIELDS}
        if not vals["name"]:
            flash("Name is required.", "error")
            return render_template("customer_form.html", c=vals, new=False, cid=cid)
        x(f"UPDATE customers SET {', '.join(f + '=?' for f in CUSTOMER_FIELDS)}, updated=? WHERE id=?",
          [vals[f] for f in CUSTOMER_FIELDS] + [now(), cid])
        flash("Saved.", "ok")
        return redirect(url_for("customers.view", cid=cid))
    return render_template("customer_form.html", c=c, new=False, cid=cid)


@bp.route("/customers/<int:cid>/delete", methods=["POST"])
@login_required
def delete(cid):
    c = _cust_or_404(cid)
    if request.form.get("confirm_name", "").strip() != c["name"]:
        flash("Type the customer name exactly to confirm deleting.", "error")
        return redirect(url_for("customers.view", cid=cid))
    x("DELETE FROM customers WHERE id=?", (cid,))
    flash(f"Deleted {c['name']}.", "ok")
    return redirect(url_for("customers.index"))


@bp.route("/customers/<int:cid>/check", methods=["POST"])
@login_required
def check(cid):
    _cust_or_404(cid)
    b = gap_fill_from_history(cid)
    if b:
        flash("Found missing info in previous orders - review it below.", "ok")
        return redirect(url_for("imports.review_batch", bid=b))
    flash("Nothing new found in previous orders for the missing fields.", "info")
    return redirect(url_for("customers.view", cid=cid))


# ---------------- contacts ----------------
@bp.route("/customers/<int:cid>/contacts", methods=["POST"])
@login_required
def contact_add(cid):
    _cust_or_404(cid)
    f = {k: request.form.get(k, "").strip() for k in ("name", "role", "email", "phone", "notes")}
    if not any(f.values()):
        flash("Enter at least a name, email or phone.", "error")
    else:
        x("INSERT INTO contacts(customer_id, name, role, email, phone, notes) VALUES (?,?,?,?,?,?)",
          (cid, f["name"], f["role"], f["email"], f["phone"], f["notes"]))
    return redirect(url_for("customers.view", cid=cid, tab="info"))


@bp.route("/contacts/<int:kid>/edit", methods=["POST"])
@login_required
def contact_edit(kid):
    k = q("SELECT * FROM contacts WHERE id=?", (kid,), one=True) or abort(404)
    f = {k2: request.form.get(k2, "").strip() for k2 in ("name", "role", "email", "phone", "notes")}
    x("UPDATE contacts SET name=?, role=?, email=?, phone=?, notes=? WHERE id=?",
      (f["name"], f["role"], f["email"], f["phone"], f["notes"], kid))
    return redirect(url_for("customers.view", cid=k["customer_id"], tab="info"))


@bp.route("/contacts/<int:kid>/delete", methods=["POST"])
@login_required
def contact_delete(kid):
    k = q("SELECT * FROM contacts WHERE id=?", (kid,), one=True) or abort(404)
    x("DELETE FROM contacts WHERE id=?", (kid,))
    return redirect(url_for("customers.view", cid=k["customer_id"], tab="info"))


# ---------------- floorplans ----------------
@bp.route("/customers/<int:cid>/floorplans", methods=["POST"])
@login_required
def floorplan_add(cid):
    _cust_or_404(cid)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Floorplan name is required.", "error")
    elif q("SELECT 1 FROM floorplans WHERE customer_id=? AND lower(name)=lower(?)", (cid, name), one=True):
        flash("That floorplan already exists.", "error")
    else:
        fid = x("INSERT INTO floorplans(customer_id, name, beds, baths, sqft, notes) VALUES (?,?,?,?,?,?)",
                (cid, name, request.form.get("beds", ""), request.form.get("baths", ""),
                 request.form.get("sqft", ""), request.form.get("notes", "")))
        return redirect(url_for("customers.floorplan", fid=fid))
    return redirect(url_for("customers.view", cid=cid, tab="layouts"))


def _meas_rows(rows):
    out = []
    for r in rows:
        d = loads(r["data"])
        summary = ", ".join(f"{k}: {v}" for k, v in d.items() if v and k not in ("width", "height", "qty"))
        dims = " x ".join(str(d[k]) for k in ("width", "height") if d.get(k))
        out.append({"row": r, "data": d, "label": PRODUCTS.get(r["product"], {}).get("label", r["product"]),
                    "dims": dims, "qty": d.get("qty", ""), "summary": summary})
    return out


@bp.route("/floorplans/<int:fid>", methods=["GET", "POST"])
@login_required
def floorplan(fid):
    fp = q("SELECT * FROM floorplans WHERE id=?", (fid,), one=True) or abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip() or fp["name"]
        x("UPDATE floorplans SET name=?, beds=?, baths=?, sqft=?, notes=? WHERE id=?",
          (name, request.form.get("beds", ""), request.form.get("baths", ""), request.form.get("sqft", ""),
           request.form.get("notes", ""), fid))
        flash("Floorplan saved.", "ok")
        return redirect(url_for("customers.floorplan", fid=fid))
    c = _cust_or_404(fp["customer_id"])
    meas = q("SELECT * FROM measurements WHERE floorplan_id=? AND unit_id IS NULL ORDER BY product, room", (fid,))
    units = q("SELECT * FROM units WHERE floorplan_id=? ORDER BY length(unit_number), unit_number", (fid,))
    others = q("SELECT * FROM floorplans WHERE customer_id=? AND id<>? ORDER BY name", (c["id"], fid))
    return render_template("floorplan.html", fp=fp, c=c, meas=_meas_rows(meas), units=units, others=others)


@bp.route("/floorplans/<int:fid>/delete", methods=["POST"])
@login_required
def floorplan_delete(fid):
    fp = q("SELECT * FROM floorplans WHERE id=?", (fid,), one=True) or abort(404)
    x("DELETE FROM floorplans WHERE id=?", (fid,))
    flash(f"Deleted floorplan {fp['name']} (units kept, now without a floorplan).", "ok")
    return redirect(url_for("customers.view", cid=fp["customer_id"], tab="layouts"))


@bp.route("/floorplans/<int:fid>/copy_from", methods=["POST"])
@login_required
def floorplan_copy(fid):
    """Copy all measurements from another floorplan (handy for mirrored layouts)."""
    fp = q("SELECT * FROM floorplans WHERE id=?", (fid,), one=True) or abort(404)
    src = int(request.form.get("source_id", 0))
    n = 0
    for m in q("SELECT * FROM measurements WHERE floorplan_id=? AND unit_id IS NULL", (src,)):
        x("""INSERT INTO measurements(customer_id, floorplan_id, product, room, data, verified, notes, updated)
             VALUES (?,?,?,?,?,0,?,?)""", (fp["customer_id"], fid, m["product"], m["room"], m["data"],
                                           m["notes"], now()))
        n += 1
    flash(f"Copied {n} measurement(s). They are marked unverified until you check them.", "ok")
    return redirect(url_for("customers.floorplan", fid=fid))


# ---------------- units ----------------
def expand_units(text):
    """'101-110, 201, 305A' -> ['101', ..., '110', '201', '305A']"""
    out = []
    for part in re.split(r"[,\n;]+", text or ""):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"([A-Za-z]*)(\d+)\s*-\s*\1?(\d+)", part)
        if m and int(m.group(3)) >= int(m.group(2)) and int(m.group(3)) - int(m.group(2)) <= 2000:
            pre, a, b = m.group(1), int(m.group(2)), int(m.group(3))
            width = len(m.group(2))
            out += [f"{pre}{str(i).zfill(width)}" for i in range(a, b + 1)]
        else:
            out.append(part)
    return out


@bp.route("/customers/<int:cid>/units", methods=["POST"])
@login_required
def units_add(cid):
    _cust_or_404(cid)
    nums = expand_units(request.form.get("units", ""))
    fid = request.form.get("floorplan_id") or None
    bldg = request.form.get("building", "").strip()
    added = updated = 0
    for n in nums:
        ex = q("SELECT * FROM units WHERE customer_id=? AND lower(unit_number)=lower(?)", (cid, n), one=True)
        if ex:
            if fid:
                x("UPDATE units SET floorplan_id=? WHERE id=?", (fid, ex["id"]))
                updated += 1
        else:
            x("INSERT INTO units(customer_id, unit_number, building, floorplan_id) VALUES (?,?,?,?)",
              (cid, n, bldg, fid))
            added += 1
    flash(f"Added {added} unit(s)" + (f", updated floorplan on {updated}" if updated else "") + ".", "ok")
    return redirect(url_for("customers.view", cid=cid, tab="units"))


@bp.route("/units/<int:uid>", methods=["GET", "POST"])
@login_required
def unit(uid):
    u = q("SELECT * FROM units WHERE id=?", (uid,), one=True) or abort(404)
    if request.method == "POST":
        x("UPDATE units SET unit_number=?, building=?, floorplan_id=?, notes=? WHERE id=?",
          (request.form.get("unit_number", u["unit_number"]).strip() or u["unit_number"],
           request.form.get("building", ""), request.form.get("floorplan_id") or None,
           request.form.get("notes", ""), uid))
        flash("Unit saved.", "ok")
        return redirect(url_for("customers.unit", uid=uid))
    c = _cust_or_404(u["customer_id"])
    fp = q("SELECT * FROM floorplans WHERE id=?", (u["floorplan_id"],), one=True) if u["floorplan_id"] else None
    inherited = q("SELECT * FROM measurements WHERE floorplan_id=? AND unit_id IS NULL ORDER BY product, room",
                  (u["floorplan_id"],)) if u["floorplan_id"] else []
    own = q("SELECT * FROM measurements WHERE unit_id=? ORDER BY product, room", (uid,))
    fps = q("SELECT * FROM floorplans WHERE customer_id=? ORDER BY name", (c["id"],))
    orders = q("SELECT * FROM orders WHERE customer_id=? AND data LIKE ? ORDER BY id DESC LIMIT 20",
               (c["id"], f'%"unit": "{u["unit_number"]}"%'))
    return render_template("unit.html", u=u, c=c, fp=fp, inherited=_meas_rows(inherited), own=_meas_rows(own),
                           fps=fps, orders=orders)


@bp.route("/units/<int:uid>/delete", methods=["POST"])
@login_required
def unit_delete(uid):
    u = q("SELECT * FROM units WHERE id=?", (uid,), one=True) or abort(404)
    x("DELETE FROM units WHERE id=?", (uid,))
    flash(f"Deleted unit {u['unit_number']}.", "ok")
    return redirect(url_for("customers.view", cid=u["customer_id"], tab="units"))


# ---------------- measurements ----------------
def _all_product_fields():
    return {k: {"label": v["label"], "fields": product_fields(k)} for k, v in PRODUCTS.items()}


@bp.route("/measurements/new", methods=["GET", "POST"])
@bp.route("/measurements/<int:mid>", methods=["GET", "POST"])
@login_required
def measurement(mid=None):
    m = q("SELECT * FROM measurements WHERE id=?", (mid,), one=True) if mid else None
    if mid and not m:
        abort(404)
    cid = m["customer_id"] if m else (request.values.get("customer_id", type=int) or 0)
    c = _cust_or_404(cid)
    fid = (m["floorplan_id"] if m else request.values.get("floorplan_id", type=int)) or None
    uid = (m["unit_id"] if m else request.values.get("unit_id", type=int)) or None
    if fid and not q("SELECT 1 FROM floorplans WHERE id=? AND customer_id=?", (fid, cid), one=True):
        abort(404)
    if uid and not q("SELECT 1 FROM units WHERE id=? AND customer_id=?", (uid, cid), one=True):
        abort(404)
    back = url_for("customers.unit", uid=uid) if uid else (
        url_for("customers.floorplan", fid=fid) if fid else url_for("customers.view", cid=cid, tab="layouts"))
    if request.method == "POST":
        product = request.form.get("product", "other")
        data = {}
        for k, v in request.form.items():
            if k.startswith("f_") and v.strip():
                data[k[2:]] = v.strip()
        vals = (product, request.form.get("room", "").strip(), dumps(data),
                1 if request.form.get("verified") else 0, request.form.get("notes", "").strip(), now())
        if m:
            x("UPDATE measurements SET product=?, room=?, data=?, verified=?, notes=?, updated=? WHERE id=?",
              vals + (mid,))
        else:
            x("""INSERT INTO measurements(product, room, data, verified, notes, updated, customer_id, floorplan_id,
                 unit_id) VALUES (?,?,?,?,?,?,?,?,?)""", vals + (cid, fid, uid))
        flash("Measurement saved.", "ok")
        if request.form.get("again"):
            return redirect(url_for("customers.measurement", customer_id=cid, floorplan_id=fid or "",
                                    unit_id=uid or "", product=product))
        return redirect(back)
    where = None
    if uid:
        u = q("SELECT unit_number FROM units WHERE id=?", (uid,), one=True)
        where = f"Unit {u['unit_number']} (this unit only)"
    elif fid:
        f = q("SELECT name FROM floorplans WHERE id=?", (fid,), one=True)
        where = f"Floorplan {f['name']} (all units with this layout)"
    else:
        where = "Whole property (not tied to a layout)"
    rooms = [r["room"] for r in q("SELECT DISTINCT room FROM measurements WHERE room<>'' ORDER BY room")]
    return render_template("measurement.html", m=m, data=loads(m["data"]) if m else {}, c=c, where=where,
                           products=_all_product_fields(), back=back, fid=fid, uid=uid,
                           sel_product=(m["product"] if m else request.args.get("product", "interior_door")),
                           rooms=rooms)


@bp.route("/measurements/<int:mid>/delete", methods=["POST"])
@login_required
def measurement_delete(mid):
    m = q("SELECT * FROM measurements WHERE id=?", (mid,), one=True) or abort(404)
    x("DELETE FROM measurements WHERE id=?", (mid,))
    flash("Measurement deleted.", "ok")
    if m["unit_id"]:
        return redirect(url_for("customers.unit", uid=m["unit_id"]))
    if m["floorplan_id"]:
        return redirect(url_for("customers.floorplan", fid=m["floorplan_id"]))
    return redirect(url_for("customers.view", cid=m["customer_id"], tab="layouts"))
