import os

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from ..db import add_status, dumps, now, q, setting, x
from ..photos import add_photo
from ..security import login_required
from .orders import today

bp = Blueprint("photos", __name__)


def _save_uploads(oid):
    n, bad = 0, []
    caption = request.form.get("caption", "").strip()
    for f in request.files.getlist("photos"):
        if not f or not f.filename:
            continue
        try:
            add_photo(oid, f.filename, f.read(), caption)
            n += 1
        except ValueError as e:
            bad.append(str(e))
    for b in bad:
        flash(b, "error")
    return n


@bp.route("/orders/<int:oid>/photos", methods=["POST"])
@login_required
def upload(oid):
    q("SELECT id FROM orders WHERE id=?", (oid,), one=True) or abort(404)
    n = _save_uploads(oid)
    if n:
        add_status_note(oid, f"{n} photo(s) added")
        flash(f"Added {n} photo(s).", "ok")
    elif not request.files.getlist("photos"):
        flash("Choose or take at least one photo.", "error")
    return redirect(url_for("orders.view", oid=oid) + "#photos")


def add_status_note(oid, note):
    o = q("SELECT status FROM orders WHERE id=?", (oid,), one=True)
    add_status(oid, o["status"], note)
    x("UPDATE orders SET updated=? WHERE id=?", (now(), oid))


@bp.route("/photos/<int:pid>")
@login_required
def show(pid):
    p = q("SELECT * FROM order_photos WHERE id=?", (pid,), one=True) or abort(404)
    if not os.path.exists(p["path"]):
        abort(404)
    return send_file(p["path"], download_name=os.path.basename(p["path"]),
                     as_attachment=bool(request.args.get("dl")))


@bp.route("/photos/<int:pid>/delete", methods=["POST"])
@login_required
def delete(pid):
    p = q("SELECT * FROM order_photos WHERE id=?", (pid,), one=True) or abort(404)
    x("DELETE FROM order_photos WHERE id=?", (pid,))
    try:
        os.remove(p["path"])
    except OSError:
        pass
    flash("Photo removed.", "ok")
    return redirect(url_for("orders.view", oid=p["order_id"]) + "#photos")


@bp.route("/field-order", methods=["GET", "POST"])
@login_required
def field_order():
    """Snap photos of a paper form / sizes in the field and send them as an order."""
    customers = q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE")
    if request.method == "POST":
        files = [f for f in request.files.getlist("photos") if f and f.filename]
        if not files:
            flash("Take or choose at least one photo.", "error")
            return render_template("field_order.html", customers=customers, f=request.form)
        cid = request.form.get("customer_id", type=int)
        cname = ""
        if cid:
            c = q("SELECT name FROM customers WHERE id=?", (cid,), one=True)
            cname = c["name"] if c else ""
        what = request.form.get("what", "").strip() or "Field order"
        units = request.form.get("units", "").strip()
        notes = request.form.get("notes", "").strip()
        title = f"{what} (photos)"
        data = {"header": {"name": cname, "po": request.form.get("po", "").strip(), "date": today()},
                "units": units, "comments": notes}
        oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, notes,
                   created, updated) VALUES (?,?,?,?,?,'Draft',?,'photo',?,?,?)""",
                (cid, None, title, data["header"]["po"], today(), dumps(data),
                 "; ".join(x for x in [f"Units: {units}" if units else "", notes] if x), now(), now()))
        add_status(oid, "Draft", "Field order created from photos")
        n = _save_uploads(oid)
        flash(f"Saved field order #{oid} with {n} photo(s).", "ok")
        if request.form.get("action") == "send":
            return redirect(url_for("orders.send_page", oid=oid))
        return redirect(url_for("orders.view", oid=oid))
    return render_template("field_order.html", customers=customers, f={},
                           pre=request.args.get("customer", type=int))
