import os
import threading
import time

from flask import Blueprint, render_template

from ..catalog import form_list
from ..changes import pending_batches
from ..db import setting, OPEN_STATUSES, q
from ..security import login_required

bp = Blueprint("main", __name__)


def orders_in_month(month):
    """Orders sent (or dated, if sent outside the app) in a YYYY-MM month - drafts don't count."""
    return q("""SELECT o.* FROM orders o WHERE o.status<>'Draft'
                AND substr(coalesce(nullif(o.sent_at,''), nullif(o.order_date,''), o.created), 1, 7)=?""", (month,))


def order_value(data):
    """$ total of an order's lines that have a price (qty x price each)."""
    import json
    import re
    try:
        d = json.loads(data or "{}")
    except ValueError:
        return 0
    total = 0.0
    for b in d.get("blocks") or []:
        price = re.sub(r"[^0-9.]", "", str(b.get("price") or ""))
        if not price:
            continue
        try:
            qty = float(re.sub(r"[^0-9.]", "", str(b.get("qty") or "1")) or 1)
            total += qty * float(price)
        except ValueError:
            pass
    return round(total, 2)


@bp.route("/")
@login_required
def dashboard():
    ph = ",".join("?" * len(OPEN_STATUSES))
    stats = {
        "customers": q("SELECT COUNT(*) n FROM customers", one=True)["n"],
        "open_orders": q(f"SELECT COUNT(*) n FROM orders WHERE status IN ({ph})", OPEN_STATUSES, one=True)["n"],
    }
    by_status = q(f"""SELECT status, COUNT(*) n FROM orders WHERE status IN ({ph})
                      GROUP BY status ORDER BY n DESC""", OPEN_STATUSES)
    recent = q("""SELECT o.*, c.name AS customer FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
                  ORDER BY o.updated DESC LIMIT 10""")
    due = q(f"""SELECT o.*, c.name AS customer FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
                WHERE o.status IN ({ph}) AND coalesce(o.due_date,'')<>'' ORDER BY o.due_date LIMIT 8""",
            OPEN_STATUSES)
    from datetime import date
    today = date.today().isoformat()
    stats["stops_today"] = q("SELECT COUNT(*) n FROM route_stops WHERE day=? AND status<>'Skipped'", (today,), one=True)["n"]
    stats["stops_left"] = q("SELECT COUNT(*) n FROM route_stops WHERE day=? AND status='Planned'", (today,), one=True)["n"]
    stats["followups_due"] = q("SELECT COUNT(*) n FROM tasks WHERE done=0 AND due_date<=?", (today,), one=True)["n"]
    month = today[:7]
    stats["month"] = month
    month_orders = orders_in_month(month)
    stats["orders_month"] = len(month_orders)
    stats["orders_month_total"] = sum(order_value(o["data"]) for o in month_orders)
    # live previews: today's route + CRM quick note
    from .routes_plan import day_timeline, fmt_dur
    from .crm import _contact_options
    from ..db import ACTIVITY_KINDS
    route_rows, _ = day_timeline(today)
    rd = q("SELECT total_drive_s, total_m, estimated FROM route_days WHERE day=?", (today,), one=True)
    start = q("SELECT lat, lon FROM geocache WHERE address=?", ((setting("route_start_address") or "").strip().lower(),), one=True)
    pts = [{"lat": r["s"]["lat"], "lon": r["s"]["lon"], "n": i + 1, "status": r["s"]["status"],
            "label": r["s"]["label"] or r["s"]["cname"] or ""} for i, r in enumerate(route_rows) if r["s"]["lat"] is not None]
    route = {"rows": route_rows, "points": pts, "start": [start["lat"], start["lon"]] if start else None,
             "drive": fmt_dur(rd["total_drive_s"]) if rd and rd["total_drive_s"] else "",
             "miles": round(rd["total_m"] / 1609.34, 1) if rd and rd["total_m"] else None,
             "estimated": bool(rd and rd["estimated"])}
    from datetime import timedelta as _td
    _d0 = date.fromisoformat(today)
    route["day_choices"] = [((_d0 + _td(days=i)).isoformat(),
                             "Today" if i == 0 else "Tomorrow" if i == 1 else (_d0 + _td(days=i)).strftime("%a %m/%d"))
                            for i in range(0, 8)]
    crm = {"due": q("""SELECT t.*, c.name AS cname FROM tasks t LEFT JOIN customers c ON c.id=t.customer_id
                       WHERE t.done=0 AND (t.due_date IS NULL OR t.due_date<=?) ORDER BY t.due_date LIMIT 6""", (today,)),
           "recent": q("""SELECT a.*, c.name AS cname FROM activities a LEFT JOIN customers c ON c.id=a.customer_id
                          ORDER BY a.occurred_at DESC, a.id DESC LIMIT 5"""),
           "kinds": ACTIVITY_KINDS}
    customers = q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE")
    return render_template("dashboard.html", today=today, route=route, crm=crm, customers=customers,
                           contacts=_contact_options(), stats=stats, by_status=by_status, recent=recent, due=due,
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
    return render_template("message.html", title="AIS Sales Support closed",
                           message="The app has shut down. You can close this browser tab.")
