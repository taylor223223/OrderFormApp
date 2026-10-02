"""Route planner: plan stops a week at a time, optimize the driving order, drive times, map."""
import json
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from .. import geo
from ..db import ESTIMATE_ITEMS, get_db, now, q, setting, x
from ..security import login_required

bp = Blueprint("plan", __name__)
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _day(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _key():
    return (setting("ors_api_key") or "").strip() or None


def stops_for(day):
    return q("""SELECT s.*, c.name AS cname, c.phone AS cphone FROM route_stops s
                LEFT JOIN customers c ON c.id=s.customer_id WHERE s.day=? ORDER BY s.position, s.id""", (day,))


def _stop_point(s):
    """(lat, lon) for a stop, geocoding (and caching on the customer) when needed."""
    if s["lat"] is not None and s["lon"] is not None:
        return (s["lat"], s["lon"])
    pt = None
    if s["customer_id"]:
        c = q("SELECT * FROM customers WHERE id=?", (s["customer_id"],), one=True)
        if c and c["lat"] is not None:
            pt = (c["lat"], c["lon"])
        elif c:
            pt = geo.geocode(s["address"] or geo.full_address(c), _key(), get_db())
            if pt:
                x("UPDATE customers SET lat=?, lon=?, geo_address=? WHERE id=?",
                  (pt[0], pt[1], geo.full_address(c), c["id"]))
    else:
        pt = geo.geocode(s["address"], _key(), get_db())
    if pt:
        x("UPDATE route_stops SET lat=?, lon=? WHERE id=?", (pt[0], pt[1], s["id"]))
    return pt


def _start(day):
    rd = q("SELECT * FROM route_days WHERE day=?", (day,), one=True)
    addr = (rd["start_address"] if rd and rd["start_address"] else None) or setting("route_start_address")
    t = (rd["start_time"] if rd and rd["start_time"] else None) or setting("route_start_time") or "08:00"
    return addr, t


def plan_day(day, optimize=False):
    """Geocode, (optionally) reorder, compute legs + ETAs, save. Returns a dict for the page."""
    stops = [s for s in stops_for(day) if s["status"] != "Skipped"]
    start_addr, start_time = _start(day)
    errors = []
    try:
        start_pt = geo.geocode(start_addr, _key(), get_db()) if start_addr else None
    except geo.GeoError as e:
        start_pt, errors = None, [str(e)]
    pts, ok_stops, missing = [], [], []
    for s in stops:
        try:
            p = _stop_point(s)
        except geo.GeoError as e:
            p = None
            if str(e) not in errors:
                errors.append(str(e))
        if p:
            pts.append(p)
            ok_stops.append(s)
        else:
            missing.append(s)
    round_trip = setting("route_return_to_start") == "1"
    result = {"legs": [], "total_s": 0, "total_m": 0, "estimated": True, "geometry": None, "errors": errors,
              "missing": [m["id"] for m in missing], "start": start_pt, "start_address": start_addr,
              "start_time": start_time, "round_trip": round_trip}
    if not start_pt or not ok_stops:
        return result
    allp = [start_pt] + pts
    dur, dist, est = geo.matrix(allp, _key())
    order = list(range(1, len(allp)))
    if optimize and len(ok_stops) > 1:
        done_first = [i for i, s in enumerate(ok_stops, 1) if s["status"] == "Done"]
        order = done_first + [i for i in geo.optimize_order(dur, round_trip) if i not in done_first]
        for pos, i in enumerate(order):
            x("UPDATE route_stops SET position=? WHERE id=?", (pos, ok_stops[i - 1]["id"]))
        base = len(order)
        for k, m in enumerate(missing):
            x("UPDATE route_stops SET position=? WHERE id=?", (base + k, m["id"]))
    legs, prev, tot_s, tot_m = [], 0, 0, 0
    for i in order:
        legs.append({"stop_id": ok_stops[i - 1]["id"], "s": dur[prev][i], "m": dist[prev][i]})
        tot_s += dur[prev][i]
        tot_m += dist[prev][i]
        prev = i
    back = None
    if round_trip:
        back = {"s": dur[prev][0], "m": dist[prev][0]}
        tot_s += back["s"]
        tot_m += back["m"]
    seq = [allp[i] for i in [0] + order] + ([start_pt] if round_trip else [])
    geom = geo.directions_geometry(seq, _key())
    x("""INSERT INTO route_days(day, total_drive_s, total_m, legs, geometry, estimated, optimized_at)
         VALUES (?,?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET total_drive_s=excluded.total_drive_s,
         total_m=excluded.total_m, legs=excluded.legs, geometry=excluded.geometry, estimated=excluded.estimated,
         optimized_at=CASE WHEN ? THEN excluded.optimized_at ELSE route_days.optimized_at END""",
      (day, tot_s, tot_m, json.dumps({"legs": legs, "back": back}), json.dumps(geom) if geom else None,
       1 if est else 0, now(), 1 if optimize else 0))
    result.update(legs=legs, back=back, total_s=tot_s, total_m=tot_m, estimated=est, geometry=geom)
    return result


def add_stop(day, customer_id=None, label="", address="", purpose="", source="", visit_min=None, smart=True,
             products="", notes=""):
    """Add a stop. If the day already has a route, slot it in where it adds the least driving."""
    pos = (q("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM route_stops WHERE day=?", (day,), one=True)["p"])
    if customer_id and not address:
        c = q("SELECT * FROM customers WHERE id=?", (customer_id,), one=True)
        if c:
            label = label or c["name"]
            address = geo.full_address(c)
    sid = x("""INSERT INTO route_stops(day, position, customer_id, label, address, purpose, visit_min, source, created,
               products, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (day, pos, customer_id, label, address, purpose, visit_min, source, now(), products or "", notes or ""))
    if smart:
        try:
            existing = [s for s in stops_for(day) if s["id"] != sid and s["status"] != "Skipped"]
            start_addr, _ = _start(day)
            start_pt = geo.geocode(start_addr, _key(), get_db())
            new = q("SELECT * FROM route_stops WHERE id=?", (sid,), one=True)
            new_pt = _stop_point(new)
            pts = [_stop_point(s) for s in existing]
            if start_pt and new_pt and existing and all(pts):
                dur, _, _ = geo.matrix([start_pt] + pts + [new_pt], _key())
                cur = list(range(1, len(existing) + 1))
                ins = geo.best_insertion(dur, cur, len(existing) + 1, setting("route_return_to_start") == "1")
                done_count = sum(1 for s in existing if s["status"] == "Done")
                ins = max(ins, done_count)       # never insert before stops already visited
                order = [s["id"] for s in existing]
                order.insert(ins, sid)
                for p, i in enumerate(order):
                    x("UPDATE route_stops SET position=? WHERE id=?", (p, i))
        except geo.GeoError:
            pass
    return sid


def fmt_dur(s):
    s = int(round(s or 0))
    h, m = divmod(s // 60, 60)
    return f"{h} h {m} min" if h else f"{m} min"


# ---------------------------------------------------------------- pages
@bp.route("/routes")
@login_required
def week():
    d = _day(request.args.get("week")) or date.today()
    mon = d - timedelta(days=d.weekday())
    days = []
    for i in range(7):
        dd = mon + timedelta(days=i)
        ds = dd.isoformat()
        st = stops_for(ds)
        rd = q("SELECT * FROM route_days WHERE day=?", (ds,), one=True)
        days.append({"date": dd, "iso": ds, "name": DAYS[i], "stops": st,
                     "drive": fmt_dur(rd["total_drive_s"]) if rd and rd["total_drive_s"] else "",
                     "estimated": bool(rd and rd["estimated"]), "today": dd == date.today()})
    customers = q("SELECT id, name, city FROM customers ORDER BY name COLLATE NOCASE")
    return render_template("route_week.html", days=days, mon=mon, prev=(mon - timedelta(days=7)).isoformat(),
                           nxt=(mon + timedelta(days=7)).isoformat(), customers=customers,
                           today=date.today().isoformat(), has_key=bool(_key()), items=ESTIMATE_ITEMS)


@bp.route("/routes/add", methods=["POST"])
@login_required
def add():
    f = request.form
    day = f.get("day") or date.today().isoformat()
    cid = f.get("customer_id", type=int)
    addr = f.get("address", "").strip()
    if not cid and not addr:
        flash("Pick a property or type an address.", "error")
        return redirect(request.referrer or url_for("plan.week"))
    add_stop(day, customer_id=cid, label=f.get("label", "").strip() or addr, address=addr if not cid else "",
             purpose=f.get("purpose", "").strip(), source=f.get("source", ""),
             visit_min=f.get("visit_min", type=int), products=", ".join(f.getlist("products")),
             notes=f.get("notes", "").strip())
    if cid and f.get("source") in ("Call", "Text"):
        from .crm import log_activity
        log_activity(cid, f["source"], datetime.now().strftime("%Y-%m-%d %H:%M"),
                     subject=f.get("purpose", "") or "Asked for a visit", notes=f"Added to route for {day}")
    flash(f"Added to {day}.", "ok")
    nxt = f.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("plan.day", day=day))


@bp.route("/routes/day/<day>")
@login_required
def day(day):
    d = _day(day) or abort(404)
    res = plan_day(day, optimize=False)
    stops = stops_for(day)
    legs = {l["stop_id"]: l for l in res["legs"]}
    # ETA timeline
    try:
        t = datetime.combine(d, datetime.strptime(res["start_time"], "%H:%M").time())
    except ValueError:
        t = datetime.combine(d, datetime.strptime("08:00", "%H:%M").time())
    visit_default = int(setting("route_visit_minutes") or 20)
    rows, order_addrs = [], []
    ordered = sorted(stops, key=lambda s: (s["id"] not in legs, s["position"], s["id"]))
    for s in ordered:
        leg = legs.get(s["id"])
        arrive = None
        if leg and s["status"] != "Skipped":
            t += timedelta(seconds=leg["s"])
            arrive = t
            t += timedelta(minutes=s["visit_min"] or visit_default)
            order_addrs.append(s["address"] or s["label"])
        rows.append({"s": s, "leg": leg, "arrive": arrive, "leg_txt": fmt_dur(leg["s"]) if leg else "",
                     "miles": round(leg["m"] / 1609.34, 1) if leg else None})
    finish = t + timedelta(seconds=res["back"]["s"]) if res.get("back") else t
    gmaps = geo.google_maps_link(res["start_address"], order_addrs, res["round_trip"])
    points = [{"lat": r["s"]["lat"], "lon": r["s"]["lon"], "label": r["s"]["label"] or r["s"]["cname"],
               "n": i + 1, "status": r["s"]["status"]} for i, r in enumerate(rows) if r["s"]["lat"] is not None]
    customers = q("SELECT id, name, city FROM customers ORDER BY name COLLATE NOCASE")
    week_days = [(d - timedelta(days=d.weekday()) + timedelta(days=i)).isoformat() for i in range(14)]
    return render_template("route_day.html", day=day, d=d, rows=rows, res=res, total=fmt_dur(res["total_s"]),
                           miles=round(res["total_m"] / 1609.34, 1), finish=finish, gmaps=gmaps,
                           points=points, start=res["start"], customers=customers, week_days=week_days,
                           has_key=bool(_key()), visit_default=visit_default, geometry=res.get("geometry"),
                           items=ESTIMATE_ITEMS)


@bp.route("/routes/day/<day>/sheet")
@login_required
def sheet(day):
    """Simple stop list for the day: where, when, what I'm estimating, notes. Printable / copyable."""
    d = _day(day) or abort(404)
    rd = q("SELECT legs FROM route_days WHERE day=?", (day,), one=True)
    legs = {}
    if rd and rd["legs"]:
        legs = {l["stop_id"]: l for l in json.loads(rd["legs"]).get("legs", [])}
    _, start_time = _start(day)
    try:
        t = datetime.combine(d, datetime.strptime(start_time, "%H:%M").time())
    except ValueError:
        t = datetime.combine(d, datetime.strptime("08:00", "%H:%M").time())
    visit_default = int(setting("route_visit_minutes") or 20)
    rows = []
    for s in stops_for(day):
        arrive = None
        if s["id"] in legs and s["status"] != "Skipped":
            t += timedelta(seconds=legs[s["id"]]["s"])
            arrive = t
            t += timedelta(minutes=s["visit_min"] or visit_default)
        rows.append({"s": s, "arrive": arrive})
    lines = [f"Route - {d:%a %m/%d}"]
    for i, r in enumerate(rows, 1):
        s = r["s"]
        lines.append(f"{i}. {s['label'] or s['cname']}" + (f" ({r['arrive']:%I:%M %p})".replace("(0", "(") if r["arrive"] else ""))
        if s["address"]:
            lines.append(f"   {s['address']}")
        if s["products"]:
            lines.append(f"   Estimating: {s['products']}")
        if s["notes"]:
            lines.append(f"   Notes: {s['notes']}")
    return render_template("route_sheet.html", d=d, day=day, rows=rows, text="\n".join(lines))


@bp.route("/routes/day/<day>/optimize", methods=["POST"])
@login_required
def optimize(day):
    _day(day) or abort(404)
    res = plan_day(day, optimize=True)
    if res["errors"]:
        flash("; ".join(res["errors"]), "error")
    else:
        flash(f"Route optimized: {fmt_dur(res['total_s'])} of driving"
              + (" (estimated - add a free OpenRouteService key in Settings for real road times)" if res["estimated"] else "")
              + ".", "ok")
    return redirect(url_for("plan.day", day=day))


@bp.route("/routes/day/<day>/settings", methods=["POST"])
@login_required
def day_settings(day):
    _day(day) or abort(404)
    x("""INSERT INTO route_days(day, start_address, start_time) VALUES (?,?,?)
         ON CONFLICT(day) DO UPDATE SET start_address=excluded.start_address, start_time=excluded.start_time""",
      (day, request.form.get("start_address", "").strip() or None, request.form.get("start_time") or None))
    return redirect(url_for("plan.day", day=day))


@bp.route("/routes/stop/<int:sid>", methods=["POST"])
@login_required
def stop_action(sid):
    s = q("SELECT * FROM route_stops WHERE id=?", (sid,), one=True) or abort(404)
    act = request.form.get("act")
    day = s["day"]
    if act in ("up", "down"):
        st = list(stops_for(day))
        ids = [r["id"] for r in st]
        i = ids.index(sid)
        j = i - 1 if act == "up" else i + 1
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
            for p, k in enumerate(ids):
                x("UPDATE route_stops SET position=? WHERE id=?", (p, k))
    elif act == "move":
        nd = request.form.get("to_day")
        if _day(nd):
            x("DELETE FROM route_stops WHERE id=?", (sid,))
            add_stop(nd, customer_id=s["customer_id"], label=s["label"], address=s["address"], purpose=s["purpose"],
                     source=s["source"], visit_min=s["visit_min"], products=s["products"], notes=s["notes"])
            flash(f"Moved to {nd}.", "ok")
    elif act in ("Done", "Skipped", "Planned"):
        x("UPDATE route_stops SET status=? WHERE id=?", (act, sid))
        if act == "Done" and s["customer_id"] and not s["activity_id"]:
            from .crm import log_activity
            est = f"Estimating: {s['products']}" if s["products"] else ""
            aid = log_activity(s["customer_id"], "Site visit", datetime.now().strftime("%Y-%m-%d %H:%M"),
                               location=s["address"], subject=s["purpose"] or "Route stop",
                               notes="\n".join(v for v in [est, s["notes"] or ""] if v))
            x("UPDATE route_stops SET activity_id=? WHERE id=?", (aid, sid))
    elif act == "notes":
        f = request.form
        x("UPDATE route_stops SET purpose=coalesce(?, purpose), visit_min=coalesce(?, visit_min), notes=?, products=? "
          "WHERE id=?", (f.get("purpose"), f.get("visit_min", type=int), f.get("notes", "").strip(),
                         ", ".join(f.getlist("products")), sid))
    elif act == "delete":
        x("DELETE FROM route_stops WHERE id=?", (sid,))
    nxt = request.form.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("plan.day", day=day))


@bp.route("/routes/geocode_all", methods=["POST"])
@login_required
def geocode_all():
    """Find map locations for customers in the background (rate-limited)."""
    import threading
    from flask import current_app
    app = current_app._get_current_object()

    def work():
        with app.app_context():
            rows = q("SELECT * FROM customers WHERE lat IS NULL AND coalesce(address,'')<>'' LIMIT 1000")
            for c in rows:
                try:
                    pt = geo.geocode(geo.full_address(c), _key(), get_db())
                except geo.GeoError:
                    break
                if pt:
                    x("UPDATE customers SET lat=?, lon=?, geo_address=? WHERE id=?",
                      (pt[0], pt[1], geo.full_address(c), c["id"]))
    threading.Thread(target=work, daemon=True).start()
    flash("Finding map locations for all properties in the background (about 1 per second). "
          "You can keep working.", "ok")
    return redirect(url_for("plan.week"))


@bp.route("/api/route_progress")
@login_required
def route_progress():
    r = q("SELECT COUNT(*) n, SUM(CASE WHEN lat IS NOT NULL THEN 1 ELSE 0 END) g FROM customers "
          "WHERE coalesce(address,'')<>''", one=True)
    return jsonify({"total": r["n"], "located": r["g"] or 0})
