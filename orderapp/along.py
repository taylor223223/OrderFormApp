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
    else:
        return []          # without the start point "on the way" means nothing - the page says why
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
               "who": "", "src": "", "orders": 0, "avg": 0.0} for i in ids}
    for r in q(f"""SELECT customer_id, SUM(CASE WHEN invoice_date>=? THEN total ELSE 0 END) s12, SUM(total) sall,
                   MAX(invoice_date) last, COUNT(*) n FROM sales WHERE customer_id IN ({ph}) GROUP BY customer_id""",
               [since] + ids):
        o = out[r["customer_id"]]
        o.update(sales12=r["s12"] or 0, sales_all=r["sall"] or 0, last_order=r["last"] or "", src="my sales",
                 orders=r["n"] or 0)
    # company-wide figures (all reps) when the Company Data reports are loaded - bigger picture wins
    try:
        from .company_db import company_key
        if q("SELECT 1 FROM co_docs LIMIT 1", one=True):
            for c in customers:
                k = company_key(c["name"])
                if not k:
                    continue
                r = q("""SELECT SUM(CASE WHEN doc_date>=? THEN total ELSE 0 END) s12, SUM(total) sall,
                         MAX(doc_date) last, COUNT(CASE WHEN doc_type='Invoice' THEN 1 END) n
                         FROM co_docs WHERE cust_key=?""", (since, k), one=True)
                o = out[c["id"]]
                if r and (r["sall"] or 0) >= o["sales_all"]:
                    o.update(sales12=r["s12"] or 0, sales_all=r["sall"] or 0, src="company", orders=r["n"] or 0)
                if r and (r["last"] or "") > o["last_order"]:
                    o["last_order"] = r["last"] or ""
    except Exception:  # noqa: BLE001 - company data is optional
        pass
    for r in q(f"SELECT customer_id, order_date, created FROM orders WHERE customer_id IN ({ph})", ids):
        d = _iso(r["order_date"]) or (r["created"] or "")[:10]
        if d and d > out[r["customer_id"]]["last_order"]:
            out[r["customer_id"]]["last_order"] = d
    for o in out.values():
        o["avg"] = round(o["sales_all"] / o["orders"], 2) if o["orders"] else 0.0
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


SORTS = {"best": "Best fit", "closest": "Closest to route", "sales": "Sales (12 mo)",
         "last_contact": "Longest since contact", "last_order": "Longest since order", "name": "Name"}
RADII = [1, 3, 5, 10]
MODES = {"route": "Along the route", "stops": "Near my stops"}
NEAR_RADII = [0.5, 1, 2, 3, 5, 8]
NEAR_MI = 2.0       # default "near my stops" distance (straight-line miles from a stop)


def stop_points(day):
    """[(lat, lon, stop name)] for the day's stops that are on the map (no start address needed)."""
    from .routes.routes_plan import stops_for
    out = []
    for s in stops_for(day):
        if s["status"] == "Skipped":
            continue
        lat, lon = s["lat"], s["lon"]
        if lat is None and s["customer_id"]:
            c = q("SELECT lat, lon FROM customers WHERE id=?", (s["customer_id"],), one=True)
            if c and c["lat"] is not None:
                lat, lon = c["lat"], c["lon"]
        if lat is not None:
            out.append((lat, lon, s["cname"] or s["label"] or "a stop"))
    return out


def suggest(day, n=None, skip=(), wide=False, sort="best", page=1, radius=None, mode="route"):
    """Every property near the day's route (within `radius` extra miles), with its sales, last order and last
    contact as information - nothing is left out for missing data. Sorted by `sort`, `n` per page.
    Returns {"rows", "page", "pages", "total", "no_points", "mapped", "unmapped", ...}."""
    n = n or int(setting("along_count") or 10)
    mode = mode if mode in MODES else "route"
    if mode == "stops":
        radius = float(radius) if radius else NEAR_MI
        pts = stop_points(day)
    else:
        radius = float(radius) if radius else (WIDE_MI if wide else NORMAL_MI)
        pts = route_points(day)
    on_route = {r["customer_id"] for r in q("SELECT customer_id FROM route_stops WHERE day=?", (day,))
                if r["customer_id"]}
    mapped = q("""SELECT id, name, address, city, lat, lon, status FROM customers WHERE lat IS NOT NULL
                  AND coalesce(status,'') NOT IN ('Lost','Inactive','Do not contact')""")
    from .territory import enabled, my_ids
    if enabled():
        mine = my_ids()
        mapped = [c for c in mapped if c["id"] in mine]
    unmapped = q("SELECT COUNT(*) n FROM customers WHERE lat IS NULL AND coalesce(address,'')<>''", one=True)["n"]
    base = {"rows": [], "page": 1, "pages": 0, "total": 0, "more": 0, "mapped": len(mapped), "unmapped": unmapped,
            "sort": sort, "radius": radius, "mode": mode}
    if mode == "stops" and not pts:      # no stop on the map yet
        return dict(base, no_points=True)
    if mode != "stops" and len(pts) < 2:     # need the start point + at least one stop to know the way
        return dict(base, no_points=True)
    near = []
    for c in mapped:
        if c["id"] in on_route:
            continue
        if mode == "stops":     # straight-line miles to the closest stop; "leg" = that stop's name
            d, name = min((_mi((p[0], p[1]), (c["lat"], c["lon"])), p[2]) for p in pts)
            if d <= radius:
                near.append((d, name, c))
            continue
        extra, leg = detour(pts, (c["lat"], c["lon"]))
        if extra <= radius:
            near.append((extra, leg, c))
    near.sort(key=lambda t: t[0])
    near = near[:600]
    fx = facts([c for _, _, c in near])
    rows = []
    for extra, leg, c in near:
        f = fx[c["id"]]
        ds = _days_since(f["last_contact"])
        # "best fit": closer is better; good accounts and ones you haven't seen in a while move up
        value = min(f["sales12"] / 1500.0, 4.0) + (1.5 if ds is None else min(ds / 30.0, 3.0))
        if f["temp"] == "Cold" and not f["sales_all"]:
            value += 0.5
        rows.append({"c": c, "extra_mi": round(extra, 1), "extra_min": max(1, round(extra / MPH * 60)),
                     "near_stop": leg if mode == "stops" else None, "leg": leg, "f": f, "why": why(f), "score": extra - value * 0.8, "days": ds,
                     "order_days": _days_since(f["last_order"])})
    big = 10 ** 6
    keys = {"best": lambda r: r["score"], "closest": lambda r: r["extra_mi"],
            "sales": lambda r: (-r["f"]["sales12"], -r["f"]["sales_all"], r["extra_mi"]),
            "last_contact": lambda r: (-(big if r["days"] is None else r["days"]), r["extra_mi"]),
            "last_order": lambda r: (-(big if r["order_days"] is None else r["order_days"]), r["extra_mi"]),
            "name": lambda r: r["c"]["name"].lower()}
    rows.sort(key=keys.get(sort, keys["best"]))
    if skip:                      # older "Refresh" links: show the ones not seen yet first
        rows = [r for r in rows if r["c"]["id"] not in set(skip)] + [r for r in rows if r["c"]["id"] in set(skip)]
    pages = max(1, -(-len(rows) // n))
    page = min(max(1, int(page or 1)), pages)
    show = rows[(page - 1) * n: page * n]
    return dict(base, rows=show, page=page, pages=pages, total=len(rows), no_points=False,
                more=max(0, len(rows) - page * n), first=(page - 1) * n + 1 if show else 0,
                last=(page - 1) * n + len(show))


def cities_on_route(day):
    """Cities and zips of the day's stops - used to find map points for nearby properties."""
    rows = q("""SELECT c.city, c.zip FROM route_stops s JOIN customers c ON c.id=s.customer_id WHERE s.day=?""",
             (day,))
    return {(r["city"] or "").strip().lower() for r in rows if r["city"]}, {(r["zip"] or "")[:5] for r in rows if r["zip"]}
