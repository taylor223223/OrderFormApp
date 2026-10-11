"""Sales history + reports, and the demo copy (made-up data, own database)."""
import os
import tempfile
from datetime import date

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402
from orderapp import sales as S  # noqa: E402


@pytest.fixture()
def demo_app():
    d = tempfile.mkdtemp()
    app = create_app(db_path=os.path.join(d, "demo.db"), testing=True, demo=True)
    return app


def test_category_and_mapping():
    assert S.category_for("Vert blind 96x84 alabaster") == "Blinds & Shades"
    assert S.category_for("72x80 mirrored bypass satin nickel") == "Closet & Wardrobe Doors"
    assert S.category_for("30x80 HC 6 panel primecoat") == "Doors"
    assert S.category_for("Soma lever passage") == "Door Hardware"
    assert S.category_for("Thinscape 96in run") == "Countertops"
    assert S.category_for("Mirror Mate frame kit") == "Mirror Frames"
    assert S.category_for("widget") == "Other"
    m = S.guess_mapping(["Invoice #", "Inv Date", "Customer Name", "Item", "Desc", "Qty", "Unit Price", "Ext"])
    assert m["invoice_no"] == "Invoice #" and m["customer_name"] == "Customer Name" and m["amount"] == "Ext"
    assert S._num("(1,234.50)") == -1234.5 and S._date("3/4/2026") == "2026-03-04"


def test_demo_seed_reports_and_reset(demo_app):
    c = demo_app.test_client()
    r = c.get("/")                                    # demo copy logs straight in
    assert r.status_code == 200 and b"DEMO COPY" in r.data
    with demo_app.app_context():
        from orderapp.db import q, setting
        assert q("SELECT COUNT(*) n FROM customers", one=True)["n"] == 12
        assert q("SELECT COUNT(*) n FROM sales", one=True)["n"] > 200
        assert setting("email_provider") == "eml"          # nothing is ever really sent from the demo
        y = date.today().year
        assert S.total(y) > 0 and len(S.monthly(y)) == 12
        gl = S.goals(y)
        assert gl[5]["goal"] == 55000 and gl[0]["goal"] == 42000 and gl[0]["commission"] == round(gl[0]["sales"] * 0.03, 2)
        assert any(g["name"] == "Canyon Vista Apartments" for g in S.gaps(y))   # win-back list
        assert q("SELECT COUNT(*) n FROM sales WHERE customer_id IS NULL", one=True)["n"] > 0
        cid = q("SELECT id FROM customers WHERE name='Palo Verde Commons'", one=True)["id"]
        assert S.customer_summary(cid)["ytd"] > 0
    for url in ("/sales", "/sales/invoices", "/sales/goals", f"/sales?customer={cid}", "/sales?category=Doors",
                f"/customers/{cid}"):
        assert c.get(url).status_code == 200, url
    assert c.get("/sales/export.csv").data.count(b"\n") > 300
    token = c.get("/").data.split(b'name="csrf-token" content="')[1].split(b'"')[0].decode()
    with demo_app.app_context():
        from orderapp.db import x
        x("DELETE FROM customers")
    r = c.post("/demo/reset", data={"csrf_token": token})
    assert r.status_code == 302
    with demo_app.app_context():
        from orderapp.db import q
        assert q("SELECT COUNT(*) n FROM customers", one=True)["n"] == 12


def test_sales_import_dedupe(demo_app):
    rows = [{"Inv": "Z-1", "Date": "2026-02-03", "Property": "Agave Pointe", "Desc": "Window screen bronze",
             "Qty": "3", "Price": "20"},
            {"Inv": "Z-1", "Date": "2026-02-03", "Property": "Agave Pointe", "Desc": "Faux wood blind", "Qty": "2",
             "Price": "45"},
            {"Inv": "Z-2", "Date": "", "Property": "Agave Pointe", "Desc": "no date", "Qty": "1", "Price": "1"}]
    with demo_app.app_context():
        m = S.guess_mapping(list(rows[0]))
        res = S.import_rows(rows, m)
        assert res["invoices"] == 1 and res["skipped_rows"] == 1 and res["matched"] == 1
        res = S.import_rows(rows, m)
        assert res["replaced"] == 1 and res["added"] == 0
        from orderapp.db import q
        s = q("SELECT * FROM sales WHERE invoice_no='Z-1'", one=True)
        assert s["total"] == 150 and q("SELECT COUNT(*) n FROM sales WHERE invoice_no='Z-1'", one=True)["n"] == 1


def test_real_app_is_not_demo():
    d = tempfile.mkdtemp()
    app = create_app(db_path=os.path.join(d, "real.db"), testing=True)
    with app.app_context():
        from orderapp.db import q
        assert q("SELECT COUNT(*) n FROM customers", one=True)["n"] == 0
    assert app.test_client().get("/demo/reset", follow_redirects=False).status_code in (404, 405)
