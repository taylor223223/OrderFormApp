from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..security import check_login, create_user, user_count

bp = Blueprint("auth", __name__)


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if user_count() > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p1, p2 = request.form.get("password", ""), request.form.get("password2", "")
        if len(u) < 3:
            flash("Username must be at least 3 characters.", "error")
        elif len(p1) < 8:
            flash("Password must be at least 8 characters.", "error")
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
        user, err = check_login(request.form.get("username", ""), request.form.get("password", ""))
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
