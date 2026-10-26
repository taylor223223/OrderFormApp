"""v2.25: box-by-box cabinet & countertop layout (uppers / lowers, sink base, drawers, appliance spaces),
drawn with dimensions; attached to cabinet / countertop orders; customer layout form. Made-up data."""
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
        a.cid = x("INSERT INTO customers(name, city, state) VALUES ('Sample Gardens (made up)', 'Phoenix', 'AZ')")
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


def _kitchen():
    from orderapp import cabinet_layout as C
    d = C.starter()
    L = lambda t, **k: dict(C.new_box("lowers", t), **k)   # noqa: E731
    U = lambda t, **k: dict(C.new_box("uppers", t), **k)   # noqa: E731
    d["walls"][0]["lowers"] = [L("Base cabinet", w="24"), L("Dishwasher space"), L("Sink base", w="36"),
                               L("Base cabinet", w="24"), L("Stove / range space"), L("Drawer base", w="18")]
    d["walls"][0]["uppers"] = [U("Wall cabinet", w="24"), U("Wall cabinet", w="24"), U("Window (no cabinet)", w="36"),
                               U("Wall cabinet", w="24"), U("Over-stove cabinet", w="30"), U("Wall cabinet", w="18")]
    return d


def test_countertop_and_schedule():
    from orderapp import cabinet_layout as C
    d = _kitchen()
    pieces = C.countertop(d)
    assert [(p["boxes"], p["length"], p["depth"]) for p in pieces] == [("L1-L4", 108.0, 25.0), ("L6", 18.0, 25.0)]
    assert "L3 center 66\"" in pieces[0]["sink"]          # sink base 48-84 -> center 66 from the left end
    rows = C.schedule(d)
    assert rows[0][:6] == ["U1", "Wall 1", "Upper", "Wall cabinet", '24"', '30"']
    l6 = next(r for r in rows if r[0] == "L6")
    assert l6[3] == "Drawer base" and l6[8] == "3"
    svg = C.wall_svg(d["walls"][0])
    assert svg.startswith("<svg") and "DISHWASHER" in svg and "SINK BASE" in svg and "OVER STOVE" in svg
    assert "18&quot;" in svg                                  # the shorter over-stove cabinet gets its own height


def test_order_lines():
    from orderapp import cabinet_layout as C
    d = _kitchen()
    cab = C.order_lines(d, "cabinet_custom")
    assert len(cab) == 9                                     # 5 upper + 4 lower cabinets (window, dishwasher, stove skipped)
    assert any(x["cab_kind"] == "Sink Base" for x in cab) and all("Dishwasher" not in x["line_comments"] for x in cab)
    tops = C.order_lines(d, "countertop")
    assert len(tops) == 2 and tops[0]["length"] == "108" and tops[0]["sink"] == "Single Bowl"
    assert tops[0]["ct_material"] == "Peel & Stick Laminate"


def test_layout_routes_and_order_pdf(app, c):
    r = c.post("/kitchen/new", data={"csrf_token": c.t, "customer_id": app.cid, "name": "Kitchen A1", "unit": "104"})
    lid = int(r.headers["Location"].rsplit("/", 1)[1])
    html = c.get(f"/kitchen/{lid}").get_data(as_text=True)
    assert "Cabinet &amp; countertop layout" in html and "Uppers (wall cabinets)" in html
    j = c.post(f"/kitchen/{lid}", json={"data": _kitchen(), "name": "Kitchen A1", "unit": "104"},
               headers={"X-CSRF-Token": c.t}).get_json()
    assert j["ok"] and len(j["walls"]) == 1 and j["pieces"][0]["length_txt"] == '108"'
    p = c.post(f"/kitchen/{lid}/preview", json={"data": _kitchen()}, headers={"X-CSRF-Token": c.t}).get_json()
    assert p["walls"][0].startswith("<svg")
    pdf = c.get(f"/kitchen/{lid}/pdf")
    assert pdf.data[:4] == b"%PDF"
    import pymupdf as fitz
    doc = fitz.open(stream=pdf.data, filetype="pdf")
    assert len(doc) == 2 and "Wall 1" in doc[0].get_text() and "Countertop" in doc[1].get_text()
    assert c.get("/kitchen/blank.pdf").data[:4] == b"%PDF"
    lines = c.get(f"/kitchen/{lid}/lines?form=cabinet_prefab").get_json()["lines"]
    assert {x["cab_item"] for x in lines} == {"Base Cabinet", "Sink Base", "Drawer Base", "Wall Cabinet"}
    # attach to a cabinet order: the drawing pages go at the end of the order PDF
    ed = c.get(f"/orders/editor?form=cabinet_custom&customer={app.cid}").get_data(as_text=True)
    assert 'id="layoutSel"' in ed and "Kitchen A1" in ed
    payload = {"header": {"name": "Sample Gardens (made up)", "po": "L-1", "date": "10/25/2026"},
               "blocks": [{"qty": "1", "width": "24", "height": "30"}], "layout_id": str(lid)}
    r = c.post("/orders/save", data={"csrf_token": c.t, "form_key": "cabinet_custom", "customer_id": app.cid,
                                     "payload": json.dumps(payload), "action": "pdf"})
    oid = int(r.headers["Location"].rsplit("/", 1)[1])
    with app.app_context():
        from orderapp.db import q
        path = q("SELECT pdf_path FROM orders WHERE id=?", (oid,), one=True)["pdf_path"]
    out = fitz.open(path)
    # v2.28: a cabinet order gets the cabinets-only layout (the countertop has its own order form)
    assert len(out) >= 2 and "Cabinet Order - Kitchen Layout" in out[-1].get_text()


def test_customer_layout_form_in_library(c):
    html = c.get("/forms").get_data(as_text=True)
    assert "Kitchen Cabinet Layout" in html
