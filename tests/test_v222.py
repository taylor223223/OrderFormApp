"""v2.22: delete orders (undo / Recently deleted), flat baseboard choice, refresh + new-day reload,
Along the way "Near my stops" mode. All data is made up."""
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402

DAY = "2030-04-09"


@pytest.fixture(scope="module")
def app():
    d = tempfile.mkdtemp()
    a = create_app(db_path=os.path.join(d, "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import set_setting, x
        set_setting("territory_only", "0")
        set_setting("route_start_address", "")       # no start address: "near my stops" still works
        ids = {}
        for n, la, lo in [("Stop One Apartments", 33.40, -112.07), ("Next Door Flats", 33.405, -112.07),
                          ("Two Miles Villas", 33.428, -112.07), ("Way Out Lofts", 33.70, -112.07)]:
            ids[n] = x("INSERT INTO customers(name, address, city, state, zip, lat, lon, status) "
                       "VALUES (?,?,?,?,?,?,?, 'Active')", (n, "1 Test Rd", "Phoenix", "AZ", "85001", la, lo))
        x("INSERT INTO route_stops(day, position, customer_id, label, address, lat, lon, status) "
          "VALUES (?, 0, ?, 'Stop One Apartments', '1 Test Rd', 33.40, -112.07, 'Planned')",
          (DAY, ids["Stop One Apartments"]))
        a.oid = x("INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, created) "
                  "VALUES (?, 'door', 'Door Order Form', 'P-77', '04/01/2030', 'Sent', '{}', '2030-04-01 09:00:00')",
                  (ids["Next Door Flats"],))
        from orderapp.db import add_status
        add_status(a.oid, "Sent", "test")
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


def test_delete_undo_restore(app, c):
    oid = app.oid
    html = c.get("/tracking?status=all").get_data(as_text=True)
    assert f'/orders/{oid}/delete' in html and "Recently deleted keeps it for 30 days" in html
    with app.app_context():
        from orderapp.contact_log import sync_orders
        from orderapp.db import q
        sync_orders()
        assert q("SELECT COUNT(*) n FROM activities WHERE order_id=?", (oid,), one=True)["n"] == 1
    r = c.post(f"/orders/{oid}/delete", data={"csrf_token": c.t, "next": "/tracking?status=all"})
    assert r.status_code == 302 and "undo=" in r.headers["Location"]
    html = c.get(r.headers["Location"]).get_data(as_text=True)
    assert f"Deleted order #{oid}" in html and "Undo" in html and "Recently deleted (1)" in html
    with app.app_context():
        from orderapp.db import q
        assert not q("SELECT 1 FROM orders WHERE id=?", (oid,), one=True)
        assert q("SELECT COUNT(*) n FROM activities", one=True)["n"] == 0
        tid = q("SELECT id FROM order_trash", one=True)["id"]
    r = c.post(f"/orders/restore/{tid}", data={"csrf_token": c.t, "next": "/tracking"})
    assert r.status_code == 302
    with app.app_context():
        from orderapp.db import q
        o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
        assert o and o["po"] == "P-77"                      # same order #, same data
        assert q("SELECT COUNT(*) n FROM order_status_history WHERE order_id=?", (oid,), one=True)["n"] >= 1
        assert q("SELECT COUNT(*) n FROM activities WHERE order_id=?", (oid,), one=True)["n"] == 1
        assert not q("SELECT 1 FROM order_trash", one=True)


def test_purge_after_30_days(app, c):
    with app.app_context():
        from orderapp import order_trash
        from orderapp.db import q, x
        tid = order_trash.delete(app.oid)
        x("UPDATE order_trash SET deleted_at='2000-01-01 00:00:00' WHERE id=?", (tid,))
        assert order_trash.purge() == 1 and not q("SELECT 1 FROM order_trash", one=True)


def test_refresh_and_new_day(c):
    html = c.get("/").get_data(as_text=True)
    assert 'id="refreshBtn"' in html and "checkDay" in html and "It's a new day" in html


def test_flat_baseboard_choice():
    from orderapp.catalog import FLAT_TRIM_PROFILE, public_spec
    spec = public_spec("trim")
    prof = next(f for f in spec["fields"] if f["key"] == "profile")
    assert any(o.startswith("K: Flat / Square") for o in prof["options"])
    th = next(f for f in spec["fields"] if f["key"] == "flat_thick")
    assert th["show_if"] == {"key": "profile", "in": [FLAT_TRIM_PROFILE]} and '1/2"' in th["options"]


def test_near_my_stops(app, c):
    with app.app_context():
        from orderapp import along
        res = along.suggest(DAY, mode="stops", radius=2)
        names = [r["c"]["name"] for r in res["rows"]]
        assert names[:2] == ["Next Door Flats", "Two Miles Villas"] or set(names) == {"Next Door Flats", "Two Miles Villas"}
        assert "Way Out Lofts" not in names and res["rows"][0]["near_stop"] == "Stop One Apartments"
        assert along.suggest(DAY, mode="route")["no_points"]          # no start address -> route mode can't
    html = c.get(f"/routes/day/{DAY}/along?mode=stops&switched=1").get_data(as_text=True)
    assert "Near my stops" in html and "from Stop One Apartments" in html and "of a stop" in html
    html = c.get(f"/routes/day/{DAY}/along").get_data(as_text=True)      # remembered
    assert "from Stop One Apartments" in html
    r = c.post(f"/routes/day/{DAY}/along/add", data={"csrf_token": c.t, "customer_id": str(app.ids["Two Miles Villas"]),
                                                    f"why_{app.ids['Two Miles Villas']}": "Near Stop One Apartments",
                                                    "source": "Near my stops"})
    assert r.status_code == 302
    with app.app_context():
        from orderapp.db import q
        assert q("SELECT 1 FROM route_stops WHERE day=? AND customer_id=?", (DAY, app.ids["Two Miles Villas"]), one=True)


def test_move_stop_to_number(app, c):
    day = "2030-04-10"
    with app.app_context():
        from orderapp.db import x
        sids = [x("INSERT INTO route_stops(day, position, label, address, status) VALUES (?,?,?,?, 'Planned')",
                  (day, i, f"Stop {i + 1}", f"{i + 1} Test Rd")) for i in range(9)]
    html = c.get(f"/routes/day/{day}").get_data(as_text=True)
    assert 'name="act" value="pos"' in html and '<option value="9" selected>' in html
    c.post(f"/routes/stop/{sids[8]}", data={"csrf_token": c.t, "act": "pos", "to": "1"})   # 9th -> 1st in one go
    c.post(f"/routes/stop/{sids[0]}", data={"csrf_token": c.t, "act": "pos", "to": "99"})  # past the end -> last
    with app.app_context():
        from orderapp.db import q
        order = [r["label"] for r in q("SELECT label FROM route_stops WHERE day=? ORDER BY position", (day,))]
    assert order == ["Stop 9", "Stop 2", "Stop 3", "Stop 4", "Stop 5", "Stop 6", "Stop 7", "Stop 8", "Stop 1"]
