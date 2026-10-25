"""Kitchen / bath measuring: lettered countertop runs, sinks, appliances, base + upper cabinets, diagram + PDF."""
import json
import os

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for

from .. import cabinet_layout as C
from .. import kitchen as K
from ..db import dumps, loads, now, q, x
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
    rows = [dict(r, v2=C.is_v2(loads(r["data"], {}))) for r in rows]
    return render_template("kitchen_list.html", rows=rows, c=_cust(cid), shapes=K.SHAPES,
                           customers=q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE"))


@bp.route("/kitchen/new", methods=["POST"])
@login_required
def new():
    f = request.form
    cid = f.get("customer_id", type=int)
    data = C.starter()
    data["room"] = f.get("room", "Kitchen")
    src = f.get("copy_from", type=int)          # start from another saved layout (same floorplan, other unit)
    if src:
        old = q("SELECT data FROM kitchen_layouts WHERE id=?", (src,), one=True)
        if old and C.is_v2(loads(old["data"], {})):
            data = C.normalize(loads(old["data"], {}))
    lid = x("""INSERT INTO kitchen_layouts(customer_id, name, unit, floorplan_id, room, data, created, updated)
               VALUES (?,?,?,?,?,?,?,?)""", (cid, f.get("name", "").strip() or "Kitchen", f.get("unit", "").strip(),
                                            f.get("floorplan_id", type=int), data["room"], dumps(data), now(), now()))
    return redirect(url_for("kitchen.edit", lid=lid))


def _raw(lid):
    r = q("SELECT * FROM kitchen_layouts WHERE id=?", (lid,), one=True)
    if not r:
        return None
    out = dict(r)
    out["data"] = loads(r["data"], {})
    return out


def _v2_payload(data):
    return {"walls": [C.wall_svg(w) for w in data["walls"]], "pieces": [
        dict(p, length_txt=K.fin(p["length"]), depth_txt=K.fin(p["depth"]), sqft_txt=f"{p['sqft']:.1f}")
        for p in C.countertop(data)], "schedule": C.schedule(data)}


@bp.route("/kitchen/<int:lid>", methods=["GET", "POST"])
@login_required
def edit(lid):
    raw = _raw(lid) or abort(404)
    if C.is_v2(raw["data"]):
        return _edit_v2(lid, raw)
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


def _edit_v2(lid, raw):
    """Box-by-box cabinet & countertop layout (v2)."""
    if request.method == "POST":
        j = request.get_json(silent=True)
        if j is None:
            j = json.loads(request.form.get("payload") or "{}")
        data = C.normalize(dict(j.get("data") or {}, v=C.V))
        x("""UPDATE kitchen_layouts SET data=?, name=coalesce(?, name), unit=coalesce(?, unit),
             floorplan_id=coalesce(?, floorplan_id), room=?, updated=? WHERE id=?""",
          (dumps(data), j.get("name"), j.get("unit"), j.get("floorplan_id") or None, data.get("room") or "Kitchen",
           now(), lid))
        return jsonify(dict(ok=True, **_v2_payload(data)))
    data = C.normalize(raw["data"])
    c = _cust(raw["customer_id"])
    fps = q("SELECT id, name FROM floorplans WHERE customer_id=? ORDER BY name", (raw["customer_id"],)) \
        if raw["customer_id"] else []
    return render_template("cabinet_layout.html", lay=raw, data=data, c=c, C=C, fps=fps,
                           payload=_v2_payload(data))


@bp.route("/kitchen/<int:lid>/preview", methods=["POST"])
@login_required
def preview(lid):
    j = request.get_json(silent=True) or {}
    if (j.get("data") or {}).get("v") == C.V:
        return jsonify(_v2_payload(C.normalize(j["data"])))
    data = K.normalize(j.get("data") or {})
    return jsonify({"svg": K.top_svg(data), "pieces": K.pieces(data), "sqft": K.sqft(data),
                    "elev": {k: K.draw_elevation(None, data, k, (0, 0, 640, 170), mode="svg")
                             for k in K.active_runs(data)}})


@bp.route("/kitchen/<int:lid>/pdf")
@login_required
def pdf(lid):
    raw = _raw(lid) or abort(404)
    lay = raw if C.is_v2(raw["data"]) else K.get(lid)
    c = _cust(lay["customer_id"])
    from .orders import output_dir, safe_name
    folder = ensure_dir(os.path.join(output_dir(), safe_name(c["name"] if c else "Measure sheets")))
    name = safe_name(f"{(lay['updated'] or '')[:10]} - {(c['name'] if c else '')} - {lay['name'] or 'Kitchen'} "
                     f"{'cabinet layout' if C.is_v2(lay['data']) else 'measure sheet'} - #{lid}.pdf".strip(" -"))
    path = os.path.join(folder, name)
    if C.is_v2(lay["data"]):
        C.make_pdf(lay, dict(c) if c else None, path)
    else:
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
    return Response(C.blank_pdf(), mimetype="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="Cabinet & Countertop Layout (blank).pdf"'})


@bp.route("/kitchen/<int:lid>/lines")
@login_required
def lines(lid):
    """Order lines for a cabinet form, from the layout's cabinet boxes (used by the order editor)."""
    raw = _raw(lid) or abort(404)
    if not C.is_v2(raw["data"]):
        return jsonify({"lines": []})
    return jsonify({"lines": C.order_lines(raw["data"], request.args.get("form", "cabinet_custom"))})
