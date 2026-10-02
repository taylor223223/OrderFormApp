import os
import threading
import time

from flask import Blueprint, render_template

from ..catalog import form_list
from ..changes import pending_batches
from ..db import OPEN_STATUSES, q
from ..security import login_required

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def dashboard():
    ph = ",".join("?" * len(OPEN_STATUSES))
    stats = {
        "customers": q("SELECT COUNT(*) n FROM customers", one=True)["n"],
        "units": q("SELECT COUNT(*) n FROM units", one=True)["n"],
        "measurements": q("SELECT COUNT(*) n FROM measurements", one=True)["n"],
        "open_orders": q(f"SELECT COUNT(*) n FROM orders WHERE status IN ({ph})", OPEN_STATUSES, one=True)["n"],
    }
    by_status = q(f"""SELECT status, COUNT(*) n FROM orders WHERE status IN ({ph})
                      GROUP BY status ORDER BY n DESC""", OPEN_STATUSES)
    recent = q("""SELECT o.*, c.name AS customer FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
                  ORDER BY o.updated DESC LIMIT 10""")
    due = q(f"""SELECT o.*, c.name AS customer FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
                WHERE o.status IN ({ph}) AND coalesce(o.due_date,'')<>'' ORDER BY o.due_date LIMIT 8""",
            OPEN_STATUSES)
    return render_template("dashboard.html", stats=stats, by_status=by_status, recent=recent, due=due,
                           forms=form_list(), batches=pending_batches())


@bp.route("/quit", methods=["POST"])
@login_required
def quit_app():
    from flask import abort, current_app
    if current_app.config.get("CLOUD"):
        abort(404)   # never let the hosted server be shut down from the browser
    def _stop():
        time.sleep(0.8)
        os._exit(0)
    threading.Thread(target=_stop, daemon=True).start()
    return render_template("message.html", title="Order Form App closed",
                           message="The app has shut down. You can close this browser tab.")
