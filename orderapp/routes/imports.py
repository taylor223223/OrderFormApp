import os
import uuid

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..changes import KIND_LABEL, apply_changes, describe, pending_batches
from ..db import loads, q, x
from ..importer import all_targets, build_batch, guess_mapping, read_table
from ..paths import data_dir
from ..security import login_required

bp = Blueprint("imports", __name__)


def _upload_dir():
    d = os.path.join(data_dir(), "uploads")
    os.makedirs(d, exist_ok=True)
    return d


@bp.route("/import", methods=["GET", "POST"])
@login_required
def upload():
    customers = q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE")
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Choose a CSV or Excel file.", "error")
            return redirect(url_for("imports.upload"))
        if not f.filename.lower().endswith((".csv", ".txt", ".xlsx", ".xlsm")):
            flash("Only .csv or .xlsx files are supported.", "error")
            return redirect(url_for("imports.upload"))
        raw = f.read()
        try:
            headers, rows = read_table(raw, f.filename)
        except Exception as e:  # noqa: BLE001
            flash(f"Couldn't read that file: {e}", "error")
            return redirect(url_for("imports.upload"))
        if not rows:
            flash("No data rows found in that file.", "error")
            return redirect(url_for("imports.upload"))
        token = uuid.uuid4().hex
        ext = os.path.splitext(f.filename)[1].lower()
        with open(os.path.join(_upload_dir(), token + ext), "wb") as fh:
            fh.write(raw)
        return render_template("import_map.html", headers=headers, rows=rows[:8], n=len(rows),
                               mapping=guess_mapping(headers), targets=all_targets(), token=token + ext,
                               filename=f.filename, customers=customers,
                               default_customer=request.form.get("customer_id", ""))
    return render_template("import_upload.html", customers=customers,
                           pre=request.args.get("customer", ""))


@bp.route("/import/apply_mapping", methods=["POST"])
@login_required
def apply_mapping():
    token = os.path.basename(request.form.get("token", ""))
    path = os.path.join(_upload_dir(), token)
    if not token or not os.path.exists(path):
        abort(400)
    with open(path, "rb") as fh:
        raw = fh.read()
    headers, rows = read_table(raw, token)
    mapping = {h: request.form.get(f"map_{i}", "") for i, h in enumerate(headers)}
    if not any(mapping.values()):
        flash("Map at least one column.", "error")
        return redirect(url_for("imports.upload"))
    bid, summary = build_batch(rows, mapping, request.form.get("filename", token),
                               request.form.get("customer_id", type=int))
    os.remove(path)
    n = q("SELECT COUNT(*) n FROM changes WHERE batch_id=?", (bid,), one=True)["n"]
    if not n:
        x("DELETE FROM change_batches WHERE id=?", (bid,))
        flash("Everything in that file is already saved - nothing new to add.", "info")
        return redirect(url_for("imports.upload"))
    flash(f"Found {n} possible update(s). Blank fields are pre-checked; conflicts are not. "
          "Review and click Apply.", "ok")
    return redirect(url_for("imports.review_batch", bid=bid))


@bp.route("/review")
@login_required
def review():
    return render_template("review_list.html", batches=pending_batches())


@bp.route("/review/<int:bid>", methods=["GET", "POST"])
@login_required
def review_batch(bid):
    b = q("SELECT * FROM change_batches WHERE id=?", (bid,), one=True) or abort(404)
    if request.method == "POST":
        if request.form.get("discard"):
            x("UPDATE changes SET status='rejected' WHERE batch_id=? AND status='pending'", (bid,))
            x("UPDATE change_batches SET status='done' WHERE id=?", (bid,))
            flash("Discarded.", "ok")
            return redirect(url_for("imports.review"))
        ids = request.form.getlist("accept")
        applied, skipped = apply_changes(bid, ids)
        msg = f"Applied {applied} change(s)."
        if skipped:
            msg += f" {skipped} skipped because their new customer wasn't approved."
        flash(msg, "ok")
        return redirect(url_for("imports.review"))
    rows = q("""SELECT ch.*, c.name AS cname FROM changes ch LEFT JOIN customers c ON c.id=ch.customer_id
                WHERE ch.batch_id=? ORDER BY ch.customer_id, ch.customer_ref, ch.id""", (bid,))
    groups = {}
    for r in rows:
        key = r["cname"] or (r["customer_ref"] or "")
        label = r["cname"]
        if not label:
            nc = next((y for y in rows if y["kind"] == "new_customer" and y["customer_ref"] == r["customer_ref"]),
                      None)
            label = ("NEW: " + (loads(nc["payload"]).get("name") or nc["new_value"] or "")) if nc else "Unknown"
        groups.setdefault(key, {"label": label, "items": []})["items"].append(
            {"r": r, "kind": KIND_LABEL.get(r["kind"], r["kind"]), "desc": describe(r),
             "payload": loads(r["payload"])})
    return render_template("review_batch.html", b=b, groups=groups,
                           done=b["status"] != "pending")
