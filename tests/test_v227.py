"""v2.27: the order contact fills itself from the property - the Maint. Super, else the Property Manager.
All data is made up."""
import json
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402


@pytest.fixture(scope="module")
def app():
    d = tempfile.mkdtemp()
    a = create_app(db_path=os.path.join(d, "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import x
        a.both = x("INSERT INTO customers(name, phone, email) VALUES ('Sample Both (made up)', '(602) 555-0000', "
                   "'office@both.example')")
        x("INSERT INTO contacts(customer_id, name, role, phone) VALUES (?, 'Pam Manager', 'Property Manager', "
          "'(602) 555-0002')", (a.both,))
        x("INSERT INTO contacts(customer_id, name, role, phone, email) VALUES (?, 'Sam Super', "
          "'maintenance supervisor', '(602) 555-0001', 'sam@both.example')", (a.both,))
        x("INSERT INTO contacts(customer_id, name, role, phone) VALUES (?, 'Tia Tech', 'Maintenance', "
          "'(602) 555-0003')", (a.both,))
        a.pm = x("INSERT INTO customers(name, phone, email) VALUES ('Sample PM Only (made up)', '(480) 555-0000', "
                 "'office@pm.example')")
        x("INSERT INTO contacts(customer_id, name, role) VALUES (?, 'Lee Leasing', 'Office')", (a.pm,))
        x("INSERT INTO contacts(customer_id, name, role, email) VALUES (?, 'Pat PM', 'PM', 'pat@pm.example')", (a.pm,))
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


def _hdr(app, cid):
    with app.app_context():
        from orderapp.db import q
        from orderapp.routes.orders import customer_header
        return customer_header(q("SELECT * FROM customers WHERE id=?", (cid,), one=True))


def test_super_first(app):
    h = _hdr(app, app.both)
    assert (h["contact"], h["contact_phone"], h["contact_email"]) == ("Sam Super", "(602) 555-0001",
                                                                      "sam@both.example")


def test_pm_when_no_super(app):
    h = _hdr(app, app.pm)
    # PM has no phone on file: the property's phone fills in
    assert (h["contact"], h["contact_phone"], h["contact_email"]) == ("Pat PM", "(480) 555-0000", "pat@pm.example")


def test_new_order_and_copy_get_current_contact(app, c):
    html = c.get(f"/orders/editor?form=door&customer={app.both}").get_data(as_text=True)
    assert "Sam Super" in html and "(602) 555-0001" in html
    payload = {"header": {"name": "Sample Both (made up)", "po": "S-1", "date": "01/02/2026",
                          "contact": "Old Person", "contact_phone": "(602) 555-9999"}, "blocks": [{"qty": "1"}]}
    c.post("/orders/save", data={"csrf_token": c.t, "form_key": "door", "customer_id": app.both,
                                 "payload": json.dumps(payload), "action": "save"})
    with app.app_context():
        from orderapp.db import q
        oid = q("SELECT id FROM orders ORDER BY id DESC LIMIT 1", one=True)["id"]
    html = c.get(f"/orders/editor?copy={oid}").get_data(as_text=True)
    data = json.JSONDecoder().raw_decode(html[html.index("DATA = ") + 7:])[0]
    assert data["header"]["contact"] == "Sam Super" and data["header"]["contact_phone"] == "(602) 555-0001"
    assert data["header"]["date"] != "01/02/2026" and data["header"]["po"] == ""
