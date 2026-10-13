"""Kitchen / bath measuring: lettered countertop runs, sinks, appliances, base + upper cabinets, diagram + PDF."""
import json
import os

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for

from .. import kitchen as K
from ..db import q, x
from ..paths import ensure_dir
from ..security import login_required

bp = Blueprint("kitchen", __name__)


def _cust(cid):
    return q("SELECT * FROM customers WHERE id=?", (cid,), one=True) if cid else None


@bp.route("/kitchen")
@login_required
def index():
    cid = request.args.get("customer", type=int)
    rows = q("""SELECT k.*, c.name cname FROM kitchen_layouts k LEFT JOIN customers c ON c.id=k.customer_id
                WHERE (? IS NULL OR k.customer_id=?) ORDER BY k.updated DESC LIMIT 200""", (cid, cid))
    return render_template("kitchen_list.html", rows=rows, c=_cust(cid), shapes=K.SHAPES,
                           customers=q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE"))


@bp.route("/kitchen/new", methods=["POST"])
@login_required
def new():
    f = request.form
    cid = f.get("customer_id", type=int)
    lid = K.create(cid, f.get("name", "").strip() or "Kitchen", f.get("unit", "").strip(),
                   f.get("floorplan_id", type=int), f.get("shape", "l"), f.get("room", "Kitchen"))
    return redirect(url_for("kitchen.edit", lid=lid))


@bp.route("/kitchen/<int:lid>", methods=["GET", "POST"])
@login_required
def edit(lid):
    lay = K.get(lid) or abort(404)
    if request.method == "POST":
        j = request.get_json(silent=True)
        if j is None:
            j = json.loads(request.form.get("payload") or "{}")
        data = K.save(lid, j.get("data") or {}, j.get("name"), j.get("unit"))
        return jsonify({"ok": True, "pieces": K.pieces(data), "sqft": K.sqft(data), "svg": K.top_svg(data)})
    c = _cust(lay["customer_id"])
    return render_template("kitchen_edit.html", lay=lay, c=c, K=K, svg=K.top_svg(lay["data"]),
                           pieces=K.pieces(lay["data"]))


@bp.route("/kitchen/<int:lid>/preview", methods=["POST"])
@login_required
def preview(lid):
    j = request.get_json(silent=True) or {}
    data = K.normalize(j.get("data") or {})
    return jsonify({"svg": K.top_svg(data), "pieces": K.pieces(data), "sqft": K.sqft(data),
                    "elev": {k: K.draw_elevation(None, data, k, (0, 0, 640, 170), mode="svg")
                             for k in K.active_runs(data)}})


@bp.route("/kitchen/<int:lid>/pdf")
@login_required
def pdf(lid):
    lay = K.get(lid) or abort(404)
    c = _cust(lay["customer_id"])
    from .orders import output_dir, safe_name
    folder = ensure_dir(os.path.join(output_dir(), safe_name(c["name"] if c else "Measure sheets")))
    name = safe_name(f"{(lay['updated'] or '')[:10]} - {(c['name'] if c else '')} - {lay['name'] or 'Kitchen'} "
                     f"measure sheet - #{lid}.pdf".strip(" -"))
    path = os.path.join(folder, name)
    K.make_pdf(lay, dict(c) if c else None, path)
    x("UPDATE kitchen_layouts SET pdf_path=? WHERE id=?", (path, lid))
    with open(path, "rb") as fh:
        data = fh.read()
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"'})


@bp.route("/kitchen/<int:lid>/delete", methods=["POST"])
@login_required
def delete(lid):
    lay = K.get(lid) or abort(404)
    x("DELETE FROM kitchen_layouts WHERE id=?", (lid,))
    flash("Layout deleted.", "ok")
    return redirect(url_for("kitchen.index", customer=lay["customer_id"]))


@bp.route("/kitchen/blank.pdf")
@login_required
def blank():
    return Response(K.blank_pdf(), mimetype="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="Countertop & Cabinet Measure Sheet.pdf"'})
