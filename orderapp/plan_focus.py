"""Plan my week around where you're going.

Each day can have an AREA (a city, zip, part of town like "South Central Phoenix", or a property name) and
ACCOUNTS you must visit (route stops). Those are the day's anchors. Recommendations are then put on the day
whose anchors they're closest to, and each day lists warm accounts near its anchors - so a South Central
Phoenix visit doesn't get paired with Scottsdale suggestions.

Distances are straight-line from map points already saved on your customers (Routes > Locate all), so
browsing never waits on a map lookup. An area that isn't a city / zip / known part of town is looked up once
with the map service and cached.
"""
import re

from . import geo
from .db import get_db, now, q, x

FOCUS_SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_focus (
    day TEXT PRIMARY KEY, area TEXT, lat REAL, lon REAL, area_label TEXT, updated TEXT
);
"""
NEAR_MI = 6.0          # a recommendation this close to a day's area / accounts goes on that day
SUGGEST_MI = 4.0       # "warm accounts near" list radius
SUGGEST_N = 6

# parts of town people say instead of a city (approximate centers)
AREAS = {
    "south central phoenix": (33.392, -112.073), "south phoenix": (33.392, -112.073),
    "south mountain": (33.370, -112.060), "laveen": (33.363, -112.169), "ahwatukee": (33.341, -111.984),
    "downtown phoenix": (33.448, -112.074), "downtown": (33.448, -112.074), "midtown phoenix": (33.480, -112.074),
    "midtown": (33.480, -112.074), "uptown phoenix": (33.512, -112.074), "uptown": (33.512, -112.074),
    "central phoenix": (33.480, -112.074), "arcadia": (33.495, -111.975), "biltmore": (33.513, -112.027),
    "camelback corridor": (33.509, -112.030), "maryvale": (33.495, -112.180), "west phoenix": (33.480, -112.170),
    "north phoenix": (33.640, -112.074), "deer valley": (33.685, -112.110), "desert ridge": (33.678, -111.968),
    "paradise valley": (33.530, -111.950), "north central phoenix": (33.560, -112.074),
    "east phoenix": (33.450, -111.990), "sky harbor": (33.435, -112.008), "moon valley": (33.600, -112.090),
    "old town scottsdale": (33.494, -111.926), "old town": (33.494, -111.926),
    "south scottsdale": (33.470, -111.926), "north scottsdale": (33.640, -111.900),
    "mccormick ranch": (33.560, -111.905), "gainey ranch": (33.580, -111.910), "kierland": (33.627, -111.927),
    "tempe": (33.425, -111.940), "asu": (33.424, -111.928), "mesa riverview": (33.437, -111.866),
    "west valley": (33.500, -112.300), "east valley": (33.390, -111.800),
}


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _centroid(rows):
    pts = [(r["lat"], r["lon"]) for r in rows if r["lat"] is not None]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def resolve_area(text, lookup=True):
    """(lat, lon, label, how) for what the user typed, or None. Order: zip, city, part of town, account name,
    then a map lookup (cached by the geocoder)."""
    t = _norm(text)
    if not t:
        return None
    z = re.fullmatch(r"(\d{5})", t)
    if z:
        rows = q("SELECT lat, lon FROM customers WHERE substr(coalesce(zip,''),1,5)=? AND lat IS NOT NULL", (z[1],))
        c = _centroid(rows)
        if c:
            return (*c, f"zip {z[1]}", "zip")
    city = t.replace(", az", "").replace(" az", "").strip()
    rows = q("SELECT lat, lon FROM customers WHERE lower(trim(coalesce(city,'')))=? AND lat IS NOT NULL", (city,))
    if rows:
        c = _centroid(rows)
        return (*c, city.title(), "city")
    if t in AREAS:
        return (*AREAS[t], t.title(), "area")
    for k, v in AREAS.items():
        if t in k or k in t:
            return (*v, k.title(), "area")
    r = q("SELECT name, lat, lon FROM customers WHERE lower(name)=? AND lat IS NOT NULL", (t,), one=True) or \
        q("SELECT name, lat, lon FROM customers WHERE lower(name) LIKE ? AND lat IS NOT NULL ORDER BY length(name)",
          (f"%{t}%",), one=True)
    if r:
        return (r["lat"], r["lon"], r["name"], "account")
    if lookup:
        try:
            from .routes.routes_plan import _key
            pt = geo.geocode(f"{text}, AZ" if "az" not in t and "nv" not in t else text, _key(), get_db())
        except Exception:   # noqa: BLE001 - offline / no key: the area just isn't placed on the map
            pt = None
        if pt:
            return (pt[0], pt[1], str(text).strip(), "map")
    return None


def set_area(day, text):
    text = (text or "").strip()
    if not text:
        x("DELETE FROM plan_focus WHERE day=?", (day,))
        return None
    r = resolve_area(text)
    x("""INSERT INTO plan_focus(day, area, lat, lon, area_label, updated) VALUES (?,?,?,?,?,?)
         ON CONFLICT(day) DO UPDATE SET area=excluded.area, lat=excluded.lat, lon=excluded.lon,
         area_label=excluded.area_label, updated=excluded.updated""",
      (day, text, r[0] if r else None, r[1] if r else None, r[2] if r else "", now()))
    return r


def focus_for(days):
    return {r["day"]: dict(r) for r in q(f"SELECT * FROM plan_focus WHERE day IN ({','.join('?' * len(days))})",
                                         list(days))} if days else {}


def anchors(days):
    """{day: [(lat, lon, label)]} - the day's area plus every stop with a map point."""
    out = {d: [] for d in days}
    for d, f in focus_for(days).items():
        if f["lat"] is not None:
            out[d].append((f["lat"], f["lon"], f["area_label"] or f["area"]))
    if days:
        for s in q(f"""SELECT s.day, s.lat slat, s.lon slon, c.lat, c.lon, coalesce(c.name, s.label) name
                       FROM route_stops s LEFT JOIN customers c ON c.id=s.customer_id
                       WHERE s.day IN ({','.join('?' * len(days))}) AND s.status<>'Skipped'""", list(days)):
            lat, lon = (s["lat"], s["lon"]) if s["lat"] is not None else (s["slat"], s["slon"])
            if lat is not None:
                out[s["day"]].append((lat, lon, s["name"]))
    return out


def mi(a, b):
    return geo.haversine_m(a, b) / 1609.34


def nearest(pt, pts):
    best = None
    for p in pts:
        d = mi(pt, p[:2])
        if best is None or d < best[0]:
            best = (d, p[2])
    return best


def near_accounts(center, radius=SUGGEST_MI, exclude=(), limit=None, sort="best"):
    """Mapped accounts within `radius` miles of center, with sales / last order / last contact facts."""
    from .along import _days_since, facts, why
    rows = q("""SELECT id, name, address, city, zip, lat, lon, status, acct_type FROM customers WHERE lat IS NOT NULL
                AND coalesce(status,'') NOT IN ('Lost','Inactive','Do not contact')
                AND coalesce(nullif(acct_type,''),'Property')='Property'""")
    near = [(mi(center, (c["lat"], c["lon"])), c) for c in rows if c["id"] not in set(exclude)]
    near = [(d, c) for d, c in near if d <= radius]
    near.sort(key=lambda t: t[0])
    near = near[:400]
    fx = facts([c for _, c in near])
    out = []
    for d, c in near:
        f = fx[c["id"]]
        ds = _days_since(f["last_contact"])
        warm = min(f["sales12"] / 1500.0, 4.0) + (1.5 if ds is None else min(ds / 30.0, 3.0))
        out.append({"c": c, "mi": round(d, 1), "f": f, "why": why(f), "score": d - warm * 0.8,
                    "warm": f["sales12"] > 0 or f["temp"] == "Warm"})
    keys = {"best": lambda r: r["score"], "closest": lambda r: r["mi"],
            "sales": lambda r: (-r["f"]["sales12"], r["mi"])}
    out.sort(key=keys.get(sort, keys["best"]))
    return out[:limit] if limit else out


def place_recs(recs, days, per_day=6):
    """Re-assign recommendation days by distance to each day's anchors. Returns {day: [suggested near rows]}.
    Recs near no anchored day keep the city-grouping day they already have (unless that day is anchored
    elsewhere, then they are flagged as not near any planned area)."""
    anc = anchors(days)
    anchored = [d for d in days if anc[d]]
    on_route = {r["customer_id"] for r in q(f"""SELECT customer_id FROM route_stops WHERE day IN
                ({','.join('?' * len(days))}) AND customer_id IS NOT NULL""", list(days))} if days else set()
    recs[:] = [r for r in recs if r.get("customer_id") not in on_route]     # already on a route this week
    pts = {}
    ids = [r["customer_id"] for r in recs if r.get("customer_id")]
    if ids:
        for c in q(f"SELECT id, lat, lon, city, zip FROM customers WHERE id IN ({','.join('?' * len(ids))})", ids):
            pts[c["id"]] = c
    load = {d: len([a for a in anc[d]]) for d in days}
    for r in recs:
        c = pts.get(r.get("customer_id"))
        r["near"] = None
        if anchored and c and c["lat"] is not None:
            best = None
            for d in anchored:
                n = nearest((c["lat"], c["lon"]), anc[d])
                if n and (best is None or n[0] < best[0]):
                    best = (n[0], n[1], d)
            if best and best[0] <= NEAR_MI and r.get("kind") == "visit":
                r["day"] = best[2]
                r["near"] = f"{best[0]:.1f} mi from {best[1]}"
                load[best[2]] += 1
                continue
        if anchored and r.get("kind") == "visit" and r.get("day") in anchored:
            # city grouping put it on a day you're spending somewhere else - move it to an open day if any
            open_days = [d for d in days if d not in anchored]
            if open_days:
                r["day"] = min(open_days, key=lambda d: load.get(d, 0))
                load[r["day"]] = load.get(r["day"], 0) + 1
            else:
                r["far"] = True
    # warm accounts near each anchored day (not already a stop or a recommendation)
    taken = set(ids) | {s["customer_id"] for s in q(f"""SELECT customer_id FROM route_stops WHERE day IN
                        ({','.join('?' * len(days))}) AND customer_id IS NOT NULL""", list(days))} if days else set()
    suggestions = {}
    for d in anchored:
        rows = []
        for a in anc[d]:
            for row in near_accounts(a[:2], SUGGEST_MI, exclude=taken, limit=SUGGEST_N):
                row["from"] = a[2]
                rows.append(row)
        rows.sort(key=lambda r: r["score"])
        seen, pick = set(), []
        for row in rows:
            if row["c"]["id"] in seen:
                continue
            seen.add(row["c"]["id"])
            pick.append(row)
            taken.add(row["c"]["id"])
            if len(pick) >= SUGGEST_N:
                break
        suggestions[d] = pick
    return suggestions
