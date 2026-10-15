"""Stops along the way: properties near a day's route worth adding, with their sales, last order and
last contact so you can pick.

Works from map points already saved on your properties (no extra map lookups while you browse). Distances
are straight-line with a road factor, so "+N min" is a rough extra-driving guide, not turn-by-turn.
"""
from datetime import date, datetime, timedelta

from . import geo
from .contact_log import IN_PERSON, _iso, last_contacts
from .db import get_db, q, setting

ROAD = 1.3          # road miles ~ 1.3 x straight-line miles in the valley
MPH = 30.0          # average city speed for the "+min" guide
NORMAL_MI = 3.0     # extra road miles that still counts as "on the way"
WIDE_MI = 8.0       # "check further out"


def _mi(a, b):
    return geo.haversine_m(a, b) / 1609.34


def route_points(day):
    """[start, stop1, stop2, ... (, start)] as (lat, lon), skipping stops without a map point."""
    from .routes.routes_plan import _key, _start, stops_for
    start_addr, _ = _start(day)
    pts = []
    try:
        sp = geo.geocode(start_addr, _key(), get_db()) if start_addr else None
    except geo.GeoError:
        sp = None
    if sp:
        pts.append(tuple(sp))
    for s in stops_for(day):
        if s["status"] == "Skipped":
            continue
        if s["lat"] is not None:
            pts.append((s["lat"], s["lon"]))
        elif s["customer_id"]:
            c = q("SELECT lat, lon FROM customers WHERE id=?", (s["customer_id"],), one=True)
            if c and c["lat"] is not None:
                pts.append((c["lat"], c["lon"]))
    if sp and len(pts) > 1 and setting("route_return_to_start") == "1":
        pts.append(tuple(sp))
    return pts


def detour(pts, c):
    """(extra road miles, index of the leg it fits after) to visit point c on the way."""
    if len(pts) == 1:
        return 2 * _mi(pts[0], c) * ROAD, 0
    best = (None, 0)
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        extra = (_mi(a, c) + _mi(c, b) - _mi(a, b)) * ROAD
        if best[0] is None or extra < best[0]:
            best = (extra, i)
    # also "after the last stop" when the day doesn't return home
    tail = _mi(pts[-1], c) * ROAD
    if tail < best[0]:
        best = (tail, len(pts) - 1)
    return best


def facts(customers):
    """{cid: {sales12, sales_all, last_order, last_contact, how, temp, who}} for display + ranking."""
    ids = [c["id"] for c in customers]
    if not ids:
        return {}
    since = (date.today() - timedelta(days=365)).isoformat()
    ph = ",".join("?" * len(ids))
    out = {i: {"sales12": 0.0, "sales_all": 0.0, "last_order": "", "last_contact": "", "how": "", "temp": "",
               "who": "", "src": ""} for i in ids}
    for r in q(f"""SELECT customer_id, SUM(CASE WHEN invoice_date>=? THEN total ELSE 0 END) s12, SUM(total) sall,
                   MAX(invoice_date) last FROM sales WHERE customer_id IN ({ph}) GROUP BY customer_id""", [since] + ids):
        o = out[r["customer_id"]]
        o.update(sales12=r["s12"] or 0, sales_all=r["sall"] or 0, last_order=r["last"] or "", src="my sales")
    # company-wide figures (all reps) when the Company Data reports are loaded - bigger picture wins
    try:
        from .company_db import company_key
        if q("SELECT 1 FROM co_docs LIMIT 1", one=True):
            for c in customers:
                k = company_key(c["name"])
                if not k:
                    continue
                r = q("""SELECT SUM(CASE WHEN doc_date>=? THEN total ELSE 0 END) s12, SUM(total) sall,
                         MAX(doc_date) last FROM co_docs WHERE cust_key=?""", (since, k), one=True)
                o = out[c["id"]]
                if r and (r["sall"] or 0) >= o["sales_all"]:
                    o.update(sales12=r["s12"] or 0, sales_all=r["sall"] or 0, src="company")
                if r and (r["last"] or "") > o["last_order"]:
                    o["last_order"] = r["last"] or ""
    except Exception:  # noqa: BLE001 - company data is optional
        pass
    for r in q(f"SELECT customer_id, order_date, created FROM orders WHERE customer_id IN ({ph})", ids):
        d = _iso(r["order_date"]) or (r["created"] or "")[:10]
        if d and d > out[r["customer_id"]]["last_order"]:
            out[r["customer_id"]]["last_order"] = d
    for cid, r in last_contacts(ids).items():
        o = out[cid]
        o.update(last_contact=r["occurred_at"][:10],
                 how="In person" if r["kind"] in IN_PERSON else r["kind"], temp=r["temp"] or "",
                 who=r["contact_name"] or "")
    return out


def _days_since(iso):
    try:
        return (date.today() - datetime.strptime(iso[:10], "%Y-%m-%d").date()).days
    except (TypeError, ValueError):
        return None


def why(f):
    """Short reasons a property is worth the stop."""
    out = []
    ds = _days_since(f["last_contact"])
    if f["sales12"] >= 1000:
        out.append(f"${f['sales12']:,.0f} last 12 mo")
    lo = _days_since(f["last_order"])
    if f["sales_all"] and lo is not None and lo > 60:
        out.append(f"no order in {lo} days")
    if ds is None:
        out.append("no contact logged" if (f["sales_all"] or f["last_order"]) else "never contacted")
    elif ds > 30:
        out.append(f"last contact {ds} days ago")
    if not f["sales_all"] and not f["last_order"]:
        out.append("prospect")
    return out


def suggest(day, n=None, skip=(), wide=False):
    """Up to n properties along the day's route, best first. skip = ids already shown (for Refresh).
    Returns {"rows": [...], "more": int, "no_points": bool, "mapped": int, "unmapped": int}."""
    n = n or int(setting("along_count") or 5)
    pts = route_points(day)
    on_route = {r["customer_id"] for r in q("SELECT customer_id FROM route_stops WHERE day=?", (day,))
                if r["customer_id"]}
    mapped = q("""SELECT id, name, address, city, lat, lon, status FROM customers WHERE lat IS NOT NULL
                  AND coalesce(status,'') NOT IN ('Lost','Inactive','Do not contact')""")
    unmapped = q("SELECT COUNT(*) n FROM customers WHERE lat IS NULL AND coalesce(address,'')<>''", one=True)["n"]
    if len(pts) < 2:     # need the start point + at least one stop to know the way
        return {"rows": [], "more": 0, "no_points": True, "mapped": len(mapped), "unmapped": unmapped}
    limit = WIDE_MI if wide else NORMAL_MI
    near = []
    for c in mapped:
        if c["id"] in on_route:
            continue
        extra, leg = detour(pts, (c["lat"], c["lon"]))
        if extra <= limit:
            near.append((extra, leg, c))
    near.sort(key=lambda t: t[0])
    near = near[:200]
    fx = facts([c for _, _, c in near])
    rows = []
    for extra, leg, c in near:
        f = fx[c["id"]]
        ds = _days_since(f["last_contact"])
        # rank: closer is better; good accounts and ones you haven't seen in a while move up
        value = min(f["sales12"] / 1500.0, 4.0) + (1.5 if ds is None else min(ds / 30.0, 3.0))
        if f["temp"] == "Cold" and not f["sales_all"]:
            value += 0.5
        score = extra - value * 0.8
        rows.append({"c": c, "extra_mi": round(extra, 1), "extra_min": max(1, round(extra / MPH * 60)),
                     "leg": leg, "f": f, "why": why(f), "score": score})
    rows.sort(key=lambda r: r["score"])
    fresh = [r for r in rows if r["c"]["id"] not in set(skip)]
    if not fresh and rows:          # went through them all - start over
        fresh = rows
    show = fresh if wide else fresh[:n]
    return {"rows": show[:40] if wide else show, "more": max(0, len(fresh) - len(show)), "no_points": False,
            "mapped": len(mapped), "unmapped": unmapped, "total": len(rows)}


def cities_on_route(day):
    """Cities and zips of the day's stops - used to find map points for nearby properties."""
    rows = q("""SELECT c.city, c.zip FROM route_stops s JOIN customers c ON c.id=s.customer_id WHERE s.day=?""",
             (day,))
    return {(r["city"] or "").strip().lower() for r in rows if r["city"]}, {(r["zip"] or "")[:5] for r in rows if r["zip"]}
