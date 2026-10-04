import os
import secrets
from datetime import timedelta

from flask import Flask, abort, request, session

from . import db
from .paths import data_dir, is_cloud, resource_path

__version__ = "2.2.1"


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
        MAX_CONTENT_LENGTH=60 * 1024 * 1024,
        TESTING=testing,
        CLOUD=is_cloud(),
    )
    if app.config["CLOUD"]:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_SAMESITE="Lax")
    db.init_db(app.config["DB_PATH"])
    app.teardown_appcontext(db.close_db)

    from .security import csrf_token, parse_flash, verify_csrf
    app.jinja_env.filters["flashparse"] = parse_flash

    @app.route("/__version")
    def app_version_route():
        return __version__, 200, {"Content-Type": "text/plain"}

    @app.route("/__alive")
    def app_alive():
        return "ok", 200, {"Content-Type": "text/plain"}

    @app.before_request
    def _guard():
        import time as _t
        app.config["LAST_SEEN"] = _t.time()
        # only answer to this computer (blocks DNS-rebinding style attacks)
        host = (request.host or "").split(":")[0]
        if not testing and not app.config["CLOUD"] and host not in ("127.0.0.1", "localhost"):
            abort(403)
        allowed = [h.strip() for h in os.environ.get("ORDERAPP_HOSTS", "").split(",") if h.strip()]
        if app.config["CLOUD"] and allowed and host not in allowed:
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
        if app.config["CLOUD"]:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.endpoint not in ("static", "pwa_sw", "pwa_manifest"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.route("/sw.js")
    def pwa_sw():
        from flask import send_from_directory
        r = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
        r.headers["Service-Worker-Allowed"] = "/"
        r.headers["Cache-Control"] = "no-cache"
        return r

    @app.route("/manifest.webmanifest")
    def pwa_manifest():
        from flask import send_from_directory
        return send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.context_processor
    def _ctx():
        from .changes import pending_count
        pc = ed = 0
        if session.get("uid"):
            try:
                pc = pending_count()
                ed = db.q("""SELECT COUNT(*) n FROM orders WHERE source='email' AND status='Draft'
                             AND notes LIKE 'Auto-drafted%'""", one=True)["n"]
            except Exception:  # noqa: BLE001
                pc = ed = 0
        return {"csrf_token": csrf_token, "pending_reviews": pc, "email_drafts": ed, "app_version": __version__, "cloud": app.config["CLOUD"],
                "username": session.get("username")}

    from .routes import auth, crm, customers, emails, imports, main, orders, photos, routes_plan, settings
    for bp in (auth.bp, main.bp, customers.bp, orders.bp, imports.bp, emails.bp, settings.bp, photos.bp, crm.bp, routes_plan.bp):
        app.register_blueprint(bp)
    if not testing:
        _start_email_watcher(app)
    return app


def _start_email_watcher(app):
    """While the app runs: every N minutes read ONLY emails whose subject has the order word,
    and turn new ones into draft orders. Never sends anything."""
    import threading
    import time

    def loop():
        com = False
        try:
            import pythoncom  # Outlook automation needs COM set up on this thread (Windows only)
            pythoncom.CoInitialize()
            com = True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(20)          # let the app finish starting
        while True:
            mins = 0
            try:
                with app.app_context():
                    mins = int(db.setting("email_auto_minutes") or 0)
                    if mins > 0 and (db.setting("email_provider") or "eml") != "eml" and \
                            db.q("SELECT 1 FROM users LIMIT 1", one=True):
                        from .routes.emails import auto_check
                        auto_check()
            except Exception:  # noqa: BLE001 - never let a mail hiccup stop the app
                pass
            time.sleep(max(mins, 1) * 60 if mins > 0 else 120)
        if com:
            pythoncom.CoUninitialize()

    threading.Thread(target=loop, daemon=True, name="email-watcher").start()
