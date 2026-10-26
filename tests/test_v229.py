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


def test_countertop_without_sink_drops_the_sink_drawing(app):
    import pymupdf
    from orderapp.pdf_fill import fill_form
    with app.app_context():
        none, _ = fill_form("countertop", {"header": {"name": "Sample (made up)"},
                                           "blocks": [{"qty": "1", "sink": "None", "length": "60"}]})
        bowl, _ = fill_form("countertop", {"header": {"name": "Sample (made up)"},
                                           "blocks": [{"qty": "1", "sink": "Single Bowl", "length": "60"}]})
        blank, _ = fill_form("countertop", {"header": {"name": "Sample (made up)"}, "blocks": []})

    def names(pdf):
        return {w.field_name for w in pymupdf.open(stream=pdf, filetype="pdf")[0].widgets()}
    assert "L0.sink_center" not in names(none) and "L0.sink_center_r" not in names(none)
    assert "L0.sink_center" in names(bowl) and "L0.sink_center" in names(blank)   # blank paper form keeps it


def test_door_style_on_cabinet_order_and_drawing(app, c):
    from orderapp import cabinet_layout as C
    html = c.get(f"/orders/editor?form=cabinet_prefab&customer={app.cid}").get_data(as_text=True)
    assert 'id="co_style"' in html and "Shaker Slim" in html
    d = C.order_starter()
    counts = {}
    for st in ("", "Shaker", "Shaker Slim", "Bradford", "Italia", "Elegante", "Elegante Gauntlet Supermatte"):
        counts[st] = len(C.wall_prims(d["walls"][0], 600, 300, st))
    assert counts["Shaker"] > counts[""] and counts["Bradford"] > counts["Shaker"]      # panel + grooves
    assert counts["Elegante Gauntlet Supermatte"] == counts[""]                            # plain slab
    d["cab"]["style"] = "Shaker Slim"
    assert all(x["style"] == "Shaker Slim" for x in C.order_lines(d, "cabinet_prefab"))
    t = _text(C.make_pdf({"data": d, "name": "Kitchen"}, None, order={"header": {"name": "S (made up)"}}))
    assert "Door style: Shaker Slim" in t


def test_prehung_jamb_swing_boxes_bore_size(app):
    import pymupdf
    from orderapp.catalog import FORMS
    from orderapp.pdf_fill import fill_form, validate_catalog
    assert validate_catalog() == []
    ph = {f["key"]: f for f in FORMS["prehung"]["blocks"][0]}
    assert {"jamb_thick", "bore_size"} <= set(ph) and "door_edge" not in ph
    assert "bore_size" in {f["key"] for f in FORMS["door"]["blocks"][0]}
    with app.app_context():
        pdf, _ = fill_form("prehung", {"header": {"name": "Sample (made up)"}, "blocks": [
            {"qty": "1", "jamb": "Kerfed", "swing_dir": "Swing Out", "jamb_thick": "2 1/4", "bore_size": "2 1/8",
             "door_edge": "Full Book (square edge)"}]})
    page = pymupdf.open(stream=pdf, filetype="pdf")[0]
    on = {w.field_name for w in page.widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_CHECKBOX
          and w.field_value not in (False, "Off", "")}
    assert {"JambKerfed.0", "SwingOut.0"} <= on and not {"JambFlat.0", "SwingIn.0"} & on
    vals = {w.field_name: w.field_value for w in page.widgets()}
    assert vals["JambThick.0"] == "2 1/4" and vals["BoreSize.0"] == "2 1/8"
    assert "Door Edge" not in " ".join(str(v) for k, v in vals.items() if k.startswith("Comments"))


def test_no_autofill_and_no_auto_prefill(app, c):
    from orderapp.catalog import FORMS
    from orderapp.suggest import suggestions
    html = c.get(f"/orders/editor?form=door&customer={app.cid}").get_data(as_text=True)
    assert "setAttribute('autocomplete', 'off')" in html          # browser autofill off on every box
    assert "SUGG.defaults" not in html                             # nothing auto-picked while typing
    with app.app_context():
        s = suggestions("door", app.cid)["suggest"]
    meas = [f["key"] for f in FORMS["door"]["blocks"][0] if f.get("measure")]
    assert meas and not set(meas) & set(s) and "unit" not in s     # no pick lists for measurements / unit


def test_standard_depths_prefill_by_room(app, c):
    from orderapp import cabinet_layout as C
    from orderapp.catalog import public_spec
    for fk in ("countertop", "cabinet_prefab"):
        dep = next(f for f in public_spec(fk)["fields"] if f["key"] == "depth")
        assert dep["room_default"]["values"]["Kitchen"] == "25-1/2"
        assert dep["room_default"]["values"]["Bathroom Vanity"] == "22-1/2"
    html = c.get("/orders/editor?form=countertop").get_data(as_text=True)
    assert "function roomDepth" in html
    d = C.order_starter()
    assert d["walls"][0]["lowers"][0]["d"] == "25-1/2"
    d["room"] = "Bath vanity"
    assert C.order_lines(d, "cabinet_prefab")[0]["cab_room"] == "Bathroom Vanity"
    html = c.get(f"/orders/editor?form=cabinet_prefab&customer={app.cid}").get_data(as_text=True)
    assert 'id="co_room"' in html and "'Bath vanity': '22-1/2'" in html


def test_tracking_ordered_and_sent_dates(app, c):
    import json
    payload = {"header": {"name": "Sample Gardens (made up)", "po": "TRK-1", "date": "01/02/2026"}, "blocks": [{"qty": "1"}]}
    r = c.post("/orders/save", data={"csrf_token": c.t, "form_key": "door", "customer_id": app.cid,
                                     "payload": json.dumps(payload), "action": "save"})
    oid = int(r.headers["Location"].rsplit("/", 1)[1])
    page = c.get("/tracking?status=all").get_data(as_text=True)
    assert "Vendor #" not in page and ">Sent</th>" in page and "not sent" in page
    with app.app_context():
        from orderapp.db import x
        x("UPDATE orders SET created='2026-10-20 09:00:00', sent_at='2026-10-23 14:05:00' WHERE id=?", (oid,))
    page = c.get("/tracking?status=all").get_data(as_text=True)
    row = page[page.index(f"#{oid}</a>"):][:900]
    assert "10/20/2026" in row and "10/23/2026" in row and "01/02/2026" not in row
