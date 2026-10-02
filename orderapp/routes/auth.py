import os
import secrets

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from ..security import check_login, create_user, ip_blocked, ip_fail, user_count

bp = Blueprint("auth", __name__)


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if user_count() > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p1, p2 = request.form.get("password", ""), request.form.get("password2", "")
        minlen = 10 if current_app.config["CLOUD"] else 8
        key = os.environ.get("ORDERAPP_SETUP_KEY", "")
        if current_app.config["CLOUD"] and (not key or not secrets.compare_digest(
                request.form.get("setup_key", "").strip(), key)):
            flash("Wrong setup key. It's in your hosting dashboard (ORDERAPP_SETUP_KEY).", "error")
        elif len(u) < 3:
            flash("Username must be at least 3 characters.", "error")
        elif len(p1) < minlen:
            flash(f"Password must be at least {minlen} characters.", "error")
        elif p1 != p2:
            flash("Passwords do not match.", "error")
        else:
            uid = create_user(u, p1)
            session.clear()
            session["uid"], session["username"] = uid, u
            flash("Account created. Next: check Settings (sales rep name, email).", "ok")
            return redirect(url_for("settings.index"))
    return render_template("setup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if user_count() == 0:
        return redirect(url_for("auth.setup"))
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if ip_blocked(ip):
            flash("Too many failed attempts from this device. Wait 15 minutes.", "error")
            return render_template("login.html")
        user, err = check_login(request.form.get("username", ""), request.form.get("password", ""))
        if not user:
            ip_fail(ip)
        if user:
            session.clear()
            session["uid"], session["username"] = user["id"], user["username"]
            nxt = request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))
        flash(err, "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
