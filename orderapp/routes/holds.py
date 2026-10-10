"""Account Holds page: lapsed accounts that must be brought current before any purchase."""
import csv
import io

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from .. import holds as H
from ..db import now, q, x
from ..security import login_required

bp = Blueprint("holds", __name__)


@bp.route("/holds")
@login_required
def index():
    rows = H.active()
    cleared = q("""SELECT h.*, c.name AS cname FROM account_holds h LEFT JOIN customers c ON c.id=h.customer_id
                   WHERE h.status='cleared' ORDER BY h.cleared DESC, h.id DESC LIMIT 50""")
    total = sum(r["balance"] or 0 for r in rows)
    customers = q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE")
    if request.args.get("csv"):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Customer", "Balance", "Rep", "Terms", "On hold since", "Notes"])
        for r in rows:
            w.writerow([r["name"], f"{r['balance']:.2f}", r["rep"], r["terms"], r["since"], r["notes"] or ""])
        w.writerow(["Total", f"{total:.2f}"])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=Account holds.csv"})
    return render_template("holds.html", rows=rows, cleared=cleared, total=total, customers=customers)


@bp.route("/holds/import", methods=["POST"])
@login_required
def import_report():
    text = request.form.get("report", "")
    f = request.files.get("file")
    if f and f.filename:
        raw = f.read()
        text += "\n" + (raw.decode("utf-8-sig", errors="replace"))
    rows = H.parse_report(text)
    if not rows:
        flash("Couldn't find any accounts in that. Paste the rows like: Name  1,234.56  TA  ACCOUNT HOLD", "error")
        return redirect(url_for("holds.index"))
    a, u, c = H.import_rows(rows, clear_missing=bool(request.form.get("clear_missing")))
    msg = f"{len(rows)} account{'s' if len(rows) != 1 else ''} read: {a} new, {u} updated"
    if c:
        msg += f", {c} taken off hold (not on this report)"
    flash(msg + ".", "ok")
    return redirect(url_for("holds.index"))


@bp.route("/holds/add", methods=["POST"])
@login_required
def add():
    f = request.form
    cid = f.get("customer_id", type=int)
    name = f.get("name", "").strip()
    if cid and not name:
        c = q("SELECT name FROM customers WHERE id=?", (cid,), one=True)
        name = c["name"] if c else ""
    bal = H._money(f.get("balance", ""))
    if not name or bal is None:
        flash("Enter the account and the balance.", "error")
        return redirect(request.referrer or url_for("holds.index"))
    H.import_rows([{"name": name, "balance": bal, "rep": f.get("rep", "").strip().upper(),
                    "terms": "ACCOUNT HOLD"}])
    if cid:
        x("UPDATE account_holds SET customer_id=? WHERE status='hold' AND name_key=?",
          (cid, H.norm_name(name)))
    flash(f"{name} is on hold.", "ok")
    return redirect(request.referrer or url_for("holds.index"))


@bp.route("/holds/<int:hid>/update", methods=["POST"])
@login_required
def update(hid):
    f = request.form
    bal = H._money(f.get("balance", ""))
    cid = f.get("customer_id", type=int)
    x("""UPDATE account_holds SET balance=coalesce(?, balance), notes=?, customer_id=?, updated=? WHERE id=?""",
      (bal, f.get("notes", "").strip(), cid or None, now(), hid))
    flash("Saved.", "ok")
    return redirect(url_for("holds.index"))


@bp.route("/holds/<int:hid>/clear", methods=["POST"])
@login_required
def clear(hid):
    H.clear(hid, request.form.get("note", "").strip() or "Brought current")
    flash("Taken off hold - they can order again.", "ok")
    return redirect(request.referrer or url_for("holds.index"))


@bp.route("/holds/<int:hid>/reopen", methods=["POST"])
@login_required
def reopen(hid):
    x("UPDATE account_holds SET status='hold', cleared=NULL, updated=? WHERE id=?", (now(), hid))
    flash("Back on hold.", "ok")
    return redirect(url_for("holds.index"))
