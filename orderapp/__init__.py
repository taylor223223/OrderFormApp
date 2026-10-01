import os
import secrets
from datetime import timedelta

from flask import Flask, abort, request, session

from . import db
from .paths import data_dir, resource_path

__version__ = "1.0.0"


def _secret_key():
    p = os.path.join(data_dir(), "secret.key")
    if not os.path.exists(p):
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_hex(32))
    with open(p, encoding="utf-8") as fh:
        return fh.read().strip()


def create_app(db_path=None, testing=False):
    app = Flask(__name__, template_folder=resource_path("templates"), static_folder=resource_path("static"))
    app.config.update(
        SECRET_KEY=_secret_key(),
        DB_PATH=db_path or db.db_path(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        MAX_CONTENT_LENGTH=40 * 1024 * 1024,
        TESTING=testing,
    )
    db.init_db(app.config["DB_PATH"])
    app.teardown_appcontext(db.close_db)

    from .security import csrf_token, parse_flash, verify_csrf
    app.jinja_env.filters["flashparse"] = parse_flash

    @app.before_request
    def _guard():
        # only answer to this computer (blocks DNS-rebinding style attacks)
        host = (request.host or "").split(":")[0]
        if not testing and host not in ("127.0.0.1", "localhost"):
            abort(403)
        verify_csrf()
        mins = int(db.setting("session_minutes") or 240)
        app.permanent_session_lifetime = timedelta(minutes=mins)
        session.permanent = True

    @app.after_request
    def _headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.context_processor
    def _ctx():
        from .changes import pending_count
        pc = 0
        if session.get("uid"):
            try:
                pc = pending_count()
            except Exception:  # noqa: BLE001
                pc = 0
        return {"csrf_token": csrf_token, "pending_reviews": pc, "app_version": __version__,
                "username": session.get("username")}

    from .routes import auth, customers, emails, imports, main, orders, settings
    for bp in (auth.bp, main.bp, customers.bp, orders.bp, imports.bp, emails.bp, settings.bp):
        app.register_blueprint(bp)
    return app
