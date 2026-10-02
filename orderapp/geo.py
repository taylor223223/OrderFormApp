"""Geocoding, drive times and route optimization (free OpenStreetMap services).

With an OpenRouteService key (free): real road drive times + route lines.
Without a key: addresses are found through OpenStreetMap Nominatim and drive times are
*estimated* from straight-line distance, so the app still works on day one.
"""
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .db import now

UA = "OrderFormApp/1.2 (route planner; desktop)"
ORS = "https://api.openrouteservice.org"
_nominatim_lock = threading.Lock()
_last_nominatim = [0.0]


class GeoError(Exception):
    pass


def _http(url, data=None, headers=None, timeout=20):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="ignore")[:200]
        if e.code in (401, 403):
            raise GeoError("OpenRouteService key was rejected - check it in Settings.") from e
        if e.code == 429:
            raise GeoError("Map service daily limit reached - try again later.") from e
        raise GeoError(f"Map service error {e.code}: {msg}") from e
    except OSError as e:
        raise GeoError(f"Can't reach the map service (no internet?): {e}") from e


# ---------------------------------------------------------------- geocoding
def full_address(c):
    parts = [c["address"] or "", " ".join(x for x in [c["city"] or "", c["state"] or "AZ", c["zip"] or ""] if x)]
    return ", ".join(p for p in parts if p.strip())


def geocode(address, key=None, db=None):
    """Return (lat, lon) or None. Uses the local cache first."""
    address = (address or "").strip()
    if not address:
        return None
    if db is not None:
        r = db.execute("SELECT lat, lon FROM geocache WHERE address=?", (address.lower(),)).fetchone()
        if r:
            return (r[0], r[1])
    lat = lon = None
    provider = ""
    if key:
        q = urllib.parse.urlencode({"api_key": key, "text": address, "boundary.country": "US", "size": 1})
        d = _http(f"{ORS}/geocode/search?{q}")
        feats = d.get("features") or []
        if feats:
            lon, lat = feats[0]["geometry"]["coordinates"][:2]
            provider = "ors"
    else:
        with _nominatim_lock:  # Nominatim policy: max 1 request per second
            wait = 1.1 - (time.time() - _last_nominatim[0])
            if wait > 0:
                time.sleep(wait)
            q = urllib.parse.urlencode({"q": address, "format": "json", "limit": 1, "countrycodes": "us"})
            d = _http(f"https://nominatim.openstreetmap.org/search?{q}")
            _last_nominatim[0] = time.time()
        if d:
            lat, lon = float(d[0]["lat"]), float(d[0]["lon"])
            provider = "nominatim"
    if lat is None:
        return None
    if db is not None:
        db.execute("INSERT OR REPLACE INTO geocache(address, lat, lon, provider, updated) VALUES (?,?,?,?,?)",
                   (address.lower(), lat, lon, provider, now()))
        db.commit()
    return (lat, lon)


# ---------------------------------------------------------------- drive times
def haversine_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def estimate_matrix(points):
    """Road distance ~1.35x straight line; ~29 mph average around town."""
    n = len(points)
    dist = [[0.0] * n for _ in range(n)]
    dur = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                m = haversine_m(points[i], points[j]) * 1.35
                dist[i][j] = m
                dur[i][j] = m / 13.0 + (90 if m > 0 else 0)   # + time to park / get going
    return dur, dist


def matrix(points, key=None):
    """points: [(lat, lon)] -> (durations_s, distances_m, estimated: bool)"""
    if key and len(points) > 1:
        try:
            d = _http(f"{ORS}/v2/matrix/driving-car", data={
                "locations": [[p[1], p[0]] for p in points], "metrics": ["duration", "distance"]},
                headers={"Authorization": key})
            dur = [[x or 0 for x in row] for row in d["durations"]]
            dist = [[x or 0 for x in row] for row in d["distances"]]
            return dur, dist, False
        except GeoError:
            pass
    dur, dist = estimate_matrix(points)
    return dur, dist, True


def directions_geometry(points, key=None):
    """Road-following line [(lat, lon)] for the map, or None (map draws straight lines)."""
    if not key or len(points) < 2:
        return None
    try:
        d = _http(f"{ORS}/v2/directions/driving-car/geojson",
                  data={"coordinates": [[p[1], p[0]] for p in points]}, headers={"Authorization": key})
        coords = d["features"][0]["geometry"]["coordinates"]
        return [(c[1], c[0]) for c in coords]
    except (GeoError, KeyError, IndexError):
        return None


# ---------------------------------------------------------------- optimization
def route_cost(order, dur, round_trip):
    """order: indexes of stops (1..n) visited after start (0)."""
    cost, prev = 0.0, 0
    for i in order:
        cost += dur[prev][i]
        prev = i
    if round_trip and order:
        cost += dur[prev][0]
    return cost


def optimize_order(dur, round_trip=True, fixed_first=None):
    """Best visiting order for stops 1..n-1 starting at 0 (nearest neighbour + 2-opt).
    fixed_first: stop index that must be visited first (e.g. a set appointment)."""
    n = len(dur)
    stops = list(range(1, n))
    if not stops:
        return []
    order, cur, left = [], 0, set(stops)
    if fixed_first in left:
        order.append(fixed_first)
        left.discard(fixed_first)
        cur = fixed_first
    while left:
        nxt = min(left, key=lambda j: dur[cur][j])
        order.append(nxt)
        left.discard(nxt)
        cur = nxt
    lo = 1 if fixed_first is not None else 0
    improved = True
    while improved:
        improved = False
        best = route_cost(order, dur, round_trip)
        for i in range(lo, len(order) - 1):
            for k in range(i + 1, len(order)):
                cand = order[:i] + order[i:k + 1][::-1] + order[k + 1:]
                c = route_cost(cand, dur, round_trip)
                if c + 1e-6 < best:
                    order, best, improved = cand, c, True
    return order


def best_insertion(dur, current_order, new_idx, round_trip=True):
    """Where to put one new stop with the least extra driving (for quick-adds during the day)."""
    best_pos, best_cost = len(current_order), None
    for pos in range(len(current_order) + 1):
        cand = current_order[:pos] + [new_idx] + current_order[pos:]
        c = route_cost(cand, dur, round_trip)
        if best_cost is None or c < best_cost:
            best_pos, best_cost = pos, c
    return best_pos


def google_maps_link(start_address, addresses, round_trip=True):
    """Turn-by-turn in Google Maps (works on phone and PC). Google allows up to 9 waypoints."""
    if not addresses:
        return None
    dest = start_address if round_trip else addresses[-1]
    way = addresses if round_trip else addresses[:-1]
    q = {"api": "1", "origin": start_address, "destination": dest, "travelmode": "driving"}
    if way:
        q["waypoints"] = "|".join(way[:9])
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(q)
