"""Route planner: a rolling 7 days of stops, driving order, drive times, map, and a trip log of past days."""
import json
import time
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from .. import geo
from ..db import ESTIMATE_ITEMS, ORDER_ITEMS, get_db, now, q, setting, x
from ..security import login_required

bp = Blueprint("plan", __name__)
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _day(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _key():
    return (setting("ors_api_key") or "").strip().strip('"').strip("'").strip() or None


def stops_for(day):
    return q("""SELECT s.*, c.name AS cname, c.phone AS cphone, a.ordered AS ordered, a.order_what AS order_what,
                a.contact_name AS visit_contact, a.kind AS visit_kind, a.notes AS visit_notes FROM route_stops s
                LEFT JOIN customers c ON c.id=s.customer_id LEFT JOIN activities a ON a.id=s.activity_id
                WHERE s.day=? ORDER BY s.position, s.id""", (day,))


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


PINS = {"first": "📌 First", "last": "📌 Last", "here": "📌 Keep this #"}


def _pinned_order(rest, pins, done, ok_stops, dur, cur, round_trip):
    """Order the not-yet-visited stops with pinned ones held in place. rest/done are 1-based indexes into
    ok_stops (= rows of the duration matrix)."""
    n = len(rest)
    slots = [None] * n
    by_pos = sorted(rest, key=lambda i: (ok_stops[i - 1]["position"], ok_stops[i - 1]["id"]))
    firsts = [i for i in by_pos if pins[i] == "first"]
    lasts = [i for i in by_pos if pins[i] == "last"]
    for k, i in enumerate(firsts):
        slots[k] = i
    for k, i in enumerate(reversed(lasts)):
        if slots[n - 1 - k] is None:
            slots[n - 1 - k] = i
    # "keep this #": the stop number it has now (counting visited stops), nearest open slot if taken
    all_by_pos = sorted(done + rest, key=lambda i: (ok_stops[i - 1]["position"], ok_stops[i - 1]["id"]))
    for i in [i for i in by_pos if pins[i] == "here"]:
        want = min(max(all_by_pos.index(i) - len(done), 0), n - 1)
        for d in range(n):
            for k in (want - d, want + d):
                if 0 <= k < n and slots[k] is None:
                    slots[k] = i
                    break
            else:
                continue
            break
    placed = {v for v in slots if v is not None}
    free = [i for i in rest if i not in placed]
    if not free:
        return slots
    return geo.optimize_with_slots(dur, slots, free, start=cur, round_trip=round_trip, home=0)


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
    if not start_pt and start_addr:
        msg = f"Couldn't find your start address on the map: \"{start_addr}\". Check it in Settings or below."
        if msg not in errors:
            errors.append(msg)
    if not start_pt or not ok_stops:
        return result
    allp = [start_pt] + pts
    dur, dist, est = geo.matrix(allp, _key())
    if est and _key() and geo.LAST.get("matrix"):
        errors.append("Drive times below are estimates - OpenRouteService drive times failed: " + geo.LAST["matrix"])
    order = list(range(1, len(allp)))
    if optimize and len(ok_stops) > 1:
        # stops already visited stay first (in the order you did them); only the rest of the day is
        # re-planned, starting from where you are now (the last stop marked Done)
        done = sorted([i for i, s in enumerate(ok_stops, 1) if s["status"] == "Done"],
                      key=lambda i: (ok_stops[i - 1]["done_at"] or "", ok_stops[i - 1]["position"]))
        rest = [i for i in range(1, len(allp)) if i not in done]
        cur = done[-1] if done else 0
        pins = {i: (ok_stops[i - 1]["pin"] or "") for i in rest}
        if len(rest) > 1 and any(pins.values()):
            # pinned stops stay put (first / last / this stop #); only the others are re-ordered around them
            rest = _pinned_order(rest, pins, done, ok_stops, dur, cur, round_trip)
        elif len(rest) > 1:
            sub = [cur] + rest
            sdur = [[dur[a][b] for b in sub] for a in sub]
            if cur and round_trip:   # trip home still ends at the start address
                for r_ in range(len(sub)):
                    sdur[r_][0] = dur[sub[r_]][0]
            rest = [sub[i] for i in geo.optimize_order(sdur, round_trip)]
        order = done + rest
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
            smart_insert(day, sid)
        except geo.GeoError:
            pass
    return sid


def smart_insert(day, sid):
    """Slot a new stop into the part of the day you haven't driven yet, where it adds the least driving.
    Visited (Done) stops are never re-routed; the trip continues from the last one you marked Done."""
    all_st = [s for s in stops_for(day) if s["id"] != sid]
    done = sorted([s for s in all_st if s["status"] == "Done"], key=lambda s: (s["done_at"] or "", s["position"]))
    skipped = [s for s in all_st if s["status"] == "Skipped"]
    rest = [s for s in all_st if s["status"] not in ("Done", "Skipped")]
    new = q("SELECT * FROM route_stops WHERE id=?", (sid,), one=True)
    ins = len(rest)
    if rest:
        start_addr, _ = _start(day)
        start_pt = geo.geocode(start_addr, _key(), get_db()) if start_addr else None
        cur_pt = _stop_point(done[-1]) if done else start_pt
        new_pt = _stop_point(new)
        pts = [_stop_point(s) for s in rest]
        if cur_pt and new_pt and all(pts):
            pts_all = [cur_pt] + pts + [new_pt]
            dur, _, _ = geo.matrix(pts_all + ([start_pt] if start_pt else []), _key())
            n = len(pts_all)
            if start_pt and setting("route_return_to_start") == "1":   # last leg goes home, not back to "cur"
                for r_ in range(n):
                    dur[r_][0] = dur[r_][n]
            sub = [row[:n] for row in dur[:n]]
            ins = geo.best_insertion(sub, list(range(1, len(rest) + 1)), n - 1, setting("route_return_to_start") == "1")
    order = [s["id"] for s in done] + [s["id"] for s in rest]
    order.insert(len(done) + ins, sid)
    order += [s["id"] for s in skipped]
    for p_, i in enumerate(order):
        x("UPDATE route_stops SET position=? WHERE id=?", (p_, i))


# the CRM entry under each stop: how you were in touch -> the CRM entry type
CRM_HOW = {"In person": "Site visit", "Call": "Call", "Text": "Text", "Email": "Email"}


def _note_into(aid, text):
    """Add text to a CRM entry's notes (once)."""
    text = (text or "").strip()
    if not aid or not text:
        return
    a = q("SELECT notes FROM activities WHERE id=?", (aid,), one=True)
    have = (a["notes"] or "") if a else ""
    if text not in have:
        x("UPDATE activities SET notes=? WHERE id=?", ("\n".join(v for v in [have.strip(), text] if v), aid))


def _set_who(aid, cid, who):
    """Who you talked to: a saved contact at the property when the name matches one."""
    who = (who or "").strip()
    if not aid or not who:
        return
    from ..people import find_person
    k = find_person(cid, who) if cid else None
    x("UPDATE activities SET contact_id=?, contact_name=? WHERE id=?", (k["id"] if k else None, who, aid))


def mark_done(stop, notes="", activity_id=None, ordered=None, order_what=None, how=None, who=None):
    """Mark a stop visited and record it in the CRM as ONE entry for that property and day:
    - how = In person / Call / Text / Email (in person = a site visit), who = the person you talked to
      (blank: the property's contact - the Maint. Super, or the PM when there's no super)
    - if something was already logged in person there today (an order, a visit from the CRM page) that
      entry is used instead of adding a second one
    - the stop's notes go on the entry; ordered = "Yes"/"No" and order_what = what they ordered too."""
    x("UPDATE route_stops SET status='Done', done_at=? WHERE id=?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), stop["id"]))
    aid = activity_id or stop["activity_id"]
    cid = stop["customer_id"]
    kind = CRM_HOW.get(how or "In person", "Site visit")
    est = f"Estimating: {stop['products']}" if stop["products"] else ""
    if not aid and cid:
        from ..contact_log import IN_PERSON
        same = None
        if kind == "Site visit":
            same = q(f"""SELECT id FROM activities WHERE customer_id=? AND substr(occurred_at,1,10)=?
                         AND (kind IN ({','.join('?' * len(IN_PERSON))}) OR owner='auto') ORDER BY id LIMIT 1""",
                     (cid, stop["day"], *IN_PERSON), one=True)
        if same:
            aid = same["id"]
            x("UPDATE activities SET kind='Site visit', location=coalesce(nullif(location,''), ?), "
              "subject=coalesce(nullif(subject,''), ?) WHERE id=?",
              (stop["address"], stop["purpose"] or "Route stop", aid))
            _note_into(aid, "\n".join(v for v in [est, stop["notes"] or "", notes] if v))
        else:
            from .crm import log_activity
            aid = log_activity(cid, kind, f"{stop['day']} {datetime.now():%H:%M}",
                               location=stop["address"] if kind == "Site visit" else "",
                               subject=stop["purpose"] or "Route stop",
                               notes="\n".join(v for v in [est, stop["notes"] or "", notes] if v))
    elif aid:
        if how:
            x("UPDATE activities SET kind=? WHERE id=?", (kind, aid))
        _note_into(aid, "\n".join(v for v in [stop["notes"] or "", notes] if v))
    if aid:
        x("UPDATE route_stops SET activity_id=? WHERE id=?", (aid, stop["id"]))
        if cid:
            _set_who(aid, cid, who)
            from ..people import order_contact
            a = q("SELECT contact_name FROM activities WHERE id=?", (aid,), one=True)
            k = order_contact(cid)
            if k and a and not (a["contact_name"] or "").strip():
                x("UPDATE activities SET contact_id=?, contact_name=? WHERE id=?", (k["id"], k["name"], aid))
        set_ordered(aid, ordered, order_what)
    return aid


def edit_entry(stop, how=None, who=None, notes=None):
    """Change a visited stop's CRM entry from the route (how, who, notes)."""
    aid = stop["activity_id"]
    if not aid:
        return
    if how in CRM_HOW:
        x("UPDATE activities SET kind=? WHERE id=?", (CRM_HOW[how], aid))
    _set_who(aid, stop["customer_id"], who)
    if notes is not None:
        x("UPDATE activities SET notes=? WHERE id=?", (notes.strip() or None, aid))
        x("UPDATE route_stops SET notes=? WHERE id=?", (notes.strip() or None, stop["id"]))


def _stop_people(stops):
    """{customer id: {"names": [(name, role)], "default": the property's contact}} for the CRM box's Who."""
    from ..people import order_contact
    out = {}
    for st in stops:
        cid = st["customer_id"]
        if not cid or cid in out:
            continue
        k = order_contact(cid)
        out[cid] = {"names": [(r["name"], r["role"] or "") for r in
                              q("SELECT name, role FROM contacts WHERE customer_id=? AND coalesce(trim(name),'')<>'' "
                                "ORDER BY name", (cid,))],
                    "default": k["name"] if k else ""}
    return out


def carry_notes(day):
    """Route stops visited before the CRM box replaced the notes box: their notes go into the stop's CRM entry
    (nothing is lost)."""
    for s_ in q("""SELECT id, notes, activity_id FROM route_stops WHERE day=? AND status='Done'
                   AND coalesce(notes,'')<>'' AND activity_id IS NOT NULL""", (day,)):
        _note_into(s_["activity_id"], s_["notes"])


def set_ordered(aid, ordered, order_what=None):
    """Record on the visit whether they ordered and what (None leaves it as it was)."""
    if not aid or ordered not in ("Yes", "No"):
        return
    what = (order_what or "").strip() if ordered == "Yes" else ""
    x("UPDATE activities SET ordered=?, order_what=? WHERE id=?", (ordered, what or None, aid))


def _order_what(form):
    picks = [v for v in form.getlist("order_items") if v in ORDER_ITEMS]
    note = form.get("order_note", "").strip()
    return ", ".join(picks) + (f" - {note}" if picks and note else note)


def fmt_dur(s):
    s = int(round(s or 0))
    h, m = divmod(s // 60, 60)
    return f"{h} h {m} min" if h else f"{m} min"


# ---------------------------------------------------------------- pages
ROLLING_DAYS = 7


def log_past_days():
    """Save finished days (before today) to the trip log: stops, visits, miles, drive time.
    Days roll off the planner once they're over; this is where they're kept for expenses/reports."""
    today = date.today().isoformat()
    days = [r["day"] for r in q("SELECT DISTINCT day FROM route_stops WHERE day<? ORDER BY day", (today,))]
    for ds in days:
        st = stops_for(ds)
        if not st:
            continue
        rd = q("SELECT * FROM route_days WHERE day=?", (ds,), one=True)   # saved whenever the day was planned
        visited = [s for s in st if s["status"] == "Done"]
        places = "; ".join((s["label"] or s["cname"] or s["address"] or "").strip() for s in visited) or ""
        x("""INSERT INTO trip_log(day, stops, visited, skipped, miles, drive_s, estimated, start_address, places, updated)
             VALUES (?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(day) DO UPDATE SET stops=excluded.stops, visited=excluded.visited, skipped=excluded.skipped,
               miles=excluded.miles, drive_s=excluded.drive_s, estimated=excluded.estimated,
               start_address=excluded.start_address, places=excluded.places, updated=excluded.updated""",
          (ds, len(st), len(visited), sum(1 for s in st if s["status"] == "Skipped"),
           round(rd["total_m"] / 1609.34, 1) if rd and rd["total_m"] else 0.0,
           rd["total_drive_s"] if rd else 0, int(bool(rd and rd["estimated"])),
           (rd["start_address"] if rd and rd["start_address"] else setting("route_start_address")) or "",
           places, now()))


@bp.route("/routes")
@login_required
def week():
    """The old 7-day route page is part of the Week plan now (one Routes tab: Today's route | Week plan)."""
    try:
        log_past_days()
    except Exception:   # noqa: BLE001 - never block the planner on the log
        pass
    return redirect(url_for("plan.overview", view="week", start=request.args.get("start", "")))


@bp.route("/routes/log")
@login_required
def trip_log():
    """Past days: stops, visits, miles and drive time - for expenses and reporting."""
    try:
        log_past_days()
    except Exception:   # noqa: BLE001
        pass
    month = (request.args.get("month") or date.today().isoformat()[:7])[:7]
    rows = q("SELECT * FROM trip_log WHERE substr(day,1,7)=? ORDER BY day DESC", (month,))
    months = [r["m"] for r in q("SELECT DISTINCT substr(day,1,7) m FROM trip_log ORDER BY m DESC")]
    if month not in months:
        months = sorted(set(months) | {month}, reverse=True)
    tot = {"days": len(rows), "stops": sum(r["stops"] or 0 for r in rows),
           "visited": sum(r["visited"] or 0 for r in rows), "miles": round(sum(r["miles"] or 0 for r in rows), 1),
           "drive": fmt_dur(sum(r["drive_s"] or 0 for r in rows))}
    if request.args.get("csv"):
        import csv
        import io
        from flask import Response
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Date", "Day", "Stops planned", "Visited", "Skipped", "Miles", "Drive time",
                    "Miles are estimated", "Start address", "Places visited"])
        for r in sorted(rows, key=lambda r: r["day"]):
            dd = _day(r["day"])
            w.writerow([r["day"], dd.strftime("%A") if dd else "", r["stops"], r["visited"], r["skipped"],
                        r["miles"], fmt_dur(r["drive_s"]), "Yes" if r["estimated"] else "No",
                        r["start_address"], r["places"]])
        w.writerow([])
        w.writerow(["Total", "", tot["stops"], tot["visited"], "", tot["miles"], tot["drive"]])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=Trip log {month}.csv"})
    for_view = [dict(r, dname=(_day(r["day"]).strftime("%a %m/%d") if _day(r["day"]) else r["day"]),
                     drive=fmt_dur(r["drive_s"])) for r in rows]
    return render_template("trip_log.html", rows=for_view, month=month, months=months, tot=tot)


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
    sid = add_stop(day, customer_id=cid, label=f.get("label", "").strip() or addr, address=addr if not cid else "",
             purpose=f.get("purpose", "").strip(), source=f.get("source", ""),
             visit_min=f.get("visit_min", type=int), products=", ".join(f.getlist("products")),
             notes=f.get("notes", "").strip())
    if cid and f.get("source") in ("Call", "Text"):
        from .crm import log_activity
        log_activity(cid, f["source"], datetime.now().strftime("%Y-%m-%d %H:%M"),
                     subject=f.get("purpose", "") or "Asked for a visit", notes=f"Added to route for {day}")
    try:
        plan_day(day)
    except geo.GeoError:
        pass
    ordered = [r["id"] for r in stops_for(day)]
    pos = ordered.index(sid) + 1 if sid in ordered else len(ordered)
    dname = "today" if day == date.today().isoformat() else (_day(day) or date.today()).strftime("%A %m/%d")
    flash(f"Added to {dname} as stop {pos} of {len(ordered)} (slotted in where it adds the least driving).", "ok")
    nxt = f.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("plan.day", day=day))


@bp.route("/routes/day/<day>")
@login_required
def day(day):
    d = _day(day) or abort(404)
    carry_notes(day)
    res = plan_day(day, optimize=False)
    stops = stops_for(day)
    legs = {l["stop_id"]: l for l in res["legs"]}
    tl, t = day_timeline(day, legs)
    visit_default = int(setting("route_visit_minutes") or 20)
    rows, order_addrs = [], []
    live = d == date.today()
    for r in sorted(tl, key=lambda r: (r["leg"] is None, r["s"]["position"], r["s"]["id"])):
        s, leg = r["s"], r["leg"]
        if leg and s["status"] not in ("Skipped",) and not (live and s["status"] == "Done"):
            order_addrs.append(s["address"] or s["label"])
        rows.append({"s": s, "leg": leg, "arrive": r["arrive"], "leg_txt": fmt_dur(leg["s"]) if leg else "",
                     "miles": round(leg["m"] / 1609.34, 1) if leg else None})
    stops = [r["s"] for r in rows]
    finish = t + timedelta(seconds=res["back"]["s"]) if res.get("back") else t
    any_done = any(r["s"]["status"] == "Done" for r in rows)
    # on the road: Google Maps starts from your current location and only covers the stops left
    gmaps = geo.google_maps_link("" if (live and any_done) else res["start_address"], order_addrs, res["round_trip"],
                                 home=res["start_address"])
    points = [{"lat": r["s"]["lat"], "lon": r["s"]["lon"], "label": r["s"]["label"] or r["s"]["cname"],
               "n": i + 1, "status": r["s"]["status"]} for i, r in enumerate(rows) if r["s"]["lat"] is not None]
    customers = q("SELECT id, name, city FROM customers ORDER BY name COLLATE NOCASE")
    week_days = [(date.today() + timedelta(days=i)).isoformat() for i in range(14)]
    return render_template("route_day.html", day=day, d=d, rows=rows, res=res, total=fmt_dur(res["total_s"]),
                           miles=round(res["total_m"] / 1609.34, 1), finish=finish, gmaps=gmaps,
                           points=points, start=res["start"], customers=customers, week_days=week_days,
                           has_key=bool(_key()), visit_default=visit_default, geometry=res.get("geometry"),
                           items=ESTIMATE_ITEMS, order_items=ORDER_ITEMS, people=_stop_people(stops),
                           crm_how=list(CRM_HOW), how_of={v: k for k, v in CRM_HOW.items()},
                           check=route_check(day, res), pins=PINS)


def route_check(day, res):
    """Plain report of every piece the drive times depend on - shown on the day page, easy to copy."""
    from .. import __version__
    rd = q("SELECT start_address FROM route_days WHERE day=?", (day,), one=True)
    day_addr = (rd["start_address"] or "").strip() if rd else ""
    key = _key()
    stops = [s for s in stops_for(day) if s["status"] != "Skipped"]
    missing = [s["label"] or s["cname"] or "?" for s in stops if s["id"] in res.get("missing", [])]
    if res.get("legs") and not res.get("estimated"):
        drive = "Real road times (OpenRouteService)"
    elif res.get("legs"):
        drive = "Estimated from straight-line distance" + (
            f" - OpenRouteService failed: {geo.LAST.get('matrix')}" if key and geo.LAST.get("matrix") else
            "" if key else " - no OpenRouteService key saved")
    else:
        drive = "Not calculated"
    items = [
        ("App version", __version__, True),
        ("Start address", (res.get("start_address") or "(none)") +
         (" - set for this day only" if day_addr else " - from Settings" if res.get("start_address") else ""),
         bool(res.get("start_address"))),
        ("Start found on the map", ("yes (%.4f, %.4f)" % tuple(res["start"])) if res.get("start") else "NO",
         bool(res.get("start"))),
        ("Stops found on the map", f"{len(stops) - len(missing)} of {len(stops)}" +
         (f" - not found: {', '.join(missing)}" if missing else ""), not missing and bool(stops)),
        ("Drive times", drive, bool(res.get("legs")) and not (key and res.get("estimated"))),
        ("OpenRouteService key", f"saved (ends …{key[-6:]})" if key else "none", bool(key)),
        ("Return to start", "yes" if res.get("round_trip") else "no", True),
        ("Properties on the map", f"{q('SELECT COUNT(*) n FROM customers WHERE lat IS NOT NULL', one=True)['n']} of "
         f"{q('SELECT COUNT(*) n FROM customers', one=True)['n']}", True),
        ("Last map problem", setting("map_last_error") or "none", not setting("map_last_error")),
    ]
    for e in res.get("errors") or []:
        items.append(("Error", e, False))
    return {"items": items, "ok": all(ok for _, _, ok in items)}


def day_timeline(day, legs=None):
    """Stops for a day with arrival times. On today's route, visited stops use the time you marked them
    Done and the rest of the day is re-timed from now - so the ETAs follow where you actually are."""
    d = _day(day)
    if legs is None:
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
    live = d == date.today()
    anchored = False
    rows = []
    for s in stops_for(day):
        leg = legs.get(s["id"])
        arrive = None
        if s["status"] == "Done":
            if live and s["done_at"]:
                try:
                    t = datetime.strptime(s["done_at"], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass
            elif leg:
                t += timedelta(seconds=leg["s"]) + timedelta(minutes=s["visit_min"] or visit_default)
        elif leg and s["status"] != "Skipped":
            if live and not anchored:
                t = max(t, datetime.now())
                anchored = True
            t += timedelta(seconds=leg["s"])
            arrive = t
            t += timedelta(minutes=s["visit_min"] or visit_default)
        rows.append({"s": s, "arrive": arrive, "leg": leg})
    return rows, t


@bp.route("/routes/day/<day>/sheet")
@login_required
def sheet(day):
    """Simple stop list for the day: where, when, what I'm estimating, notes. Printable / copyable."""
    d = _day(day) or abort(404)
    rows, _ = day_timeline(day)
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
        done_n = sum(1 for st in stops_for(day) if st["status"] == "Done")
        pinned = sum(1 for st in stops_for(day) if st["pin"] and st["status"] == "Planned")
        flash(("Rest of the day re-planned from your last visited stop" if done_n else "Route optimized")
              + (f" around {pinned} pinned stop{'s' if pinned != 1 else ''}" if pinned else "")
              + f": {fmt_dur(res['total_s'])} of driving"
              + (" (estimated - add a free OpenRouteService key in Settings for real road times)" if res["estimated"] else "")
              + ".", "ok")
    nxt = request.form.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("plan.day", day=day))


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
    act = request.form.get("act2") or request.form.get("act")     # act2: a 2nd button on the same form
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
    elif act == "pos":            # jump straight to stop # N (instead of clicking the arrows N times)
        ids = [r["id"] for r in stops_for(day)]
        to = request.form.get("to", type=int)
        if to and sid in ids:
            ids.remove(sid)
            to = min(max(to, 1), len(ids) + 1)
            ids.insert(to - 1, sid)
            for p, k in enumerate(ids):
                x("UPDATE route_stops SET position=? WHERE id=?", (p, k))
            # a stop you put somewhere on purpose stays there when you hit Optimize (unpin it to let it move)
            pin = "first" if to == 1 else ("last" if to == len(ids) else "here")
            x("UPDATE route_stops SET pin=? WHERE id=?", (pin, sid))
    elif act == "move":
        nd = request.form.get("to_day")
        if _day(nd):
            x("DELETE FROM route_stops WHERE id=?", (sid,))
            add_stop(nd, customer_id=s["customer_id"], label=s["label"], address=s["address"], purpose=s["purpose"],
                     source=s["source"], visit_min=s["visit_min"], products=s["products"], notes=s["notes"])
            dd = _day(nd)
            flash(f"Moved to {'tomorrow' if dd == date.today() + timedelta(days=1) else dd.strftime('%A %m/%d')} "
                  f"- it's on that day's route.", "ok")
    elif act == "Done":
        f = request.form
        if "notes" in f:                  # the CRM box's notes are the stop's notes
            x("UPDATE route_stops SET notes=? WHERE id=?", (f.get("notes", "").strip() or None, sid))
            s = q("SELECT * FROM route_stops WHERE id=?", (sid,), one=True)
        mark_done(s, ordered=f.get("ordered") or None, order_what=_order_what(f),
                  how=f.get("crm_how") or None, who=f.get("crm_who") or None)
    elif act == "crm":            # change a visited stop's CRM entry (how / who / notes)
        f = request.form
        if s["status"] != "Done" or not s["activity_id"]:
            mark_done(s)
            s = q("SELECT * FROM route_stops WHERE id=?", (sid,), one=True)
        edit_entry(s, f.get("crm_how"), f.get("crm_who"), f.get("notes"))
        set_ordered(s["activity_id"], f.get("ordered"), _order_what(f))
        flash("CRM entry saved.", "ok")
    elif act == "crmnote":        # save the note without marking the stop visited yet
        x("UPDATE route_stops SET notes=? WHERE id=?", (request.form.get("notes", "").strip() or None, sid))
    elif act == "ordered":        # change "did they order / what" after the stop was marked visited
        if s["status"] != "Done" or not s["activity_id"]:
            mark_done(s)
            s = q("SELECT * FROM route_stops WHERE id=?", (sid,), one=True)
        set_ordered(s["activity_id"], request.form.get("ordered"), _order_what(request.form))
    elif act == "pin":            # hold this stop first / last / at its number when optimizing
        v = request.form.get("pin", "")
        x("UPDATE route_stops SET pin=? WHERE id=?", (v if v in PINS else None, sid))
    elif act in ("Skipped", "Planned"):
        x("UPDATE route_stops SET status=?, done_at=NULL WHERE id=?", (act, sid))
    elif act == "notes":
        f = request.form
        x("UPDATE route_stops SET purpose=coalesce(?, purpose), visit_min=coalesce(?, visit_min), products=? "
          "WHERE id=?", (f.get("purpose"), f.get("visit_min", type=int), ", ".join(f.getlist("products")), sid))
        if "notes" in f:          # (stops without a property keep their notes box here)
            x("UPDATE route_stops SET notes=? WHERE id=?", (f.get("notes", "").strip(), sid))
    elif act == "delete":
        x("DELETE FROM route_stops WHERE id=?", (sid,))
    if act in ("Done", "Skipped", "Planned", "move", "delete", "up", "down", "pos"):
        try:
            plan_day(day)          # refresh legs / arrival times for the rest of the day
        except geo.GeoError:
            pass
    nxt = request.form.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("plan.day", day=day))


@bp.route("/routes/day/<day>/along")
@login_required
def along(day):
    """Suggested stops along the way (HTML piece loaded into the route page). ?skip=ids for Refresh, ?wide=1."""
    from .. import along as AL
    _day(day) or abort(404)
    skip = [int(v) for v in (request.args.get("skip") or "").split(",") if v.strip().isdigit()]
    a = request.args
    from ..db import set_setting
    mode = a.get("mode") or setting("along_mode") or "route"
    if a.get("mode") in AL.MODES and a.get("mode") != setting("along_mode"):
        set_setting("along_mode", a["mode"])          # remember the last one picked
    radius = a.get("radius", type=float)
    if a.get("switched"):                             # each mode has its own distances
        radius = None
    # which stops to search near: picked on the page, remembered for that day (none picked = every stop)
    if "near_set" in a:
        only = [int(v) for v in a.getlist("near") if v.isdigit()]
        set_setting(f"along_near:{day}", ",".join(map(str, only)))
    else:
        only = [int(v) for v in (setting(f"along_near:{day}") or "").split(",") if v.isdigit()]
    res = AL.suggest(day, skip=skip, wide=bool(a.get("wide")), sort=a.get("sort", "best"),
                     page=a.get("page", 1, type=int), radius=radius, mode=mode, only=only)
    return render_template("_along.html", day=day, res=res, sorts=AL.SORTS, modes=AL.MODES,
                           radii=AL.NEAR_RADII if res["mode"] == "stops" else AL.RADII)


@bp.route("/routes/day/<day>/along/add", methods=["POST"])
@login_required
def along_add(day):
    _day(day) or abort(404)
    ids = [int(v) for v in request.form.getlist("customer_id") if v.isdigit()]
    if not ids:
        flash("Tick the properties you want to add.", "error")
        return redirect(url_for("plan.day", day=day))
    for cid in ids:
        add_stop(day, customer_id=cid, purpose=request.form.get(f"why_{cid}", "") or "On the way",
                 source=request.form.get("source") or "Along the way")
    try:
        plan_day(day)
    except geo.GeoError:
        pass
    flash(f"Added {len(ids)} stop(s), each slotted in where it adds the least driving.", "ok")
    return redirect(url_for("plan.day", day=day))


@bp.route("/routes/day/<day>/along/find", methods=["POST"])
@login_required
def along_find(day):
    """Look up map points for properties in the same cities / zips as today's stops (background)."""
    import threading
    from flask import current_app
    from .. import along as AL
    _day(day) or abort(404)
    cities, zips = AL.cities_on_route(day)
    rows = [c for c in q("SELECT * FROM customers WHERE lat IS NULL AND coalesce(address,'')<>''")
            if (c["city"] or "").strip().lower() in cities or (c["zip"] or "")[:5] in zips]
    if not rows:
        flash("Every property in these areas is already on the map - try 'Check further out'.", "info")
        return redirect(url_for("plan.day", day=day))
    app = current_app._get_current_object()
    ids = [c["id"] for c in rows[:150]]

    def work():
        with app.app_context():
            for cid in ids:
                c = q("SELECT * FROM customers WHERE id=?", (cid,), one=True)
                try:
                    pt = geo.geocode(geo.full_address(c), _key(), get_db())
                except geo.GeoError:
                    break
                if pt:
                    x("UPDATE customers SET lat=?, lon=?, geo_address=? WHERE id=?",
                      (pt[0], pt[1], geo.full_address(c), c["id"]))
    threading.Thread(target=work, daemon=True).start()
    flash(f"Finding map locations for {len(ids)} more properties near this route (about 1 a second). "
          "Click Refresh on the suggestions in a minute or two.", "ok")
    return redirect(url_for("plan.day", day=day))


NOT_FOUND = "NOT FOUND|"
_geo_job = {"running": False}


def _job(**kw):
    """Locate-all progress, kept in settings so the page can show it."""
    from ..db import loads, set_setting
    cur = loads(setting("geo_job") or "{}", {})
    cur.update(kw)
    set_setting("geo_job", json.dumps(cur))
    return cur


def locate_all(limit=None, pause=None):
    """Find map points for every property with an address. Paced for the free OpenRouteService plan
    (100 a minute); waits out a per-minute limit, stops cleanly on the daily one. Addresses no map service
    can find are marked so they aren't retried until the address changes."""
    key = _key()
    pause = (0.7 if key else 0) if pause is None else pause
    rows = [c for c in q("SELECT * FROM customers WHERE lat IS NULL AND coalesce(address,'')<>'' ORDER BY name")
            if (c["geo_address"] or "") != NOT_FOUND + geo.full_address(c)]
    if limit:
        rows = rows[:limit]
    _job(running=True, todo=len(rows), done=0, located=0, not_found=0, error="", started=now(), finished="")
    located = missing = done = errs = 0
    for c in rows:
        addr = geo.full_address(c)
        pt = None
        for attempt in range(2):
            try:
                pt = geo.geocode(addr, key, get_db())
                errs = 0
                break
            except geo.GeoError as e:
                msg = str(e)
                if "limit" in msg.lower() and attempt == 0:
                    _job(error="Waiting a minute for the map service's per-minute limit...")
                    time.sleep(65)
                    continue
                errs += 1
                _job(error=msg)
                if "limit" in msg.lower() or errs >= 3:
                    _job(running=False, finished=now(), done=done, located=located, not_found=missing,
                         error=msg + (" Click Locate all properties again tomorrow to finish."
                                      if "limit" in msg.lower() else ""))
                    return
                break
        if pt:
            x("UPDATE customers SET lat=?, lon=?, geo_address=? WHERE id=?", (pt[0], pt[1], addr, c["id"]))
            located += 1
        elif errs == 0:
            x("UPDATE customers SET geo_address=? WHERE id=?", (NOT_FOUND + addr, c["id"]))
            missing += 1
        done += 1
        if done % 10 == 0:
            _job(done=done, located=located, not_found=missing, error="")
        if pause:
            time.sleep(pause)
    _job(running=False, finished=now(), done=done, located=located, not_found=missing,
         error="" if errs == 0 else setting("map_last_error") or "")


@bp.route("/routes/geocode_all", methods=["POST"])
@login_required
def geocode_all():
    """Find map locations for every property in the background."""
    import threading
    from flask import current_app
    if _geo_job["running"]:
        flash("Already finding map locations - watch the progress on this page.", "info")
        return redirect(url_for("plan.week"))
    app = current_app._get_current_object()

    def work():
        _geo_job["running"] = True
        try:
            with app.app_context():
                locate_all()
        finally:
            _geo_job["running"] = False
            try:
                with app.app_context():
                    _job(running=False)
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=work, daemon=True).start()
    flash("Finding map locations for every property in the background (about 1 a second - roughly 15 minutes "
          "for 850). You can keep working; progress shows on this page.", "ok")
    return redirect(url_for("plan.week"))


@bp.route("/routes/unlocated")
@login_required
def unlocated():
    """Properties whose address no map service could find - fix the address and locate again."""
    rows = q("""SELECT id, name, address, city, zip, geo_address FROM customers
                WHERE lat IS NULL AND coalesce(address,'')<>'' ORDER BY name""")
    return render_template("route_unlocated.html", rows=rows, nf=NOT_FOUND)


@bp.route("/api/route_progress")
@login_required
def route_progress():
    from ..db import loads
    r = q("SELECT COUNT(*) n, SUM(CASE WHEN lat IS NOT NULL THEN 1 ELSE 0 END) g FROM customers "
          "WHERE coalesce(address,'')<>''", one=True)
    nf = q("SELECT COUNT(*) n FROM customers WHERE lat IS NULL AND geo_address LIKE ?", (NOT_FOUND + "%",),
           one=True)["n"]
    job = loads(setting("geo_job") or "{}", {})
    job["running"] = bool(_geo_job["running"])
    return jsonify({"total": r["n"], "located": r["g"] or 0, "not_found": nf, "job": job})


# ------------------------------------------------------------------ plan my week / month
@bp.route("/plan")
@login_required
def overview():
    """Week or month at a glance, who to see and why, and where the month is heading."""
    from .. import planner as PL
    view = request.args.get("view", "week")
    today = date.today()
    if view == "month":
        ym = request.args.get("month") or today.isoformat()[:7]
        try:
            y, m = int(ym[:4]), int(ym[5:7])
        except ValueError:
            y, m = today.year, today.month
        weeks = PL.month_grid(y, m)
        recs = PL.recommendations(30, today)
        prev_m = (date(y, m, 1) - timedelta(days=1)).isoformat()[:7]
        next_m = (date(y, m, 28) + timedelta(days=7)).isoformat()[:7]
        days, label = None, date(y, m, 1).strftime("%B %Y")
    else:
        start = _day(request.args.get("start")) or (today - timedelta(days=today.weekday()))
        days = PL.glance(start, 7)
        recs = PL.recommendations(7, today)
        weeks, prev_m, next_m = None, (start - timedelta(days=7)).isoformat(), (start + timedelta(days=7)).isoformat()
        label = f"Week of {start:%b %d}"
    focus, near_sugg, areas = {}, {}, []
    if view == "week":
        from .. import plan_focus as PF
        work = [d["iso"] for d in days if d["date"].weekday() < 5 and d["iso"] >= today.isoformat()] or \
            [d["iso"] for d in days if d["date"].weekday() < 5]
        focus = PF.focus_for([d["iso"] for d in days])
        near_sugg = PF.place_recs(recs, work)
        for d in days:
            d["focus"] = focus.get(d["iso"])
            d["near"] = near_sugg.get(d["iso"], [])
        for dd, rows in near_sugg.items():      # warm accounts near the day's area go in the list too
            for row in rows:
                c = row["c"]
                recs.append({"key": f"n{dd}{c['id']}", "name": c["name"], "customer_id": c["id"], "city": c["city"] or "",
                             "address": "", "kind": "visit", "score": 0, "value": row["f"]["sales12"], "links": [],
                             "why": [f"Near {row['from']} ({row['mi']} mi)" + (": " + ", ".join(row["why"]) if row["why"] else "")],
                             "day": dd, "near": f"{row['mi']} mi from {row['from']}", "suggested": True})
        recs.sort(key=lambda r: (r.get("kind") != "visit", r.get("day") or "9", bool(r.get("suggested")), -r["score"]))
        areas = sorted({(r["city"] or "").strip().title() for r in q("SELECT DISTINCT city FROM customers") if r["city"]}
                       | {k.title() for k in PF.AREAS})
    from ..along import facts as _facts
    cids = [r["customer_id"] for r in recs if r.get("customer_id")]
    crow = {c["id"]: c for c in q(f"SELECT id, name, address, city FROM customers WHERE id IN ({','.join('?' * len(cids))})",
                                    cids)} if cids else {}
    fx = _facts(list(crow.values())) if crow else {}
    for r in recs:
        r["f"] = fx.get(r.get("customer_id"))
        r["addr"] = crow[r["customer_id"]]["address"] if r.get("customer_id") in crow else ""
    proj = PL.projection(today)
    from .. import territory as T
    route_tools = dict(items=ESTIMATE_ITEMS, has_key=bool(_key()), map_error=setting("map_last_error"),
                       mapped=q("SELECT COUNT(*) n FROM customers WHERE lat IS NOT NULL", one=True)["n"],
                       unmapped=q("SELECT COUNT(*) n FROM customers WHERE lat IS NULL AND coalesce(address,'')<>''",
                                  one=True)["n"])
    return render_template("plan.html", **route_tools, terr_on=T.enabled(), terr_n=len(T.my_ids()) if T.enabled() else 0, view=view, days=days, weeks=weeks, recs=recs, proj=proj, label=label,
                           prev=prev_m, nxt=next_m, today=today.isoformat(), monday=today.weekday() == 0,
                           areas=areas, start=request.args.get("start", ""),
                           customers=q("SELECT id, name, acct_type FROM customers ORDER BY name COLLATE NOCASE"))


@bp.route("/plan/area", methods=["POST"])
@login_required
def plan_area():
    """Where you're going on a day: a city, zip, part of town or a property."""
    from .. import plan_focus as PF
    day = _day(request.form.get("day")) or abort(400)
    text = request.form.get("area", "").strip()
    r = PF.set_area(day.isoformat(), text)
    if not text:
        flash(f"Cleared the area for {day:%a %m/%d}.", "ok")
    elif r:
        flash(f"{day:%a %m/%d}: planning around {r[2]}. Suggestions near it are listed under the day and in "
              f"'Who to see'.", "ok")
    else:
        flash(f"Saved '{text}' for {day:%a %m/%d}, but couldn't place it on the map - try a city, a zip, a part of "
              "town (e.g. South Central Phoenix) or a property name. Accounts you add to the day still count.", "error")
    return redirect(url_for("plan.overview", view="week", start=request.form.get("start", "")) + f"#d{day.isoformat()}")


@bp.route("/plan/day_add", methods=["POST"])
@login_required
def plan_day_add():
    """Put an account you need to visit on a day; the day's suggestions then form around it."""
    day = _day(request.form.get("day")) or abort(400)
    name = request.form.get("account", "").strip()
    c = q("SELECT id, name FROM customers WHERE name=? COLLATE NOCASE", (name,), one=True) if name else None
    if not c:
        flash("Pick an account from the list (start typing its name).", "error")
    else:
        add_stop(day.isoformat(), customer_id=c["id"], purpose=request.form.get("purpose", "").strip() or "Planned visit",
                 source="Plan")
        try:
            plan_day(day.isoformat())
        except geo.GeoError:
            pass
        flash(f"Added {c['name']} to {day:%a %m/%d}. Accounts near it are suggested under that day.", "ok")
    return redirect(url_for("plan.overview", view="week", start=request.form.get("start", "")) + f"#d{day.isoformat()}")


@bp.route("/plan/near")
@login_required
def plan_near():
    """Accounts around a property or an area - pick the ones to add to a day."""
    from .. import plan_focus as PF
    a = request.args
    center, label = None, ""
    acct = a.get("account", "").strip()
    area = a.get("area", "").strip()
    c = q("SELECT id, name, lat, lon FROM customers WHERE name=? COLLATE NOCASE", (acct,), one=True) if acct else None
    if c and c["lat"] is not None:
        center, label = (c["lat"], c["lon"]), c["name"]
    elif area or acct:
        r = PF.resolve_area(area or acct)
        if r:
            center, label = (r[0], r[1]), r[2]
    radius = a.get("radius", type=float) or 3.0
    sort = a.get("sort", "best")
    everyone = bool(a.get("everyone"))
    rows = PF.near_accounts(center, radius, exclude=[c["id"]] if c else [], sort=sort, everyone=everyone) if center else []
    from .. import territory as T
    unplaced = bool(c and c["lat"] is None)
    return render_template("plan_near.html", rows=rows[:150], total=len(rows), label=label, acct=acct, area=area,
                           radius=radius, sort=sort, day=a.get("day") or date.today().isoformat(), unplaced=unplaced,
                           searched=bool(acct or area), center=center, everyone=everyone, terr_on=T.enabled(),
                           customers=q("SELECT id, name FROM customers ORDER BY name COLLATE NOCASE"),
                           areas=sorted({k.title() for k in PF.AREAS}))


@bp.route("/plan/add", methods=["POST"])
@login_required
def plan_add():
    """Put the ticked recommendations on their days (each slotted where it adds the least driving)."""
    f = request.form
    n = 0
    days = set()
    for key in f.getlist("pick"):
        day = f.get(f"day_{key}") or date.today().isoformat()
        cid = f.get(f"cid_{key}", type=int)
        addr = f.get(f"addr_{key}", "").strip()
        if not cid and not addr:
            continue
        add_stop(day, customer_id=cid, label=f.get(f"name_{key}", "") if not cid else "", address=addr if not cid else "",
                 purpose=f.get(f"why_{key}", "")[:200], source="Plan")
        days.add(day)
        n += 1
    for d in days:
        try:
            plan_day(d)
        except geo.GeoError:
            pass
    flash(f"Added {n} stop(s) to your route{'s' if len(days) > 1 else ''}.", "ok")
    if f.get("back"):
        return redirect(f["back"]) if f["back"].startswith("/") else redirect(url_for("plan.overview"))
    return redirect(url_for("plan.overview", view=f.get("view", "week"), start=f.get("start", "")))


@bp.route("/plan/projection", methods=["POST"])
@login_required
def plan_projection():
    """Save what you expect each planned stop to bring in, and the quote win rate (play with the numbers)."""
    from ..db import set_setting
    f = request.form
    for k, v in f.items():
        if k.startswith("exp_") and k[4:].isdigit():
            try:
                val = float(v.replace("$", "").replace(",", "")) if v.strip() else None
            except ValueError:
                continue
            x("UPDATE route_stops SET expected=? WHERE id=?", (val, int(k[4:])))
    wr = f.get("win_rate", "").strip().rstrip("%")
    if wr.replace(".", "", 1).isdigit():
        set_setting("plan_win_rate", wr)
    flash("Projection updated.", "ok")
    return redirect(url_for("plan.overview", view=f.get("view", "week")) + "#projection")
