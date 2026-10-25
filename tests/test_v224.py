"""v2.24: property contact (name / phone / email) on the order forms + pre-filled customer forms. Made-up data."""
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
        a.cid = x("INSERT INTO customers(name, city, state, email) VALUES ('Sample Gardens (made up)', 'Phoenix', 'AZ', "
                  "'office@sample.example')")
        x("INSERT INTO contacts(customer_id, name, role, phone, email) VALUES (?, 'Pat Sample', 'Maint. Super', "
          "'(602) 555-0101', 'pat@sample.example')", (a.cid,))
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


H = {"name": "Sample Gardens (made up)", "contact": "Pat Sample", "contact_phone": "(602) 555-0101",
     "contact_email": "pat@sample.example", "po": "S-9", "date": "10/24/2026"}


def _text(pdf):
    import pymupdf as fitz
    return fitz.open(stream=pdf, filetype="pdf")[0].get_text()


def test_contact_boxes_on_door_style_forms(app):
    from orderapp.catalog import FORMS
    from orderapp.pdf_fill import fill_form, validate_catalog
    assert validate_catalog() == []
    for fk in ("door", "prehung", "bypass", "trim", "cabinet_custom", "countertop"):
        assert FORMS[fk]["header"]["contact_email"] == "Contact Email", fk
    with app.app_context():
        pdf, _ = fill_form("door", {"header": H, "blocks": [{"qty": "1"}]})
    t = _text(pdf)
    assert "Pat Sample" in t and "(602) 555-0101" in t and "pat@sample.example" in t
    assert "Contact:" in t        # the printed label


def test_contact_in_comments_when_no_boxes(app):
    from orderapp.catalog import FORMS
    from orderapp.pdf_fill import fill_form
    assert "contact" not in FORMS["screen_door"]["header"]
    with app.app_context():
        pdf, _ = fill_form("screen_door", {"header": H, "blocks": [{"qty": "1"}]})
    assert "Contact: Pat Sample" in _text(pdf)


def test_editor_defaults_contact_from_saved_people(app, c):
    html = c.get(f"/orders/editor?form=door&customer={app.cid}").get_data(as_text=True)
    assert 'data-h="contact"' in html and "Pat Sample" in html and "pat@sample.example" in html
    r = c.get(f"/api/customer/{app.cid}").get_json()
    assert r["header"]["contact"] == "Pat Sample" and r["people"][0]["phone"] == "(602) 555-0101"


def test_customer_form_prefilled(app):
    import glob

    import pymupdf as fitz
    from orderapp.paths import resource_path
    from orderapp.routes.forms_lib import prefilled
    src = sorted(glob.glob(resource_path(os.path.join("forms", "manager", "Customer Order Form - *.pdf"))))[0]
    with app.app_context():
        from orderapp.db import q
        out = prefilled(src, q("SELECT * FROM customers WHERE id=?", (app.cid,), one=True))
    vals = {w.field_name: w.field_value for w in fitz.open(out)[0].widgets()}
    assert vals["Property"] == "Sample Gardens (made up)" and vals["Manager Name"] == "Pat Sample"
    assert vals["Manager Email"] == "pat@sample.example"
