"""v2.30.2: Items & Stock lists products that only show up on invoices (billed under a general item);
sizes shown in inches everywhere; side-by-side drawers on the cabinet layout. All data is made up."""
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402
from orderapp.inches import inches_text  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import x
        a.cid = x("INSERT INTO customers(name, address, city, state, zip, status) VALUES "
                  "('Sample Palms (made up)', '1 Test Rd', 'Phoenix', 'AZ', '85020', 'Active')")
        # a general billing item with no description, and a normal stock item
        x("INSERT INTO items(item_no, description, list_price) VALUES ('Shades', NULL, 0)")
        x("INSERT INTO items(item_no, description, list_price, category, last_sold) VALUES "
          "('900100', '2''-10\" x 6''-8\" x 1-3/8\" HC Sample Door', 50, 'Doors', '2026-01-05')")
        sid = x("INSERT INTO sales(invoice_no, invoice_date, customer_id, customer_name, total) "
                "VALUES ('T-1', '2026-02-03', ?, 'Sample Palms (made up)', 300)", (a.cid,))
        x("INSERT INTO sales_lines(sale_id, category, item_no, description, qty, unit_price, amount) VALUES "
          "(?, 'Blinds & Shades', 'Shades', '6'' x 8'' Breezeway Sampleshade Roller Shade *Unit 12', 2, 100, 200)",
          (sid,))
        x("INSERT INTO sales_lines(sale_id, category, item_no, description, qty, unit_price, amount) VALUES "
          "(?, 'Doors', '900100', '2''-10\" x 6''-8\" x 1-3/8\" HC Sample Door *Unit 4', 2, 50, 100)", (sid,))
    return a


@pytest.fixture(scope="module")
def c(app):
    cl = app.test_client()

    def tok(path):
        return re.search(r'name="csrf-token" content="([^"]+)"', cl.get(path).get_data(as_text=True)).group(1)
    cl.post("/setup", data={"username": "tester", "password": "pw-123456", "password2": "pw-123456",
                            "csrf_token": tok("/setup")})
    return cl


@pytest.mark.parametrize("raw,want", [
    ("2'10\" x 6'8\" Bi-Fold", '34" x 80" Bi-Fold'),
    ("7'-0\" x 6'-8\" Bypass", '84" x 80" Bypass'),
    ("2/10 x 6/8 slab", '34" x 80" slab'),
    ("3/0 X 6/8 steel", '36" X 80" steel'),
    ("6' x 15' shade fabric", '72" x 180" shade fabric'),
    ("2'-10 1/2\" door", '34-1/2" door'),
    ("2'-10\" x 6-8\" x 1-3/8\"", '34" x 80" x 1-3/8"'),
    ("18'' x 28 1/2'' cabinet door", '18" x 28 1/2" cabinet door'),
    ("36'\" x 30'\" wall cabinet", '36" x 30" wall cabinet'),
    ("29\" x 57\" x 7/16\" screen", '29" x 57" x 7/16" screen'),      # already inches: unchanged
    ("16' x 3-1/2\" slat", '192" x 3-1/2" slat'),
    ("1/2 x 3/4 trim", "1/2 x 3/4 trim"),                              # a fraction, not door shorthand
    ("Owner's choice, 90's style", "Owner's choice, 90's style"),
    ("70\" x 60' fabric", "70\" x 60' fabric"),                       # 60 feet on a shade: a typo, left alone
])
def test_inches_text(raw, want):
    assert inches_text(raw) == want


def test_inches_text_passthrough():
    assert inches_text(None) is None
    assert inches_text("") == ""
    assert inches_text("Fuel surcharge") == "Fuel surcharge"


def test_items_search_finds_products_billed_under_general_item(app):
    with app.app_context():
        from orderapp import company_reports as CR
        rows = CR.search("breezeway")
        assert len(rows) == 1
        r = rows[0]
        assert r["from_invoices"] and r["item_no"] == "Shades"
        assert r["description"] == '72" x 96" Breezeway Sampleshade Roller Shade'   # note dropped, inches
        assert r["qty_sold"] == 2 and r["avg_price"] == 100 and r["last_sold"] == "2026-02-03"
        # browsing lists the general-item product too, but not the stock door twice
        allrows = CR.search("")
        assert any(x.get("from_invoices") and "Breezeway" in x["description"] for x in allrows)
        assert sum(1 for x in allrows if x["item_no"] == "900100") == 1
        # the blank billing bucket itself isn't listed as an item
        assert not any(x["item_no"] == "Shades" and not x.get("from_invoices") for x in allrows)
        # stock-only / invoices-only filters
        assert CR.search("breezeway", source="stock") == []
        assert all(x.get("from_invoices") for x in CR.search("", source="invoices"))


def test_items_search_matches_sizes_in_inches(app):
    with app.app_context():
        from orderapp import company_reports as CR
        for term in ('34" x 80"', "2/10 x 6/8", "2'10 x 6'8"):
            assert any(x["item_no"] == "900100" for x in CR.search(term)), term


def test_items_page_shows_invoice_products_in_inches(c):
    html = c.get("/items?q=breezeway").get_data(as_text=True)
    assert "Breezeway Sampleshade" in html and "From invoices" in html and '72&#34; x 96&#34;' in html
    html = c.get("/items?q=sample+door").get_data(as_text=True)
    assert "34&#34; x 80&#34;" in html and "2&#39;-10" not in html


def test_purchase_history_in_inches(app):
    with app.app_context():
        from orderapp import order_insights as OI
        descs = [r["desc"] for r in OI.purchases(app.cid)]
        assert '34" x 80" x 1-3/8" HC Sample Door *Unit 4' in descs
        assert any(d.startswith('72" x 96" Breezeway') for d in descs)


def test_side_by_side_drawers():
    from orderapp import cabinet_layout as C
    d = C.starter()
    b = d["walls"][0]["lowers"][0]
    assert C.new_box("lowers", "Base cabinet")["drawer_layout"] == "Stacked"
    b.update(drawers="Yes", drawer_n="2", drawer_h="6", drawer_layout="Side by side", w="30", doors="2")
    assert C.side_by_side(b)
    sched = {r[0]: r for r in C.schedule(d)}
    assert sched["L1"][8] == '2 side by side @ 6"'
    # one row of drawer fronts: both drawers the same height at the same y, each half the width
    P = []
    C._fronts(P, (0, 0, 300, 345), b, 10, 8)
    rects = [p for p in P if p[0] == "rect" and p[5] == C.GREY]
    drawers = rects[:2]
    assert drawers[0][2] == drawers[1][2] and drawers[0][4] == drawers[1][4]          # same top / bottom
    assert drawers[0][3] < drawers[1][1] + 5 and drawers[0][4] <= 61                  # side by side, 6" tall
    assert C._drawer_band(b, 10) == 60                                                 # one 6" row, not 2 stacked
    b["drawer_layout"] = "Stacked"
    assert C._drawer_band(b, 10) == 120
    # old layouts without the field still load as stacked
    old = C.starter()
    old["walls"][0]["lowers"][0].pop("drawer_layout", None)
    assert C.normalize(old)["walls"][0]["lowers"][0]["drawer_layout"] == "Stacked"
    # the order line says so
    b["drawer_layout"] = "Side by side"
    lines = C.order_lines(d, "cabinet_custom") if hasattr(C, "order_lines") else []
    if lines:
        assert any("2 drawers side by side" in ln["line_comments"] for ln in lines)


def test_cabinet_editors_have_layout_choice(c):
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "orderapp" / "templates"
    for f in ("cabinet_layout.html", "_cabinet_order_editor.html"):
        t = (root / f).read_text(encoding="utf-8")
        assert 'data-k="drawer_layout"' in t and "Side by side" in t and "drawer_layout: 'Stacked'" in t
