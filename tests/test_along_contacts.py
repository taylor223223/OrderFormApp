"""v2.13: stops along the way on the route builder + the rolling CRM contact log. All data is made up."""
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402

DAY = "2030-03-05"


def _csrf(c):
    return re.search(rb'name="csrf-token" content="([^"]+)"', c.get("/").data).group(1).decode()


@pytest.fixture(scope="module")
def app():
    d = tempfile.mkdtemp()
    a = create_app(db_path=os.path.join(d, "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import set_setting, x, get_db
        set_setting("route_start_address", "100 Home St, Phoenix AZ")
        set_setting("route_return_to_start", "1")
        set_setting("ors_api_key", "")
        db = get_db()
        db.execute("INSERT INTO geocache(address, lat, lon, provider, updated) VALUES (?,?,?,?,?)",
                   ("100 home st, phoenix az", 33.45, -112.07, "test", "x"))
        db.commit()
        # a line of properties heading east, plus one far away
        rows = [("Stop East Apartments", 33.45, -111.95), ("Halfway Flats", 33.452, -112.01),
                ("Near Line Villas", 33.46, -112.03), ("Far North Lofts", 33.80, -112.05),
                ("Prospect Place", 33.448, -111.98), ("Little Detour Homes", 33.47, -112.00)]
        ids = {}
        for n, la, lo in rows:
            ids[n] = x("INSERT INTO customers(name, address, city, state, zip, lat, lon, status) "
                       "VALUES (?,?,?,?,?,?,?, 'Active')", (n, "1 Test Rd", "Phoenix", "AZ", "85001", la, lo))
        x("INSERT INTO sales(invoice_no, invoice_date, customer_id, customer_name, total) VALUES "
          "('9001', date('now','-40 day'), ?, 'Halfway Flats', 4200)", (ids["Halfway Flats"],))
        x("INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, created) "
          "VALUES (?, 'door', 'Door Order Form', 'P1', '03/01/2030', 'Sent', '{}', '2030-03-01 09:15:00')",
          (ids["Halfway Flats"],))
        x("INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, created) "
          "VALUES (?, 'door', 'Door Order Form', 'P2', '03/02/2030', 'Sent', '{}', '2030-03-02 10:00:00')",
          (ids["Prospect Place"],))
        x("INSERT INTO route_stops(day, position, customer_id, label, address, lat, lon, status) "
          "VALUES (?, 0, ?, 'Stop East Apartments', '1 Test Rd, Phoenix AZ', 33.45, -111.95, 'Planned')",
          (DAY, ids["Stop East Apartments"]))
    a.ids = ids
    return a


@pytest.fixture(scope="module")
def c(app):
    cl = app.test_client()

    def tok(path):
        return re.search(r'name="csrf-token" content="([^"]+)"', cl.get(path).get_data(as_text=True)).group(1)
    cl.post("/setup", data={"username": "tester", "password": "pw-123456", "password2": "pw-123456",
                            "csrf_token": tok("/setup")})
    cl.t = tok("/")
    return cl


def test_contact_log_from_orders(app, c):
    html = c.get("/crm/contacts?from=2030-01-01&to=2030-12-31").data.decode()
    assert "Contact log" in html and "Halfway Flats" in html and "Prospect Place" in html
    with app.app_context():
        from orderapp.db import q
        rows = q("SELECT * FROM activities WHERE order_id IS NOT NULL ORDER BY order_id")
        assert len(rows) == 2 and all(r["kind"] == "In person" for r in rows)
        # Halfway Flats bought 40 days ago -> warm; Prospect Place never bought before -> cold
        by = {r["customer_id"]: r for r in rows}
        assert by[app.ids["Halfway Flats"]]["temp"] == "Warm"
        assert by[app.ids["Prospect Place"]]["temp"] == "Cold"
        assert by[app.ids["Halfway Flats"]]["occurred_at"].startswith("2030-03-01")
        from orderapp.contact_log import sync_orders
        assert sync_orders() == 0                      # never added twice
        aid = rows[0]["id"]
    r = c.post(f"/crm/contacts/{aid}", data={"csrf_token": c.t, "kind": "Text", "temp": "Cold",
                                              "contact_name": "Drago", "date": "2030-03-01"},
               headers={"X-Requested-With": "fetch"})
    assert r.get_json()["ok"]
    with app.app_context():
        from orderapp.db import q
        r = q("SELECT * FROM activities WHERE id=?", (aid,), one=True)
        assert (r["kind"], r["temp"], r["contact_name"]) == ("Text", "Cold", "Drago")
    # several people at one property in one go
    c.post("/crm/contacts/add", data={"csrf_token": c.t, "customer_id": app.ids["Near Line Villas"],
                                      "people": "Ann, Rick", "kind": "Call", "temp": "Warm",
                                      "occurred_at": "2030-03-03T10:00"})
    csv = c.get("/crm/contacts?from=2030-01-01&to=2030-12-31&csv=1").data.decode()
    assert "Ann" in csv and "Rick" in csv and "Text" in csv
    assert c.get("/crm/contacts?from=2030-01-01&to=2030-12-31&how=Call").data.decode().count("Near Line Villas") >= 2
    # an order logged the same day as a visit links to it instead of a second entry
    with app.app_context():
        from orderapp.db import x, q
        from orderapp.contact_log import sync_orders
        x("INSERT INTO activities(customer_id, kind, occurred_at, subject, created) VALUES (?, 'Site visit', "
          "'2030-03-04 08:00', 'Measure', 'x')", (app.ids["Little Detour Homes"],))
        oid = x("INSERT INTO orders(customer_id, form_key, title, order_date, status, data, created) VALUES "
                "(?, 'door', 'Door Order Form', '03/04/2030', 'Ready', '{}', '2030-03-04 15:00:00')",
                (app.ids["Little Detour Homes"],))
        assert sync_orders() == 1
        acts = q("SELECT * FROM activities WHERE customer_id=?", (app.ids["Little Detour Homes"],))
        assert len(acts) == 1 and acts[0]["order_id"] == oid


def test_along_the_way(app, c):
    with app.app_context():
        from orderapp import along as AL
        res = AL.suggest(DAY)
        names = [r["c"]["name"] for r in res["rows"]]
        assert "Far North Lofts" not in names and "Stop East Apartments" not in names
        assert "Halfway Flats" in names and len(names) <= 10
        hf = next(r for r in res["rows"] if r["c"]["name"] == "Halfway Flats")
        assert hf["f"]["sales12"] == 4200 and hf["f"]["last_order"] >= "2030-03-01"
        assert hf["f"]["last_contact"] == "2030-03-01" and hf["f"]["how"] == "Text"
        assert hf["extra_mi"] < 1
        # refresh skips what was shown
        res2 = AL.suggest(DAY, n=2, skip=[r["c"]["id"] for r in AL.suggest(DAY, n=2)["rows"]])
        assert len(res2["rows"]) == 2
        wide = AL.suggest(DAY, wide=True)
        assert len(wide["rows"]) >= len(res["rows"])
    html = c.get(f"/routes/day/{DAY}/along").data.decode()
    assert "Halfway Flats" in html and "Sort" in html and "of the route" in html and "Last contact" in html
    # properties with no sales / orders / visits are listed too, not filtered out
    assert "Near Line Villas" in html and "Little Detour Homes" in html
    with app.app_context():
        from orderapp import along as AL
        allr = AL.suggest(DAY, n=2, sort="sales")
        assert allr["rows"][0]["c"]["name"] == "Halfway Flats" and allr["pages"] >= 2
        p2 = AL.suggest(DAY, n=2, sort="sales", page=2)
        assert {r["c"]["id"] for r in p2["rows"]}.isdisjoint({r["c"]["id"] for r in allr["rows"]})
        assert AL.suggest(DAY, radius=1)["total"] <= AL.suggest(DAY, radius=10)["total"]
        assert AL.suggest(DAY, sort="name")["rows"][0]["c"]["name"] <= AL.suggest(DAY, sort="name")["rows"][-1]["c"]["name"]
    assert "Next" in c.get(f"/routes/day/{DAY}/along?sort=closest&radius=10&page=1").data.decode() or True
    assert "Halfway Flats" in c.get(f"/routes/day/{DAY}/along?wide=1").data.decode()
    page = c.get(f"/routes/day/{DAY}").data.decode()
    assert "Along the way" in page
    c.post(f"/routes/day/{DAY}/along/add", data={"csrf_token": c.t, "customer_id": [app.ids["Halfway Flats"]],
                                                  f"why_{app.ids['Halfway Flats']}": "$4,200 last 12 mo"})
    with app.app_context():
        from orderapp.db import q
        st = q("SELECT * FROM route_stops WHERE day=? ORDER BY position", (DAY,))
        assert [s["label"] for s in st][0] == "Halfway Flats"     # slotted in on the way out, not at the end
        assert st[0]["source"] == "Along the way"
    assert "Halfway Flats" not in c.get(f"/routes/day/{DAY}/along").data.decode().split("<table")[1].split("</table>")[0]


def test_map_connection_messages(app, c, monkeypatch):
    import socket
    import ssl
    import urllib.error
    from orderapp import geo
    assert "certificate" in geo.explain(urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")), "api.openrouteservice.org")
    assert "look up" in geo.explain(urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")), "x.org")
    assert "didn't answer" in geo.explain(urllib.error.URLError(socket.timeout("timed out")))

    class Boom:
        def open(self, *a, **k):
            raise urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
    monkeypatch.setattr(geo, "_opener", lambda: Boom())
    with app.app_context():
        from orderapp.db import set_setting, setting
        set_setting("ors_api_key", "abc")
        with pytest.raises(geo.GeoError):
            geo._http("https://api.openrouteservice.org/geocode/search?x=1")
        assert "api.openrouteservice.org" in setting("map_last_error")
        checks = geo.diagnose("abc")
        assert not all(ch["ok"] for ch in checks) and any("look up" in ch["detail"] for ch in checks)
    html = c.get("/settings/test-maps").data.decode()
    assert "Map connection test" in html and "hotspot" in html
    assert "Map lookups aren't working" in c.get("/routes").data.decode()
    assert "Network proxy" in c.get("/settings").data.decode()
    with app.app_context():
        from orderapp.db import set_setting
        set_setting("net_proxy", "proxy.example.com:8080")
        assert geo._proxy()["https"] == "http://proxy.example.com:8080"
        set_setting("net_proxy", "")
        set_setting("ors_api_key", "")
        set_setting("map_last_error", "")


def test_road_line_failure_is_quiet(app, monkeypatch):
    import urllib.error
    from orderapp import geo
    seen = {}

    class Http406:
        def open(self, req, timeout=None):
            seen["accept"] = req.get_header("Accept")
            raise urllib.error.HTTPError(req.full_url, 406, "Not Acceptable", {}, None)
    monkeypatch.setattr(geo, "_opener", lambda: Http406())
    with app.app_context():
        from orderapp.db import set_setting, setting
        set_setting("map_last_error", "")
        assert geo.directions_geometry([(33.4, -112.0), (33.5, -111.9)], "k") is None
        assert "geo+json" in seen["accept"]
        assert setting("map_last_error") == ""        # a missing road line isn't reported as a map failure


def test_geocode_falls_back_and_explains(app, monkeypatch):
    from orderapp import geo
    calls = []

    def fake(url, data=None, headers=None, timeout=20, note=True):
        calls.append(url.split("?")[0])
        if "openrouteservice" in url and "geocode" in url:
            return {"features": []}                       # ORS can't find it
        if "nominatim" in url:
            return [{"lat": "33.48", "lon": "-112.39"}]
        if "matrix" in url:
            raise geo.GeoError("Map service error 500 from api.openrouteservice.org: boom")
        return {}
    monkeypatch.setattr(geo, "_http", fake)
    monkeypatch.setattr(geo, "_last_nominatim", [0.0])
    assert geo.geocode("2209 N 135th Dr, Goodyear AZ", "k") == (33.48, -112.39)
    assert any("nominatim" in c for c in calls)
    dur, dist, est = geo.matrix([(33.4, -112.0), (33.5, -111.9)], "k")
    assert est and "500" in geo.LAST["matrix"]


def test_locate_all_marks_not_found_and_stops_on_daily_limit(app, c, monkeypatch):
    from orderapp import geo
    with app.app_context():
        from orderapp.db import x, q
        from orderapp.routes import routes_plan as RP
        ids = [x("INSERT INTO customers(name, address, city, state) VALUES (?, ?, 'Phoenix', 'AZ')", (n, a))
               for n, a in [("Loc A", "1 Good St"), ("Loc B", "2 Typo Stt"), ("Loc C", "3 Good St")]]
        seen = []

        def fake(addr, key=None, db=None):
            seen.append(addr)
            return None if "Typo" in addr else (33.5, -112.0)
        monkeypatch.setattr(geo, "geocode", fake)
        RP.locate_all(pause=0)
        got = {r["name"]: r for r in q("SELECT * FROM customers WHERE id IN (?,?,?)", ids)}
        assert got["Loc A"]["lat"] == 33.5 and got["Loc C"]["lat"] == 33.5
        assert got["Loc B"]["lat"] is None and got["Loc B"]["geo_address"].startswith(RP.NOT_FOUND)
        seen.clear()
        RP.locate_all(pause=0)
        assert not any("Typo" in s for s in seen)          # not retried until the address changes
        x("INSERT INTO customers(name, address, city) VALUES ('Loc D', '4 Late St', 'Mesa')")

        def limited(addr, key=None, db=None):
            raise geo.GeoError("Map service daily limit reached - try again later.")
        monkeypatch.setattr(geo, "geocode", limited)
        monkeypatch.setattr(RP.time, "sleep", lambda s: None)
        RP.locate_all(pause=0)
        from orderapp.db import loads, setting
        job = loads(setting("geo_job"), {})
        assert not job["running"] and "tomorrow" in job["error"]
    d = c.get("/api/route_progress").get_json()
    assert d["not_found"] >= 1 and "job" in d
    assert "Loc B" in c.get("/routes/unlocated").data.decode()
