"""v2.11: data safety through updates, back buttons, orders folder, tracking totals, products bought,
previous orders + price estimates on the order form. All data here is made up."""
import os
import re
import sqlite3
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app, __version__  # noqa: E402


def _csrf(c):
    return re.search(rb'name="csrf-token" content="([^"]+)"', c.get("/").data).group(1).decode()


def test_update_keeps_data_and_backs_up(tmp_path):
    from orderapp import safety
    path = str(tmp_path / "orderapp.db")
    app = create_app(db_path=path, testing=True)
    with app.app_context():
        from orderapp.db import x
        x("INSERT INTO customers(name) VALUES ('Keep Me Apartments')")
        x("UPDATE settings SET value='2.0.0' WHERE key='app_version'")
    made = safety.protect(path, __version__)            # what a new version does on start-up
    assert made and os.path.exists(made) and f"before-v{__version__}" in made
    assert sqlite3.connect(made).execute("SELECT name FROM customers").fetchone()[0] == "Keep Me Apartments"
    app2 = create_app(db_path=path, testing=True)       # the "new version" opening the same file
    with app2.app_context():
        from orderapp.db import q, setting
        assert q("SELECT name FROM customers", one=True)["name"] == "Keep Me Apartments"
        assert setting("app_version") == __version__
    st = safety.status(path)
    assert st["counts"]["customers"] == 1 and st["backups"]
    assert safety.protect(path, __version__) is None    # same version again: no new "before" copy


@pytest.fixture(scope="module")
def app():
    d = tempfile.mkdtemp()
    a = create_app(db_path=os.path.join(d, "f.db"), testing=True, demo=True)
    with a.app_context():
        from orderapp.db import set_setting
        set_setting("output_dir", os.path.join(d, "out"))
    return a


def Q(app, sql, a=(), one=False):
    with app.app_context():
        from orderapp.db import q
        return q(sql, a, one)


def test_sizes():
    from orderapp.order_insights import fmt_in, parse_size
    assert parse_size('2\'-6" x 6\'-8" x 1-3/8" HC Primecoat Door') == (30.0, 80.0)
    assert parse_size('59-1/2" x 72" White 2" Faux Wood Blind') == (59.5, 72.0)
    assert parse_size("36 1/4\" x 100 VX roller shade") == (36.25, 100.0)
    assert parse_size("Fuel Surcharge") is None
    assert fmt_in(59.5) == '59-1/2"' and fmt_in(11.375) == '11-3/8"'


def test_nav_back_and_settings(app):
    c = app.test_client()
    html = c.get("/").data.decode()
    assert html.index("New Order") < html.index("Order Tracking") < html.index("Order Forms")
    cid = Q(app, "SELECT id FROM customers ORDER BY id LIMIT 1", one=True)["id"]
    assert "Back to Customers" in c.get(f"/customers/{cid}").data.decode()
    oid = Q(app, "SELECT id FROM orders ORDER BY id LIMIT 1", one=True)["id"]
    assert "backbtn" in c.get(f"/orders/{oid}").data.decode()          # "← Order tracking" becomes a button
    s = c.get("/settings").data.decode()
    assert "safe through updates" in s and "Organize order PDFs by" in s


def test_order_pdf_folder_and_name(app):
    c = app.test_client()
    t = _csrf(c)
    with app.app_context():
        from orderapp.db import set_setting
        set_setting("pdf_layout", "customer_month")
    o = Q(app, "SELECT o.*, c.name cname FROM orders o JOIN customers c ON c.id=o.customer_id "
               "WHERE o.form_key IS NOT NULL ORDER BY o.id LIMIT 1", one=True)
    with app.test_request_context():
        from orderapp.routes.orders import _make_pdf
        path, _ = _make_pdf(o["id"])
    name = os.path.basename(path)
    assert re.match(r"^\d{4}-\d{2}-\d{2} - ", name) and os.path.exists(path)
    assert re.search(r"[\\/]\d{4}-\d{2}$", os.path.dirname(path))      # customer / month
    with app.app_context():
        from orderapp.db import set_setting
        set_setting("pdf_layout", "customer")


def test_tracking_totals(app):
    c = app.test_client()
    html = c.get("/tracking").data.decode()
    assert "Put in today" in html and "Put in year to date" in html and "Conservative estimate" in html
    with app.app_context():
        from orderapp.order_insights import tracking_totals
        t = tracking_totals()
    assert [p["label"] for p in t["periods"]] == ["Today", "This week", "This month", "Year to date"]
    assert t["periods"][3]["orders"] >= t["periods"][2]["orders"] >= t["periods"][0]["orders"]
    assert t["periods"][3]["total"] > 0


def test_products_bought_tab(app):
    c = app.test_client()
    cid = Q(app, "SELECT customer_id FROM sales WHERE customer_id IS NOT NULL GROUP BY customer_id "
                 "ORDER BY COUNT(*) DESC LIMIT 1", one=True)["customer_id"]
    html = c.get(f"/customers/{cid}?tab=products").data.decode()
    assert "Products &amp; Sizes Bought" in html and "Total qty" in html
    assert "Buys most" in c.get(f"/customers/{cid}").data.decode()
    assert "Every invoice line" in c.get(f"/customers/{cid}?tab=products&view=lines").data.decode()
    with app.app_context():
        from orderapp.order_insights import condensed, purchases
        rows = condensed(cid)
        lines = purchases(cid)
    assert rows and all(r["qty"] > 0 for r in rows)
    # rolled up: a product+size bought on several invoices is one line
    assert len(rows) < len([ln for ln in lines if ln["qty"]])


def test_order_help_api(app):
    c = app.test_client()
    t = _csrf(c)
    cid = Q(app, "SELECT id FROM customers WHERE name='Palo Verde Commons'", one=True)["id"]
    r = c.post("/api/order-help", json={"customer": cid, "form": "vertical_blind",
                                        "blocks": [{"qty": "2", "item_no": "VB-35-ALB"}]},
               headers={"X-CSRF-Token": t}).get_json()
    assert r["previous"] and any(p["match"] for p in r["previous"])
    e = r["estimate"]
    assert e["lines"][0]["est"]["exact"]["source"] == "Account price" and e["low"] > 0
    r = c.post("/api/order-help", json={"customer": None, "form": "horizontal_blind",
                                        "blocks": [{"qty": "3", "width": "35", "height": "72"}]},
               headers={"X-CSRF-Token": t}).get_json()
    assert r["previous"] == [] and r["estimate"]["lines"]
    assert "Previous orders" in c.get("/orders/editor?form=door").data.decode()


def test_old_saved_values_still_work():
    from orderapp.catalog import split_blind_style, split_closet, split_door_style
    assert split_blind_style({"style": '2" Metal Plus'}) == {"style": "Metal Plus", "slat_size": '2"'}
    d = split_door_style({"style": "2 Panel Arch"})
    assert d == {"style": "Arch", "panels": "2 Panel", "door_top": "Arched Top"}
    assert split_door_style({"style": "5 Panel"}) == {"panels": "5 Panel"}
    c = split_closet({"panels": "4", "style": "6 Panel"})
    assert c["leaves"] == "4" and c["style"] == "Colonist" and c["panels"] == "6 Panel"
    assert split_closet({"panels": "2 Panel", "style": "Shaker"}) == {"panels": "2 Panel", "style": "Shaker"}


def test_closet_form_ball_catch(app):
    from orderapp.catalog import public_spec
    from orderapp.pdf_fill import fill_form, read_filled_form
    keys = [f["key"] for f in public_spec("closet_bifold")["fields"]]
    for k in ("ball_catch", "panels", "leaves", "core", "thickness", "width", "height", "opening_width"):
        assert k in keys
    fk, back = read_filled_form(fill_form("closet_bifold", {"blocks": [{"closet_type": "Bi-Fold (standard)",
                                                                         "ball_catch": "Yes", "leaves": "2",
                                                                         "width": "18", "height": "80"}]})[0])
    assert fk == "closet_bifold" and back["blocks"][0]["ball_catch"] == "Yes" and back["blocks"][0]["leaves"] == "2"


def test_order_notices(app):
    c = app.test_client()
    t = _csrf(c)
    with app.app_context():
        from orderapp.db import x
        x("UPDATE orders SET status='Sent', po='PO-55501', vendor_ref='' WHERE id=(SELECT MIN(id) FROM orders "
          "WHERE status NOT IN ('Delivered','Completed','Installed','Cancelled','Draft'))")
        from orderapp import order_notices as ON
        o = Q(app, "SELECT * FROM orders WHERE po='PO-55501'", one=True)
        assert ON.is_notice("Order Received - PO 55501") and not ON.is_notice("Deliveries today")
        _, res, n = ON.record("Thank you! Order received for PO# 55501. Sales Order #290001", "email",
                              "Order Received", auto_apply=True)
        assert n == 1
    o2 = Q(app, "SELECT * FROM orders WHERE id=?", (o["id"],), one=True)
    assert o2["status"] == "Confirmed" and o2["vendor_ref"] == "290001"
    with app.app_context():
        from orderapp import order_notices as ON
        _, _, n = ON.record("Order received PO 55501", "email", "Order Received", auto_apply=True)
    assert n == 0                                   # already Confirmed - never moves backwards or twice
    assert c.get("/order-notices").status_code == 200
    c.post("/order-notices", data={"csrf_token": t, "text": "Order Acknowledgement PO-55501"})
    assert b"Order replies" in c.get("/tracking").data


def test_reply_got_it(app):
    with app.app_context():
        from orderapp.db import x
        from orderapp import order_notices as ON
        ids = [r["id"] for r in Q(app, "SELECT id FROM orders WHERE status NOT IN ('Delivered','Completed','Installed',"
                                       "'Cancelled','Draft','Confirmed') ORDER BY id LIMIT 3")]
        a, b, d = ids
        x("UPDATE orders SET status='Ready', po='77120', sent_subject='Door Order - Test Place - PO 77120' WHERE id=?", (a,))
        x("UPDATE orders SET status='Sent', po='88231', sent_subject=NULL WHERE id=?", (b,))
        x("UPDATE orders SET status='Sent', po='99342', sent_subject='Blinds - Test - PO 99342' WHERE id=?", (d,))
        quoted = "\n\nFrom: Taylor\nSent: Monday\nSubject: Door Order\nPlease order the following, any questions?"
        assert ON.reply_verdict("Got it" + quoted)[0] == "yes"       # the quoted '?' below doesn't count
        assert ON.reply_verdict("got it, but the 2nd door is missing a size?")[0] == "check"
        assert ON.reply_verdict("")[0] == "no"
        cid, res, n = ON.record_reply({"subject": "RE: Door Order - Test Place - PO 77120", "body": "Got it" + quoted,
                                       "sender": "jimmy@x.com"}, "m1")
        assert n == 1 and res[0]["order_id"] == a
        # no stored subject: the PO # in the reply subject still finds it
        _, res, n = ON.record_reply({"subject": "Re: RE: Something - PO 88231", "body": "got it thanks"}, "m2")
        assert n == 1 and res[0]["order_id"] == b
        # a question waits for you
        _, res, n = ON.record_reply({"subject": "RE: Blinds - Test - PO 99342", "body": "Which color on these?"}, "m3")
        assert n == 0 and res and not res[0]["sure"]
        assert ON.is_reply("RE: x") and not ON.is_reply("Order for Unit 5")
    st = {r["id"]: r["status"] for r in Q(app, "SELECT id, status FROM orders WHERE id IN (?,?,?)", (a, b, d))}
    assert st[a] == "Confirmed" and st[b] == "Confirmed" and st[d] == "Sent"


def test_plan_page(app):
    c = app.test_client()
    t = _csrf(c)
    for url in ("/plan", "/plan?view=month", "/"):
        assert c.get(url).status_code == 200
    with app.app_context():
        from orderapp import planner as PL
        recs = PL.recommendations(7)
        proj = PL.projection()
    assert recs and all(r["why"] and r.get("day") for r in recs)
    assert proj["mtd"] >= 0 and "plan_total" in proj
    html = c.get("/plan").data.decode()
    assert "Who to see this week" in html and "where it's heading" in html
    cid = Q(app, "SELECT id FROM customers ORDER BY id LIMIT 1", one=True)["id"]
    before = Q(app, "SELECT COUNT(*) n FROM route_stops", one=True)["n"]
    c.post("/plan/add", data={"csrf_token": t, "pick": ["1"], "cid_1": str(cid), "day_1": "2030-01-07",
                              "why_1": "Overdue", "name_1": "", "addr_1": ""})
    s = Q(app, "SELECT * FROM route_stops WHERE day='2030-01-07'", one=True)
    assert s and s["customer_id"] == cid and Q(app, "SELECT COUNT(*) n FROM route_stops", one=True)["n"] == before + 1
    c.post("/plan/projection", data={"csrf_token": t, f"exp_{s['id']}": "1,250", "win_rate": "55"})
    assert Q(app, "SELECT expected FROM route_stops WHERE id=?", (s["id"],), one=True)["expected"] == 1250


def test_kitchen_measure(app):
    from orderapp import kitchen as K
    assert K.num("25-1/2") == 25.5 and K.num("8'") == 96 and K.fin(76.5) == '76-1/2"'
    d = K.normalize({"shape": "u", "island": {"length": "60"},
                     "runs": {"A": {"length": "96", "items": [{"type": "fridge", "from": "0", "width": "36"}]},
                              "B": {"length": "144", "items": [{"type": "sink", "from": "60", "width": "33"}]},
                              "C": {"length": "108", "items": [{"type": "range", "from": "40", "width": "30"}]}}})
    ps = {p["name"]: p for p in K.pieces(d)}
    assert set(ps) == {"A", "B", "C1", "C2", "I"}                 # the range splits run C in two
    assert ps["A"]["length"] == 60 and ps["A"]["left"] == "Appliance"
    assert ps["B"]["cutouts"][0]["center_from_left"] == 76.5
    assert ps["C2"]["length"] == 38 and ps["I"]["left"] == "Finished end"
    svg = K.top_svg(d)
    assert svg.startswith("<svg") and ">B<" in svg and ">C<" in svg and "RANGE" in svg
    c = app.test_client()
    t = _csrf(c)
    cid = Q(app, "SELECT id FROM customers ORDER BY id LIMIT 1", one=True)["id"]
    r = c.post("/kitchen/new", data={"csrf_token": t, "customer_id": cid, "name": "Kitchen B2", "shape": "l"})
    lid = int(r.headers["Location"].rsplit("/", 1)[1])
    assert c.get(f"/kitchen/{lid}").status_code == 200
    j = c.post(f"/kitchen/{lid}", json={"data": dict(d, shape="l"), "name": "Kitchen B2", "unit": "1107"},
               headers={"X-CSRF-Token": t}).get_json()
    assert j["ok"] and j["sqft"] > 0
    p = c.post(f"/kitchen/{lid}/preview", json={"data": d}, headers={"X-CSRF-Token": t}).get_json()
    assert set(p["elev"]) == {"A", "B", "C", "I"}
    pdf = c.get(f"/kitchen/{lid}/pdf")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    import fitz
    doc = fitz.open(stream=pdf.data, filetype="pdf")
    txt = doc[0].get_text()
    assert "Measure Sheet" in txt and "COUNTERTOP PIECES" in txt and len(doc) >= 2
    assert c.get("/kitchen/blank.pdf").data[:4] == b"%PDF"
    assert c.get(f"/kitchen?customer={cid}").status_code == 200


def test_reply_settings_save(app):
    c = app.test_client()
    t = _csrf(c)
    assert b"got it" in c.get("/settings").data
    c.post("/settings", data={"csrf_token": t, "notice_subject_words": "Order Received",
                              "reply_confirm_words": "got it, rcvd"})
    with app.app_context():
        from orderapp import order_notices as ON
        assert not ON.reply_on() and ON.reply_words() == ["got it", "rcvd"]
    c.post("/settings", data={"csrf_token": t, "notice_subject_words": "Order Received", "reply_confirm": "1",
                              "reply_confirm_words": "got it"})
    with app.app_context():
        from orderapp import order_notices as ON
        assert ON.reply_on()
