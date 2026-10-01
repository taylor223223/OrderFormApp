import secrets
from datetime import datetime, timedelta
from functools import wraps

import json

from flask import abort, flash, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import now, q, x

MAX_ATTEMPTS = 5
LOCK_MINUTES = 5


def hash_pw(pw):
    return generate_password_hash(pw)


def user_count():
    return q("SELECT COUNT(*) AS n FROM users", one=True)["n"]


def create_user(username, pw):
    return x("INSERT INTO users(username, password_hash, created) VALUES (?,?,?)",
             (username.strip(), hash_pw(pw), now()))


def check_login(username, pw):
    """Return (user_row | None, error message)."""
    u = q("SELECT * FROM users WHERE username=?", (username.strip(),), one=True)
    if not u:
        return None, "Invalid username or password."
    if u["locked_until"] and u["locked_until"] > now():
        return None, "Too many failed attempts. Try again in a few minutes."
    if not check_password_hash(u["password_hash"], pw):
        n = (u["failed_attempts"] or 0) + 1
        lock = None
        if n >= MAX_ATTEMPTS:
            lock = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
            n = 0
        x("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?", (n, lock, u["id"]))
        return None, "Invalid username or password."
    x("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (u["id"],))
    return u, ""


def change_password(user_id, old, new):
    u = q("SELECT * FROM users WHERE id=?", (user_id,), one=True)
    if not u or not check_password_hash(u["password_hash"], old):
        return False
    x("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(new), user_id))
    return True


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def verify_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Security token missing or expired. Go back, refresh the page and try again.")


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("uid"):
            return redirect(url_for("auth.login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def flash_link(text, url, link_text, category="info"):
    """Flash a message with a link without allowing HTML injection."""
    flash("@@" + json.dumps({"text": text, "url": url, "link": link_text}), category)


def parse_flash(msg):
    if isinstance(msg, str) and msg.startswith("@@"):
        try:
            return json.loads(msg[2:])
        except ValueError:
            pass
    return {"text": msg, "url": None, "link": None}
