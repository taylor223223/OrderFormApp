"""CRM + route planner. Map services are mocked (no network in tests)."""
import os
import re
import tempfile
from datetime import date, timedelta

import pytest

_tmp = tempfile.mkdtemp()
os.environ["ORDERAPP_DATA"] = _tmp

from orderapp import create_app, geo  # noqa: E402

# fake coordinates around Tempe/Phoenix
COORDS = {"5325 s. kyrene rd, suite 103, tempe, az 85283": (33.3555, -111.9298)}
PLACES = [(33.4152, -111.8315), (33.4942, -112.0740), (33.3062, -111.8413), (33.6386, -112.0130),
          (33.4255, -111.9400), (33.3528, -111.7890)]


@pytest.fixture(scope="module")
def client():
    app = create_app(db_path=os.path.join(_tmp, "crm.db"), testing=True)
    c = app.test_client()
    mp = pytest.MonkeyPatch()
    calls = []

    def fake_http(url, data=None, headers=None, timeout=20):
        calls.append(url)
        raise geo.GeoError("network disabled in tests")

    def fake_geocode(address, key=None, db=None):
        a = (address or "").strip().lower()
        if a in COORDS:
            return COORDS[a]
        m = re.search(r"(\d+) test st", a)
        return PLACES[int(m.group(1)) % len(PLACES)] if m else None
    mp.setattr(geo, "_http", fake_http)
    mp.setattr(geo, "geocode", fake_geocode)
    c.app = app
    yield c
    mp.undo()


def csrf(c, path="/"):
    return re.search(r'name="csrf-token" content="([^"]+)"', c.get(path).get_data(as_text=True)).group(1)


def post(c, path, data=None, page="/", **kw):
    data = dict(data or {})
    data["csrf_token"] = csrf(c, page)
    return c.post(path, data=data, **kw)


def test_optimizer():
    pts = [(33.35, -111.93)] + PLACES
    dur, dist = geo.estimate_matrix(pts)
    naive = geo.route_cost(list(range(1, len(pts))), dur, True)
    order = geo.optimize_order(dur, True)
    assert sorted(order) == list(range(1, len(pts)))
    assert geo.route_cost(order, dur, True) <= naive
    assert geo.best_insertion(dur, order[:-1], order[-1]) in range(len(order))
    link = geo.google_maps_link("Start", ["A", "B"], True)
    assert link.startswith("https://www.google.com/maps/dir/?") and "waypoints=A%7CB" in link


def test_crm_and_routes(client):
    c = client
    post(c, "/setup", {"username": "taylor", "password": "secret123", "password2": "secret123"}, page="/setup")
    cids = []
    for i in range(5):
        r = post(c, "/customers/new", {"name": f"Test Apts {i}", "address": f"{i} Test St", "city": "Mesa"})
        cids.append(int(r.headers["Location"].rsplit("/", 1)[1]))
    for page in ["/crm", "/crm/activities", "/crm/tasks", "/crm/pipeline", "/crm/report", "/routes",
                 f"/customers/{cids[0]}?tab=crm", "/customers?status=Active", "/settings"]:
        assert c.get(page).status_code == 200, page

    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    # log a call with new contact, status, follow-up and route visit
    r = post(c, "/crm/log", {"customer_id": cids[0], "kind": "Call", "contact_name": "Maria Lopez",
                             "save_contact": "1", "subject": "Verticals quote", "topics": ["Vertical blinds"],
                             "notes": "Needs 40 units", "outcome": "Quote requested", "status": "Prospect",
                             "follow_up": tomorrow, "follow_up_title": "Send quote", "route_day": today,
                             "next": "//evil.example"})
    assert r.headers["Location"].endswith("/crm")
    html = c.get(f"/customers/{cids[0]}?tab=crm").get_data(as_text=True)
    assert "Verticals quote" in html and "Send quote" in html and "Prospect" in html
    assert "Maria Lopez" in c.get(f"/customers/{cids[0]}").get_data(as_text=True)

    # route: add the rest, optimize, view
    for cid in cids[1:]:
        post(c, "/routes/add", {"customer_id": cid, "day": today, "purpose": "Drop samples", "source": "Text"})
    post(c, "/routes/add", {"address": "999 Nowhere Blvd", "day": today})        # can't be located
    r = post(c, f"/routes/day/{today}/optimize")
    assert r.status_code == 302
    html = c.get(f"/routes/day/{today}").get_data(as_text=True)
    assert "Open in Google Maps" in html and "estimated" in html
    assert "find this address on the map" in html
    assert html.count('class="stopn') >= 6
    with c.app.app_context():
        from orderapp.db import q
        rd = q("SELECT * FROM route_days WHERE day=?", (today,), one=True)
        assert rd["total_drive_s"] > 0 and rd["estimated"] == 1
        stops = q("SELECT * FROM route_stops WHERE day=? ORDER BY position", (today,))
        assert len(stops) == 6
        # Text requests logged as CRM activity
        assert q("SELECT COUNT(*) n FROM activities WHERE kind='Text'", one=True)["n"] == 4
        sid = stops[0]["id"]
    # done -> logs a site visit; move another to tomorrow
    post(c, f"/routes/stop/{sid}", {"act": "Done"})
    post(c, f"/routes/stop/{stops[1]['id']}", {"act": "move", "to_day": tomorrow})
    with c.app.app_context():
        from orderapp.db import q
        assert q("SELECT COUNT(*) n FROM activities WHERE kind='Site visit'", one=True)["n"] == 1
        assert q("SELECT COUNT(*) n FROM route_stops WHERE day=?", (tomorrow,), one=True)["n"] == 1
    assert "Next 7 days" in c.get("/routes").get_data(as_text=True)
    # estimate + notes on a stop, then the stop list
    post(c, f"/routes/stop/{stops[2]['id']}", {"act": "notes", "products": ["Doors", "Blinds"],
                                               "notes": "Unit 104 vacant, 212 occupied"})
    sheet = c.get(f"/routes/day/{today}/sheet").get_data(as_text=True)
    assert "Doors, Blinds" in sheet and "Unit 104 vacant" in sheet and "Estimating: Doors, Blinds" in sheet
    r = post(c, "/routes/add", {"customer_id": cids[1], "day": tomorrow, "products": ["Verticals"], "notes": "gate 4411"})
    assert "Verticals" in c.get("/routes?week=" + tomorrow).get_data(as_text=True)

    # tasks + deals
    post(c, "/crm/tasks", {"title": "Call back Ironwood", "due_date": today, "customer_id": cids[2]})
    post(c, "/crm/deal", {"customer_id": cids[0], "name": "Unit turn blinds", "value": "$12,500", "stage": "Quote sent"})
    html = c.get("/crm/pipeline").get_data(as_text=True)
    assert "Unit turn blinds" in html and "12,500" in html
    with c.app.app_context():
        from orderapp.db import q
        did = q("SELECT id FROM deals", one=True)["id"]
    post(c, f"/crm/deal/{did}", {"stage_only": "1", "stage": "Won"})
    assert "Quote sent -&gt; Won" in c.get("/crm/activities").get_data(as_text=True)

    dash = c.get("/").get_data(as_text=True)
    assert "Today&#39;s route" in dash or "Today's route" in dash
    assert f"/routes/day/{today}" in dash and 'href="/crm"' in dash
    assert "CRM quick note" in dash and "Doors, Blinds" in dash and "Unit 104 vacant" in dash
    post(c, "/crm/log", {"customer_id": cids[3], "kind": "Text", "notes": "Needs screens for 12 units",
                         "follow_up": today, "next": "/"})
    dash = c.get("/").get_data(as_text=True)
    assert "Follow up: Needs screens for 12 units" in dash
    # weekly report: page, pdf, csv, send as draft file
    # (with no week given, Mon/Tue default to LAST week's report - so ask for this week explicitly)
    html = c.get(f"/crm/report?week={today}").get_data(as_text=True)
    assert "Test Apts 0" in html and "Maria Lopez" in html
    pdf = c.get(f"/crm/report/file/pdf?week={today}")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    csv = c.get(f"/crm/report/file/csv?week={today}")
    assert b"Verticals quote" in csv.data
    post(c, "/settings", {"email_provider": "eml", "output_dir": _tmp, "route_start_address": "5325 S. Kyrene Rd, Suite 103, Tempe, AZ 85283"})
    r = post(c, "/crm/report/send", {"to": "boss@example.com", "mode": "review", "week": date.today().isoformat()})
    assert r.status_code in (200, 302)
    with c.app.app_context():
        from orderapp.db import setting
        assert setting("report_to") == "boss@example.com"
        assert setting("route_return_to_start") == "0"


def test_live_route_insertion(client):
    """On the road: new stops go into the part of the day not yet driven; visited stops never move."""
    c = client
    from datetime import date, timedelta
    day = (date.today() + timedelta(days=0)).isoformat()
    with c.app.app_context():
        from orderapp.db import x
        x("DELETE FROM route_stops WHERE day=?", (day,))
    cids = []
    for i in range(5):
        r = post(c, "/customers/new", {"name": f"Live Apts {i}", "address": f"{i} Test St", "city": "Mesa"})
        cids.append(int(r.headers["Location"].rsplit("/", 1)[1]))
    for cid in cids[:4]:
        post(c, "/routes/add", {"customer_id": cid, "day": day})
    post(c, f"/routes/day/{day}/optimize")
    from orderapp.db import q
    with c.app.app_context():
        order = [r["id"] for r in q("SELECT id FROM route_stops WHERE day=? ORDER BY position", (day,))]
    # visit the first two
    post(c, f"/routes/stop/{order[0]}", {"act": "Done"})
    post(c, f"/routes/stop/{order[1]}", {"act": "Done"})
    # a call comes in: add a stop (dashboard-style, back to "/")
    r = post(c, "/routes/add", {"customer_id": cids[4], "day": day, "source": "Call", "next": "/"})
    assert r.headers["Location"].endswith("/")
    with c.app.app_context():
        rows = q("SELECT * FROM route_stops WHERE day=? ORDER BY position", (day,))
    ids = [r["id"] for r in rows]
    assert ids[:2] == order[:2]                         # visited stops stay first, untouched
    new_pos = [r["customer_id"] for r in rows].index(cids[4])
    assert new_pos >= 2                                  # never routed as an earlier stop
    # re-plan rest of day keeps visited first
    post(c, f"/routes/day/{day}/optimize")
    with c.app.app_context():
        ids2 = [r["id"] for r in q("SELECT id FROM route_stops WHERE day=? ORDER BY position", (day,))]
    assert ids2[:2] == order[:2] and sorted(ids2) == sorted(ids)
    # dashboard shows visited + next + day picker
    dash = c.get("/").get_data(as_text=True)
    assert "✓ Visited" in dash and ">next<" in dash and "Tomorrow" in dash and "Re-plan rest of today" in dash
    # customer page shows it's on today's route; logging a site visit checks it off
    nxt = rows[2]
    page = c.get(f"/customers/{nxt['customer_id']}").get_data(as_text=True)
    assert "On today&#39;s route" in page or "On today's route" in page
    post(c, "/crm/log", {"customer_id": nxt["customer_id"], "kind": "Site visit", "notes": "Measured 3 units"})
    with c.app.app_context():
        st = q("SELECT * FROM route_stops WHERE id=?", (nxt["id"],), one=True)
        assert st["status"] == "Done" and st["activity_id"]
        assert q("SELECT COUNT(*) n FROM activities WHERE customer_id=? AND kind='Site visit'",
                 (nxt["customer_id"],), one=True)["n"] == 1   # not double-logged
    # adding for tomorrow from the dashboard
    tmr = (date.today() + timedelta(days=1)).isoformat()
    post(c, "/routes/add", {"customer_id": cids[0], "day": tmr, "next": "/"})
    with c.app.app_context():
        assert q("SELECT COUNT(*) n FROM route_stops WHERE day=? AND customer_id=?", (tmr, cids[0]), one=True)["n"] == 1


def test_rolling_week_and_trip_log():
    """Planner shows today + 6 days (past days drop off); finished days land in the trip log."""
    import os
    import re
    import tempfile
    from datetime import date, timedelta
    d = tempfile.mkdtemp()
    os.environ["ORDERAPP_DATA"] = d
    from orderapp import create_app
    app = create_app(db_path=os.path.join(d, "t.db"), testing=True)
    c = app.test_client()
    h = c.get("/setup").get_data(as_text=True)
    tok = re.search(r'name="csrf-token" content="([^"]+)"', h).group(1)
    c.post("/setup", data={"username": "taylor", "password": "secret123", "password2": "secret123", "csrf_token": tok})
    today = date.today()
    yday = (today - timedelta(days=1)).isoformat()
    with app.app_context():
        from orderapp.db import x
        cid = x("INSERT INTO customers(name) VALUES ('Sunrise Villas')")
        x("INSERT INTO route_stops(day, customer_id, label, status) VALUES (?,?,?,?)", (yday, cid, "Sunrise Villas", "Done"))
        x("INSERT INTO route_stops(day, label, status) VALUES (?,?,?)", (yday, "Somewhere", "Skipped"))
        x("INSERT INTO route_days(day, total_m, total_drive_s, estimated) VALUES (?,?,?,1)", (yday, 16093.4, 1800))
    html = c.get("/routes").get_data(as_text=True)
    assert today.strftime("%m/%d") in html and (today + timedelta(days=6)).strftime("%m/%d") in html
    assert f'href="/routes/day/{yday}"' not in html          # yesterday has rolled off the planner
    nxt = c.get(f"/routes?start={(today + timedelta(days=7)).isoformat()}").get_data(as_text=True)
    assert (today + timedelta(days=13)).strftime("%m/%d") in nxt
    log = c.get(f"/routes/log?month={yday[:7]}").get_data(as_text=True)
    assert "Sunrise Villas" in log and "10.0" in log and "1 of 2" in log
    csv = c.get(f"/routes/log?month={yday[:7]}&csv=1")
    assert csv.status_code == 200 and b"Sunrise Villas" in csv.data and b"10.0" in csv.data
