"""v2.29: one Cabinet Order Form - the kitchen layout (pre-fab boxes, optional drawers, add boxes as needed);
countertop measured by back wall + side wall, backsplash picked per side. All data is made up."""
import json
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import x
        a.cid = x("INSERT INTO customers(name, phone) VALUES ('Sample Gardens (made up)', '(602) 555-0100')")
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


def _text(pdf):
    import pymupdf
    return "".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))


def test_one_cabinet_form():
    from orderapp.catalog import FORMS, PRODUCTS, form_list
    keys = [k for k, _ in form_list()]
    assert "cabinet_prefab" in keys and "cabinet_custom" not in keys
    assert FORMS["cabinet_prefab"]["title"] == "Cabinet Order Form" and FORMS["cabinet_prefab"]["layout_editor"]
    assert PRODUCTS["cabinet"]["forms"] == ["cabinet_prefab"]


def test_cabinet_order_is_the_layout(app, c):
    html = c.get(f"/orders/editor?form=cabinet_prefab&customer={app.cid}").get_data(as_text=True)
    assert 'id="cabOrder"' in html and "window.CAB_ORDER" in html and 'id="blocks" hidden' in html
    from orderapp import cabinet_layout as C
    lay = C.order_starter()
    lay["unit"] = "204"
    lay["cab"] = {"material": "Plywood", "color": "White", "hw": "Custom", "hw_custom": "Black bar pull (made up)"}
    w = lay["walls"][0]
    w["lowers"].append(C.new_box("lowers", "Sink base"))
    w["lowers"].append(C.new_box("lowers", "Drawer base"))
    w["uppers"].append(C.new_box("uppers", "Wall cabinet"))
    payload = {"header": {"name": "Sample Gardens (made up)", "po": "CAB-1", "date": "10/26/2026",
                          "contact": "Pat Sample", "contact_phone": "(602) 555-0101"},
               "blocks": [], "comments": "Made-up test order", "layout": lay}
    r = c.post("/orders/save", data={"csrf_token": c.t, "form_key": "cabinet_prefab", "customer_id": app.cid,
                                     "payload": json.dumps(payload), "action": "pdf"})
    oid = int(r.headers["Location"].rsplit("/", 1)[1])
    with app.app_context():
        from orderapp.db import loads, q
        o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    d = loads(o["data"])
    assert len(d["blocks"]) == 4 and {b["cab_item"] for b in d["blocks"]} == {"Base Cabinet", "Sink Base",
                                                                               "Drawer Base", "Wall Cabinet"}
    assert all(b["cab_material"] == "Plywood" and b["unit"] == "204" for b in d["blocks"])
    with open(o["pdf_path"], "rb") as fh:
        t = _text(fh.read())
    for v in ("Cabinet Order Form", "CAB-1", "Pat Sample", "Cabinet material: Plywood", "Cabinet color: White",
              "Black bar pull (made up)", "Made-up test order", "Drawer base", "Unit 204"):
        assert v in t, v
    assert "Countertop" not in t
    # reopening keeps the layout editor
    html = c.get(f"/orders/editor?order={oid}").get_data(as_text=True)
    assert 'id="cabOrder"' in html and "Black bar pull (made up)" in html


def test_old_line_cabinet_orders_still_open(app, c):
    payload = {"header": {"name": "Sample Gardens (made up)", "po": "OLD-1"}, "blocks": [{"qty": "1", "width": "24"}]}
    r = c.post("/orders/save", data={"csrf_token": c.t, "form_key": "cabinet_prefab", "customer_id": app.cid,
                                     "payload": json.dumps(payload), "action": "save"})
    oid = int(r.headers["Location"].rsplit("/", 1)[1])
    html = c.get(f"/orders/editor?order={oid}").get_data(as_text=True)
    assert 'id="cabOrder"' not in html


def test_countertop_back_and_side_wall_backsplash_per_side(app):
    from orderapp.catalog import FORMS, upgrade_blocks
    keys = [f["key"] for f in FORMS["countertop"]["blocks"][0]]
    assert not {"length_b", "length_c", "ct_layout", "backsplash", "end_splash"} & set(keys)
    assert {"length", "depth", "bs_back", "bs_left", "bs_right"} <= set(keys)
    f = {x["key"]: x for x in FORMS["countertop"]["blocks"][0]}
    assert f["depth"]["label"].startswith("Side Wall Length")
    d = upgrade_blocks({"blocks": [{"backsplash": "Yes", "end_splash": "Both"}, {"backsplash": "No", "end_splash": "Left"}]})
    assert d["blocks"][0] == {"bs_back": "Yes", "bs_left": "Yes", "bs_right": "Yes"}
    assert d["blocks"][1] == {"bs_left": "Yes"}
    from orderapp.pdf_fill import fill_form
    with app.app_context():
        pdf, _ = fill_form("countertop", {"header": {"name": "Sample (made up)"}, "blocks": [
            {"qty": "1", "length": "96", "depth": "25-1/2", "bs_back": "Yes", "bs_right": "Yes"}]})
    import pymupdf
    page = pymupdf.open(stream=pdf, filetype="pdf")[0]
    on = {w.field_name for w in page.widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX and w.field_value
          not in (False, "Off", "")}
    assert any("bs_back" in n for n in on) and any("bs_right" in n for n in on) and not any("bs_left" in n for n in on)
    t = _text(pdf)
    assert "Side wall length" in t and "Run B" not in t


def test_blank_cabinet_order_form_and_customer_forms():
    from orderapp import cabinet_layout as C
    t = _text(C.blank_pdf(order=True))
    assert "Cabinet Order Form" in t and "Hardware" in t and "Countertop" not in t
