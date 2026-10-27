"""v2.29.4: one line per property per day on the weekly report; Visited records ONE site visit with the
property's contact; "Ordered?" + what next to Visited on the route. All data is made up."""
import csv
import io
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402

MON, TUE, WED = "2030-05-13", "2030-05-14", "2030-05-15"


@pytest.fixture(scope="module")
def app():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import set_setting, x
        set_setting("sales_rep", "Sample Rep")
        set_setting("route_start_address", "")
        a.cid = x("INSERT INTO customers(name, address, city, state, zip, lat, lon, status) VALUES "
                  "('Sample Palms (made up)', '1 Test Rd', 'Phoenix', 'AZ', '85020', 33.6, -112.07, 'Active')")
        a.cid2 = x("INSERT INTO customers(name, address, city, state, zip, lat, lon, status) VALUES "
                   "('Demo Ridge (made up)', '2 Test Rd', 'Phoenix', 'AZ', '85020', 33.61, -112.07, 'Active')")
        x("INSERT INTO contacts(customer_id, name, role, phone) VALUES (?, 'Pat Manager', 'Property Manager', '602-555-0100')", (a.cid,))
        x("INSERT INTO contacts(customer_id, name, role, phone) VALUES (?, 'Sam Super', 'Maint. Super', '602-555-0101')", (a.cid,))
        a.s1 = x("INSERT INTO route_stops(day, position, customer_id, label, address, lat, lon, status) "
                 "VALUES (?, 0, ?, 'Sample Palms (made up)', '1 Test Rd', 33.6, -112.07, 'Planned')", (TUE, a.cid))
        a.s2 = x("INSERT INTO route_stops(day, position, customer_id, label, address, lat, lon, status) "
                 "VALUES (?, 1, ?, 'Demo Ridge (made up)', '2 Test Rd', 33.61, -112.07, 'Planned')", (TUE, a.cid2))
        a.s3 = x("INSERT INTO route_stops(day, position, customer_id, label, address, lat, lon, status) "
                 "VALUES (?, 0, ?, 'Sample Palms (made up)', '1 Test Rd', 33.6, -112.07, 'Planned')", (WED, a.cid))
        # a call that morning, and the route "asked for a visit" note -> same line as the visit
        x("""INSERT INTO activities(customer_id, kind, occurred_at, subject, notes) VALUES
             (?, 'Call', ?, 'Verticals quote', 'Needs 12 sets')""", (a.cid, TUE + " 08:05"))
        x("""INSERT INTO activities(customer_id, kind, occurred_at, subject, notes) VALUES
             (?, 'Text', ?, 'Drop samples', 'Added to route for 2030-05-14')""", (a.cid, TUE + " 08:10"))
        # an order made in the app that day
        x("""INSERT INTO orders(customer_id, form_key, title, po, status, created) VALUES
             (?, 'vertical_blind', 'Vertical Blind Order Form', 'S-4471', 'Sent', ?)""", (a.cid, TUE + " 10:00:00"))
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


def _post(c, url, data):
    return c.post(url, data={**data, "csrf_token": c.t}, follow_redirects=True)


def test_route_page_has_ordered_picker(app, c):
    html = c.get(f"/routes/day/{TUE}").get_data(as_text=True)
    assert "Ordered?" in html and 'name="order_items"' in html and f'form="vf{app.s1}"' in html


def test_visited_one_entry_with_contact_and_order(app, c):
    _post(c, f"/routes/stop/{app.s1}", {"act": "Done", "ordered": "Yes", "order_items": ["Verticals", "Doors"],
                                       "order_note": "12 sets"})
    _post(c, f"/routes/stop/{app.s2}", {"act": "Done", "ordered": "No"})
    _post(c, f"/routes/stop/{app.s3}", {"act": "Done"})
    # marking it visited again doesn't add a second entry
    _post(c, f"/routes/stop/{app.s1}", {"act": "Done"})
    with app.app_context():
        from orderapp.db import q
        v = q("SELECT * FROM activities WHERE customer_id=? AND kind='Site visit' AND substr(occurred_at,1,10)=?",
              (app.cid, TUE))
        assert len(v) == 1
        v = v[0]
        assert v["contact_name"] == "Sam Super"          # the super, not the PM
        assert v["ordered"] == "Yes" and v["order_what"] == "Verticals, Doors - 12 sets"
    html = c.get(f"/routes/day/{TUE}").get_data(as_text=True)
    assert "Ordered: Verticals, Doors - 12 sets" in html and "No order" in html and "Sam Super" in html
    # change it afterwards
    _post(c, f"/routes/stop/{app.s2}", {"act": "ordered", "ordered": "Yes", "order_items": ["Window screens"]})
    with app.app_context():
        from orderapp.db import q
        a = q("SELECT a.* FROM activities a JOIN route_stops s ON s.activity_id=a.id WHERE s.id=?", (app.s2,), one=True)
        assert a["ordered"] == "Yes" and a["order_what"] == "Window screens"


def test_report_one_line_per_property_per_day(app, c):
    from datetime import date
    with app.app_context():
        from orderapp.routes.crm import report_data
        dd = report_data(date.fromisoformat(MON))
    palms = [v for v in dd["visits"] if v["cname"].startswith("Sample Palms")]
    assert [v["day"] for v in palms] == [TUE, WED]       # two days -> two lines, not five
    tue = palms[0]
    assert tue["visited"] and tue["contacts"] == ["Sam Super"] and tue["ordered"] == "Yes"
    assert "Verticals, Doors - 12 sets" in tue["what"] and "PO S-4471" in tue["what"]
    assert any(n.startswith("Call: Verticals quote") for n in tue["notes"])
    assert not any("Added to route" in n for n in tue["notes"])
    assert palms[1]["contacts"] == ["Sam Super"]
    rows = list(csv.reader(io.StringIO(c.get(f"/crm/report/file/csv?week={MON}").data.decode("utf-8-sig"))))
    assert rows[0][:3] == ["Date", "Time", "Property"] and "Ordered" in rows[0]
    assert sum(1 for r in rows[1:] if r[2].startswith("Sample Palms")) == 2
    pdf = c.get(f"/crm/report/file/pdf?week={MON}")
    assert pdf.status_code == 200 and pdf.data[:4] == b"%PDF"
    html = c.get(f"/crm/report?week={MON}").get_data(as_text=True)
    assert "Site visits" in html and "one line per property per day" in html
