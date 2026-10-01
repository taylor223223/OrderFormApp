import csv
import io
import os
import sqlite3
import tempfile
from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from ..catalog import PRODUCTS
from ..db import DEFAULT_SETTINGS, db_path, loads, q, set_setting, setting
from ..emailer import GraphMail, MailError, OutlookDesktop
from ..paths import data_dir, default_output_dir
from ..security import change_password, login_required

bp = Blueprint("settings", __name__)

EDITABLE = ["sales_rep", "order_to", "order_cc", "email_provider", "send_mode", "graph_client_id", "graph_tenant",
            "subject_template", "body_template", "output_dir", "date_format", "session_minutes"]


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        for k in EDITABLE:
            if k in request.form:
                v = request.form.get(k, "").strip() if k != "body_template" else request.form.get(k, "")
                if k == "session_minutes" and not v.isdigit():
                    v = DEFAULT_SETTINGS[k]
                set_setting(k, v)
        flash("Settings saved.", "ok")
        return redirect(url_for("settings.index"))
    vals = {k: setting(k) for k in EDITABLE}
    od_ok, od_why = OutlookDesktop.available()
    g = GraphMail(vals["graph_client_id"], vals["graph_tenant"])
    graph_user = None
    try:
        graph_user = g.signed_in_as()
    except Exception:  # noqa: BLE001
        graph_user = None
    return render_template("settings.html", v=vals, od_ok=od_ok, od_why=od_why, graph_user=graph_user,
                           default_out=default_output_dir(), data_dir=data_dir())


@bp.route("/settings/password", methods=["POST"])
@login_required
def password():
    new = request.form.get("new", "")
    if len(new) < 8:
        flash("New password must be at least 8 characters.", "error")
    elif new != request.form.get("new2", ""):
        flash("New passwords don't match.", "error")
    elif not change_password(session["uid"], request.form.get("old", ""), new):
        flash("Current password is wrong.", "error")
    else:
        flash("Password changed.", "ok")
    return redirect(url_for("settings.index"))


@bp.route("/settings/graph/signin", methods=["POST"])
@login_required
def graph_signin():
    g = GraphMail(setting("graph_client_id"), setting("graph_tenant"))
    ok, why = g.available()
    if not ok:
        return jsonify({"ok": False, "error": why})
    try:
        st = g.start_sign_in()
    except MailError as e:
        return jsonify({"ok": False, "error": str(e)})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"Sign-in failed to start: {e}"})
    return jsonify({"ok": True, **st})


@bp.route("/settings/graph/status")
@login_required
def graph_status():
    g = GraphMail(setting("graph_client_id"), setting("graph_tenant"))
    st = g.sign_in_state() or {}
    user = None
    try:
        user = g.signed_in_as()
    except Exception:  # noqa: BLE001
        pass
    return jsonify({"status": st.get("status"), "message": st.get("message"), "user": user})


@bp.route("/settings/graph/signout", methods=["POST"])
@login_required
def graph_signout():
    GraphMail(setting("graph_client_id"), setting("graph_tenant")).sign_out()
    flash("Signed out of Microsoft.", "ok")
    return redirect(url_for("settings.index"))


@bp.route("/settings/backup")
@login_required
def backup():
    """Download a consistent copy of the database."""
    tmp = os.path.join(tempfile.gettempdir(), "orderapp_backup.db")
    src = sqlite3.connect(db_path())
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    dst.close()
    src.close()
    return send_file(tmp, as_attachment=True,
                     download_name=f"OrderFormApp-backup-{datetime.now():%Y%m%d-%H%M}.db")


@bp.route("/settings/export/<what>")
@login_required
def export(what):
    out = io.StringIO()
    w = csv.writer(out)
    if what == "customers":
        cols = ["name", "acct", "address", "city", "state", "zip", "mgmt", "phone", "email", "notes"]
        w.writerow(cols + ["contact_name", "contact_email", "contact_phone", "contact_role"])
        for c in q("SELECT * FROM customers ORDER BY name"):
            ks = q("SELECT * FROM contacts WHERE customer_id=?", (c["id"],)) or [None]
            for k in ks:
                w.writerow([c[x] or "" for x in cols] +
                           ([k["name"] or "", k["email"] or "", k["phone"] or "", k["role"] or ""] if k else [""] * 4))
    elif what == "measurements":
        rows = q("""SELECT m.*, c.name AS cname, f.name AS fp, u.unit_number FROM measurements m
                    JOIN customers c ON c.id=m.customer_id LEFT JOIN floorplans f ON f.id=m.floorplan_id
                    LEFT JOIN units u ON u.id=m.unit_id ORDER BY c.name, f.name, u.unit_number""")
        keys = []
        for r in rows:
            for k in loads(r["data"]):
                if k not in keys:
                    keys.append(k)
        w.writerow(["name", "floorplan", "unit", "product", "room", "verified", "notes"] + keys)
        for r in rows:
            d = loads(r["data"])
            w.writerow([r["cname"], r["fp"] or "", r["unit_number"] or "",
                        PRODUCTS.get(r["product"], {}).get("label", r["product"]), r["room"] or "",
                        "yes" if r["verified"] else "", r["notes"] or ""] + [d.get(k, "") for k in keys])
    elif what == "units":
        w.writerow(["name", "unit", "building", "floorplan"])
        for r in q("""SELECT c.name, u.unit_number, u.building, f.name AS fp FROM units u
                      JOIN customers c ON c.id=u.customer_id LEFT JOIN floorplans f ON f.id=u.floorplan_id
                      ORDER BY c.name, u.unit_number"""):
            w.writerow([r["name"], r["unit_number"], r["building"] or "", r["fp"] or ""])
    else:
        return redirect(url_for("settings.index"))
    data = io.BytesIO(out.getvalue().encode("utf-8-sig"))
    return send_file(data, mimetype="text/csv", as_attachment=True,
                     download_name=f"{what}-{datetime.now():%Y%m%d}.csv")
