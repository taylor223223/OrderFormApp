import csv
import io
import os
import sqlite3
import tempfile
from datetime import datetime

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from ..catalog import PRODUCTS
from ..db import DEFAULT_SETTINGS, db_path, loads, q, set_setting, setting
from ..emailer import GraphMail, MailError, OutlookDesktop
from ..paths import data_dir, default_output_dir
from ..security import change_password, login_required

bp = Blueprint("settings", __name__)

EDITABLE = ["sales_rep", "order_to", "order_cc", "email_provider", "send_mode", "graph_client_id", "graph_tenant",
            "subject_template", "body_template", "output_dir", "date_format", "session_minutes",
            "report_to", "report_cc", "ors_api_key", "route_start_address", "route_start_time",
            "route_visit_minutes", "route_return_to_start", "email_order_word", "email_auto_minutes",
            "email_lookback_days", "delivery_subject_word", "quote_valid_days", "quote_terms", "pdf_layout", "notice_subject_words",
            "reply_confirm_words", "net_proxy"]


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        for k in EDITABLE:
            if k in request.form:
                v = request.form.get(k, "").strip() if k != "body_template" else request.form.get(k, "")
                if k in ("session_minutes", "route_visit_minutes", "email_auto_minutes", "email_lookback_days") \
                        and not v.isdigit():
                    v = DEFAULT_SETTINGS[k]
                set_setting(k, v)
        if "email_order_word" in request.form and not request.form.get("email_order_word", "").strip():
            set_setting("email_order_word", "Order")
        if "delivery_subject_word" in request.form:
            set_setting("delivery_auto", "1" if request.form.get("delivery_auto") else "0")
        if "notice_subject_words" in request.form:
            set_setting("notice_auto", "1" if request.form.get("notice_auto") else "0")
            set_setting("reply_confirm", "1" if request.form.get("reply_confirm") else "0")
        if "quote_valid_days" in request.form and not request.form.get("quote_valid_days", "").strip().isdigit():
            set_setting("quote_valid_days", "30")
        if "route_start_address" in request.form:
            set_setting("route_return_to_start", "1" if request.form.get("route_return_to_start") else "0")
        flash("Settings saved.", "ok")
        return redirect(url_for("settings.index"))
    vals = {k: setting(k) for k in EDITABLE}
    vals["delivery_auto"] = setting("delivery_auto")
    vals["notice_auto"] = setting("notice_auto")
    vals["reply_confirm"] = setting("reply_confirm")
    od_ok, od_why = OutlookDesktop.available()
    g = GraphMail(vals["graph_client_id"], vals["graph_tenant"])
    graph_user = None
    try:
        graph_user = g.signed_in_as()
    except Exception:  # noqa: BLE001
        graph_user = None
    from ..safety import status
    return render_template("settings.html", v=vals, od_ok=od_ok, od_why=od_why, graph_user=graph_user,
                           default_out=default_output_dir(), data_dir=data_dir(),
                           safe=status(current_app.config["DB_PATH"]), map_error=setting("map_last_error"))


@bp.route("/settings/test-maps", methods=["GET", "POST"])
@login_required
def test_maps():
    """Step-by-step check of the map services the route planner uses."""
    from .. import geo
    key = (setting("ors_api_key") or "").strip().strip('"').strip("'") or None
    checks = geo.diagnose(key)
    return render_template("map_test.html", checks=checks, ok=all(c["ok"] for c in checks), has_key=bool(key))


@bp.route("/settings/open-data", methods=["POST"])
@login_required
def open_data():
    from ..emailer import open_file
    folder = os.path.dirname(os.path.abspath(current_app.config["DB_PATH"]))
    try:
        open_file(folder)
    except Exception:  # noqa: BLE001
        flash(f"Your data folder is: {folder}", "info")
    return redirect(url_for("settings.index") + "#data")


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
    src = sqlite3.connect(current_app.config['DB_PATH'])
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    dst.close()
    src.close()
    return send_file(tmp, as_attachment=True,
                     download_name=f"AIS-Sales-Support-backup-{datetime.now():%Y%m%d-%H%M}.db")


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


@bp.route("/settings/restore", methods=["POST"])
@login_required
def restore():
    """Replace all data with an uploaded backup (e.g. moving the PC app's data online)."""
    from ..db import init_db
    f = request.files.get("backup")
    if not f or not f.filename.lower().endswith(".db"):
        flash("Choose a .db backup file (Settings → Download backup on the other app).", "error")
        return redirect(url_for("settings.index"))
    tmpd = os.path.join(data_dir(), "backups")
    os.makedirs(tmpd, exist_ok=True)
    up = os.path.join(tmpd, "upload-restore.db")
    f.save(up)
    try:
        src = sqlite3.connect(up)
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"customers", "orders", "users"} <= tables:
            raise ValueError("not an AIS Sales Support backup")
        n_users = src.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if not n_users:
            raise ValueError("backup has no login")
    except Exception as e:  # noqa: BLE001
        flash(f"That file can't be restored: {e}", "error")
        return redirect(url_for("settings.index"))
    # keep a copy of what's here now
    keep = os.path.join(tmpd, f"before-restore-{datetime.now():%Y%m%d-%H%M%S}.db")
    cur = sqlite3.connect(current_app.config['DB_PATH'])
    k = sqlite3.connect(keep)
    cur.backup(k)
    k.close()
    src.backup(cur)
    cur.close()
    src.close()
    os.remove(up)
    init_db(current_app.config['DB_PATH'])   # adds any tables newer than the backup
    session.clear()
    flash("Data restored. Log in with the username and password from the app the backup came from.", "ok")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------- product option photos
@bp.route("/option-photo/<group>/<name>")
@login_required
def option_photo(group, name):
    from .. import option_photos
    p = option_photos.find(group, name)
    if not p:
        return ("", 404)
    resp = send_file(p, max_age=0)
    return resp


@bp.route("/settings/product-photos", methods=["GET", "POST"])
@login_required
def product_photos():
    from .. import option_photos
    groups = option_photos.catalog_groups()
    if request.method == "POST":
        group, label = request.form.get("group", ""), request.form.get("label", "")
        if group not in groups or label not in groups[group]:
            flash("Unknown product option.", "error")
        elif request.form.get("action") == "reset":
            option_photos.remove_upload(group, label)
            flash(f"{label}: back to the catalog photo.", "ok")
        else:
            f = request.files.get("photo")
            if not f or not f.filename:
                flash("Pick a picture first.", "error")
            else:
                try:
                    option_photos.save_upload(group, label, f)
                    flash(f"{label}: photo saved.", "ok")
                except ValueError as e:
                    flash(str(e), "error")
        return redirect(url_for("settings.product_photos", _anchor=group))
    rows = [(g, option_photos.GROUP_LABELS.get(g, g),
             [(lab, option_photos.slug(lab), bool(option_photos.find(g, lab)), option_photos.is_custom(g, lab))
              for lab in labs])
            for g, labs in groups.items()]
    return render_template("product_photos.html", rows=rows)


@bp.route("/settings/territory", methods=["GET", "POST"])
@login_required
def territory():
    """Which ZIPs are mine (and the other reps'), so the planners only suggest accounts in my territory."""
    from .. import territory as T
    if request.method == "POST":
        f = request.form
        for i, rep in enumerate(T.reps()):
            if f"zips_{i}" in f:
                T.save_rep_zips(rep, f.get(f"zips_{i}", ""))
        T.set_options(bool(f.get("only")), bool(f.get("include_billed")), f.get("partial", ""))
        flash("Territory saved. Plan my week, Routes and 'accounts around' now use it.", "ok")
        return redirect(url_for("settings.territory"))
    return render_template("territory.html", me=T.me(), reps=[(r, " ".join(T.rep_zips(r))) for r in T.reps()],
                           only=T.enabled(), include_billed=setting("territory_include_billed") != "0",
                           partial=" ".join(sorted(T.partial_zips())), counts=T.counts(),
                           north=T.NORTH_OF_202_LAT)
