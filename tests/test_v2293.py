"""v2.29.3: Along the way - pick which stops to search near; one Routes tab (Today's route | Week plan);
sales rep by ZIP. All data is made up."""
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402

DAY = "2030-05-14"


@pytest.fixture(scope="module")
def app():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import set_setting, x
        set_setting("territory_only", "0")
        set_setting("route_start_address", "")
        ids = {}
        # two stops far apart (north / south); a property next to each
        for n, la, lo, z in [("North Stop Apts", 33.60, -112.07, "85020"), ("South Stop Apts", 33.30, -112.07, "85040"),
                             ("Near North Flats", 33.605, -112.07, "85020"), ("Near South Villas", 33.305, -112.07, "85040")]:
            ids[n] = x("INSERT INTO customers(name, address, city, state, zip, lat, lon, status) "
                       "VALUES (?,?,?,?,?,?,?, 'Active')", (n, "1 Test Rd", "Phoenix", "AZ", z, la, lo))
        a.sid = {}
        for pos, n in enumerate(("North Stop Apts", "South Stop Apts")):
            a.sid[n] = x("INSERT INTO route_stops(day, position, customer_id, label, address, lat, lon, status) "
                         "VALUES (?,?,?,?, '1 Test Rd', ?, ?, 'Planned')",
                         (DAY, pos, ids[n], n, 33.60 if n.startswith("North") else 33.30, -112.07))
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


def test_pick_stops_to_search_near(app, c):
    with app.app_context():
        from orderapp import along
        both = {r["c"]["name"] for r in along.suggest(DAY, mode="stops", radius=2)["rows"]}
        assert both == {"Near North Flats", "Near South Villas"}
        north = along.suggest(DAY, mode="stops", radius=2, only=[app.sid["North Stop Apts"]])
        assert {r["c"]["name"] for r in north["rows"]} == {"Near North Flats"} and north["only"]
    # on the page: the picker, then the pick is remembered for that day
    html = c.get(f"/routes/day/{DAY}/along?mode=stops&switched=1").get_data(as_text=True)
    assert "Search near:" in html and "1. North Stop Apts" in html and "2. South Stop Apts" in html
    html = c.get(f"/routes/day/{DAY}/along?mode=stops&near_set=1&near={app.sid['South Stop Apts']}").get_data(as_text=True)
    assert "Near South Villas" in html and "Near North Flats" not in html and "1 of 2 stops" in html
    html = c.get(f"/routes/day/{DAY}/along?mode=stops").get_data(as_text=True)          # remembered
    assert "Near South Villas" in html and "Near North Flats" not in html
    html = c.get(f"/routes/day/{DAY}/along?mode=stops&near_set=1").get_data(as_text=True)  # none ticked = all
    assert "Near South Villas" in html and "Near North Flats" in html


def test_one_routes_tab(c):
    from datetime import date
    today = date.today().isoformat()
    html = c.get("/").get_data(as_text=True)
    assert f'href="/routes/day/{today}"' in html and ">Plan</a>" not in html
    day = c.get(f"/routes/day/{today}").get_data(as_text=True)
    assert "Today&#39;s route" in day and "Week plan" in day and "Trip log" in day
    plan = c.get("/plan").get_data(as_text=True)
    assert "Add a stop to a day" in plan and "Who to see" in plan


def test_sales_rep_by_zip(app, c):
    with app.app_context():
        from orderapp import territory as T
        from orderapp.db import set_setting
        set_setting("sales_rep", "Taylor M. Anderson")
        T.save_rep_zips("Taylor M. Anderson", "85020")
        set_setting("territory_partial", "")
        assert T.rep_for({"zip": "85020", "lat": None})[0] == "Taylor M. Anderson"
        assert T.rep_for({"zip": "85040", "lat": None})[0] == ""                 # nobody has that ZIP yet
    html = c.get("/customers?q=Flats").get_data(as_text=True)
    assert ">Rep</th>" in html and "<b>Me</b>" in html
    page = c.get(f"/customers/{app.ids['Near North Flats']}").get_data(as_text=True)
    assert "Sales rep: <b>Me (Taylor M. Anderson)</b>" in page and "ZIP 85020" in page
    page = c.get(f"/customers/{app.ids['Near South Villas']}").get_data(as_text=True)
    assert "not assigned" in page and "no rep has ZIP 85040 yet" in page
