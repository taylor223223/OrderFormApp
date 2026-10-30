"""v2.30.1: reorders fill in the contact like new orders; a skipped stop can move to tomorrow / any day;
Avalon door style. All data is made up."""
import json
import os
import re
import tempfile
from datetime import date, timedelta

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402

TODAY = date.today().isoformat()
TOMORROW = (date.today() + timedelta(days=1)).isoformat()


@pytest.fixture(scope="module")
def app():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import set_setting, x
        set_setting("route_start_address", "")
        a.cid = x("INSERT INTO customers(name, address, city, state, zip, phone, email, lat, lon, status) VALUES "
                  "('Sample Palms (made up)', '1 Test Rd', 'Phoenix', 'AZ', '85020', '(602) 555-0100', "
                  "'office@samplepalms.example', 33.6, -112.07, 'Active')")
        x("INSERT INTO contacts(customer_id, name, role, phone, email) VALUES (?, 'Pat Manager', 'Property Manager', "
          "'(602) 555-0102', 'pat@samplepalms.example')", (a.cid,))
        x("INSERT INTO contacts(customer_id, name, role, phone) VALUES (?, 'Sam Super', 'Maint. Super', '(602) 555-0101')",
          (a.cid,))
        a.sid = x("INSERT INTO route_stops(day, position, customer_id, label, address, lat, lon, status, notes) "
                  "VALUES (?, 0, ?, 'Sample Palms (made up)', '1 Test Rd', 33.6, -112.07, 'Planned', 'Gate 1234')",
                  (TODAY, a.cid))
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


def _post(c, url, data, **kw):
    return c.post(url, data={**data, "csrf_token": c.t}, **kw)


def test_reorder_request_contact(app, c):
    with app.app_context():
        from orderapp import reorder as RO
        from orderapp.db import loads, q
        cust = q("SELECT * FROM customers WHERE id=?", (app.cid,), one=True)
        assert RO.default_contact(cust) == {"contact": "Sam Super", "contact_phone": "(602) 555-0101",
                                            "contact_email": "office@samplepalms.example"}
        oid = RO.create(app.cid, [{"qty": "2", "desc": "Vertical blind (made up)"}])
        h = loads(q("SELECT data FROM orders WHERE id=?", (oid,), one=True)["data"])["header"]
        assert (h["contact"], h["contact_phone"], h["contact_email"]) == \
            ("Sam Super", "(602) 555-0101", "office@samplepalms.example")
        oid2 = RO.create(app.cid, [{"qty": "1", "desc": "x"}], contact="Pat Manager", contact_phone="(602) 555-0102",
                         contact_email="pat@samplepalms.example")        # changed for this order
        h2 = loads(q("SELECT data FROM orders WHERE id=?", (oid2,), one=True)["data"])["header"]
        assert h2["contact"] == "Pat Manager" and h2["contact_email"] == "pat@samplepalms.example"
        import pymupdf
        from orderapp.routes.orders import _make_pdf
        path, _ = _make_pdf(oid)
    t = pymupdf.open(path)[0].get_text()
    assert "CONTACT (WHO TO TALK TO)" in t and "Sam Super" in t and "office@samplepalms.example" in t
    ed = c.get(f"/orders/{oid}/reorder_lines").get_data(as_text=True)
    assert 'name="contact_phone" value="(602) 555-0101"' in ed


def test_one_click_reorder_refreshes_contact(app, c):
    r = _post(c, "/orders/save", {"form_key": "door", "customer_id": app.cid, "kind": "order", "action": "save",
                                  "payload": json.dumps({"header": {"name": "Sample Palms (made up)"},
                                                         "blocks": [{"qty": "1", "width": "30", "height": "80"}]})})
    oid = int(r.headers["Location"].rsplit("/", 1)[1])
    r = _post(c, f"/orders/{oid}/reorder", {})
    nid = int(r.headers["Location"].rsplit("=", 1)[1])
    with app.app_context():
        from orderapp.db import loads, q
        h = loads(q("SELECT data FROM orders WHERE id=?", (nid,), one=True)["data"])["header"]
    assert h["contact"] == "Sam Super" and h["contact_phone"] == "(602) 555-0101" and h["po"] == ""


def test_skip_move_to_tomorrow(app, c):
    html = c.get(f"/routes/day/{TODAY}").get_data(as_text=True)
    assert "Skip ▾" in html and "→ Tomorrow" in html and "Just skip" in html and 'type="date" name="to_day"' in html
    r = _post(c, f"/routes/stop/{app.sid}", {"act": "move", "to_day": TOMORROW}, follow_redirects=True)
    assert "Moved to tomorrow" in r.get_data(as_text=True)
    with app.app_context():
        from orderapp.db import q
        assert not q("SELECT 1 FROM route_stops WHERE day=? AND customer_id=?", (TODAY, app.cid))
        st = q("SELECT * FROM route_stops WHERE day=? AND customer_id=?", (TOMORROW, app.cid), one=True)
    assert st and st["status"] == "Planned" and st["notes"] == "Gate 1234"
    assert "Sample Palms (made up)" in c.get(f"/routes/day/{TOMORROW}").get_data(as_text=True)


def test_avalon_door_style(app):
    import pymupdf
    from orderapp.catalog import FORMS
    from orderapp.paths import resource_path
    from orderapp.pdf_fill import fill_form, read_filled_form
    for fk in ("door", "prehung"):
        f = next(x for x in FORMS[fk]["blocks"][0] if x["key"] == "style")
        assert "Avalon" in [o[0] for o in f["options"]], fk
    with app.app_context():
        pdf, _ = fill_form("door", {"header": {"name": "x"}, "blocks": [{"qty": "1", "style": "Avalon",
                                                                         "panels": "3 Panel", "width": "30"}]})
    got = read_filled_form(pdf)[1]["blocks"][0]
    assert got["style"] == "Avalon" and got["panels"] == "3 Panel"
    cust = pymupdf.open(resource_path(os.path.join("forms", "manager", "Customer Order Form - Interior Doors.pdf")))
    assert any("Style Avalon" in w.field_name for p in cust for w in p.widgets())
    assert "Avalon" in [o[0] for o in next(x for x in FORMS["closet_bifold"]["blocks"][0] if x["key"] == "style")["options"]]
