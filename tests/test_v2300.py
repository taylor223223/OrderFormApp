"""v2.30.0 (list 5): tall pantries take the upper spot; forms in one PDF; the CRM entry under each route stop;
roller shade bracket size; drawer box + cabinet door / drawer front forms; kitchen shape (straight, mirrored,
L, U); standard appliance widths; Discard; QUOTE in the title; bigger email box; status buttons in pairs.
All data is made up."""
import json
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402

DAY = "2030-06-04"
H = {"name": "Sample Palms (made up)", "po": "S-30", "date": "06/04/2030", "contact": "Pat Sample",
     "contact_phone": "(602) 555-0101", "contact_email": "maintenance.supervisor@samplepalmsapartments.example"}


@pytest.fixture(scope="module")
def app():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import set_setting, x
        set_setting("route_start_address", "")
        a.cid = x("INSERT INTO customers(name, address, city, state, zip, lat, lon, status) VALUES "
                  "('Sample Palms (made up)', '1 Test Rd', 'Phoenix', 'AZ', '85020', 33.6, -112.07, 'Active')")
        x("INSERT INTO contacts(customer_id, name, role) VALUES (?, 'Sam Super', 'Maint. Super')", (a.cid,))
        x("INSERT INTO contacts(customer_id, name, role) VALUES (?, 'Pat Manager', 'Property Manager')", (a.cid,))
        a.s1 = x("INSERT INTO route_stops(day, position, customer_id, label, address, lat, lon, status, notes) "
                 "VALUES (?, 0, ?, 'Sample Palms (made up)', '1 Test Rd', 33.6, -112.07, 'Planned', 'Gate 1234')",
                 (DAY, a.cid))
        a.s2 = x("INSERT INTO route_stops(day, position, customer_id, label, address, lat, lon, status, notes) "
                 "VALUES (?, 1, ?, 'Sample Palms (made up)', '1 Test Rd', 33.6, -112.07, 'Planned', '')",
                 (DAY, a.cid))
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


def test_tall_pantry_takes_the_upper_spot():
    from orderapp import cabinet_layout as C
    w = {"lowers": [C.new_box("lowers", "Tall / pantry"), C.new_box("lowers", "Base cabinet"),
                    C.new_box("lowers", "Tall / pantry")],
         "uppers": [C.new_box("uppers", "Wall cabinet") for _ in range(3)]}
    w["lowers"][2]["h"] = "60"                              # 68" or shorter: stays a lower, the spot stays open
    for u in w["uppers"]:
        u["w"] = "18"
    assert C.tall_spans(w) == [(0.0, 18.0)]
    assert C.upper_xs(w) == [18.0, 36.0, 54.0]              # the first upper moves past the 84" pantry
    d = C.order_starter()
    d["walls"] = [dict(w, name="Wall 1")]
    rows = C.schedule(d)
    assert any("takes the upper spot" in r[10] for r in rows if r[0] == "L1")
    assert not any("takes the upper spot" in r[10] for r in rows if r[0] == "L3")


def test_standard_appliance_widths():
    from orderapp import cabinet_layout as C
    assert C.new_box("lowers", "Dishwasher space")["w"] == "24"
    assert C.new_box("lowers", "Stove / range space")["w"] == "30"
    assert C.new_box("lowers", "Fridge space")["w"] == "33"
    root = os.path.join(os.path.dirname(__file__), "..", "orderapp", "templates")
    for fn in ("_cabinet_order_editor.html", "cabinet_layout.html"):     # switching a box to an appliance
        js = open(os.path.join(root, fn), encoding="utf-8").read()       # space takes the space's width
        assert "['cab', 'sink'].includes(kindOf(tr.dataset.row, nb))" in js, fn


def test_roller_shade_bracket_size(app):
    from orderapp.catalog import FORMS
    from orderapp.pdf_fill import fill_form, read_filled_form
    assert any(f["key"] == "bracket_size" and f["kind"] == "text" for f in FORMS["roller_shade"]["blocks"][0])
    with app.app_context():
        pdf, _ = fill_form("roller_shade", {"header": H, "blocks": [{"qty": "1", "bracket_size": '3"'}]})
    assert read_filled_form(pdf)[1]["blocks"][0]["bracket_size"] == '3"'


def test_new_drawer_and_door_front_forms(app):
    import pymupdf
    from orderapp.catalog import FORMS, PRODUCTS
    from orderapp.pdf_fill import fill_form
    assert FORMS["drawer_box"]["title"] == "Drawer Box Order Form"
    assert FORMS["door_front"]["title"] == "Cabinet Door & Drawer Front Order Form"
    keys = {f["key"] for f in FORMS["door_front"]["blocks"][0]}
    assert {"hinge_cut", "hinge_cut_dia", "hinge_top", "hinge_bottom", "df_item", "style"} <= keys   # hinge bores reused
    assert "door_front" in PRODUCTS["cabinet_door"]["forms"] and PRODUCTS["drawer_box"]["forms"] == ["drawer_box"]
    with app.app_context():
        pdf, w = fill_form("drawer_box", {"header": H, "blocks": [
            {"qty": "4", "width": "14-1/2", "depth": "18", "height": "4-1/2", "db_material": "Baltic Birch Plywood",
             "db_joint": "Dovetail", "db_slide": "Undermount", "db_slide_len": "18", "db_notch": "Yes"}]})
    t = pymupdf.open(stream=pdf, filetype="pdf")[0].get_text()
    assert "Drawer Box Order Form" in t and "14-1/2" in t and w == []


def test_bigger_email_box(app):
    import pymupdf
    from orderapp.catalog import FORMS
    from orderapp.pdf_fill import fill_form
    with app.app_context():
        for fk in FORMS:
            pdf, _ = fill_form(fk, {"header": H, "blocks": [{"qty": "1"}]})
            em = [w for w in pymupdf.open(stream=pdf, filetype="pdf")[0].widgets() if w.field_name == "Contact Email"]
            assert em and em[0].rect.width >= 190 and em[0].text_fontsize >= 7.5, (fk, em and em[0].rect)


def test_quote_title_says_quote(app):
    import pymupdf
    from orderapp.order_kind import quote_word, stamp_quote
    from orderapp.pdf_fill import fill_form
    assert quote_word("Door Order Form") == "Door Quote Form"
    assert quote_word("HORIZONTAL BLIND ORDER FORM") == "HORIZONTAL BLIND QUOTE FORM"
    assert quote_word("PRE-HUNG DOOR FORM") == "PRE-HUNG DOOR QUOTE FORM"
    with app.app_context():
        for fk, title in (("horizontal_blind", "Horizontal Blind Order Form"), ("countertop", "Countertop Order Form")):
            pdf, _ = fill_form(fk, {"header": H, "blocks": [{"qty": "1"}]})
            t = pymupdf.open(stream=stamp_quote(pdf, title), filetype="pdf")[0].get_text()
            assert "QUOTE FORM" in t.upper() and "ORDER FORM" not in t.upper(), fk


def test_kitchen_shape_countertop(app, c):
    import pymupdf
    from orderapp.catalog import FORMS
    from orderapp.pdf_fill import fill_form, read_filled_form
    f = {x["key"]: x for x in FORMS["countertop"]["blocks"][0]}
    assert [o[0] for o in f["ct_shape"]["options"]] == ["Straight", "Mirrored", "L-Shape", "U-Shape"]
    assert f["length3"]["show_if"] == ("ct_shape", ["U-Shape"])
    blk = {"qty": "1", "ct_shape": "L-Shape", "length": "120", "length2": "84", "depth": "25-1/2", "overhang": "1",
           "sink": "Single Bowl", "sink_center": "60", "bs_back": "Yes"}
    with app.app_context():
        pdf, _ = fill_form("countertop", {"header": H, "blocks": [blk]})
    t = pymupdf.open(stream=pdf, filetype="pdf")[0].get_text()
    assert "Left wall length" in t and "Kitchen shape: L-Shape" in t and "84" in t
    assert read_filled_form(pdf)[1]["blocks"][0]["length"] == "120"       # still reads back
    r = c.post("/api/countertop-shape", json={"ct_shape": "U-Shape", "length": "120"},
               headers={"X-CSRF-Token": c.t}).get_json()
    assert r["svg"].startswith("<svg") and "Right wall length" in r["svg"]
    assert 'ctshape' in c.get("/orders/editor?form=countertop").get_data(as_text=True)


def test_kitchen_shape_cabinet_layout():
    import pymupdf
    from orderapp import cabinet_layout as C
    d = C.order_starter()
    d["shape"] = "U-Shape"
    d["walls"] = [{"name": n, "uppers": [], "lowers": [C.new_box("lowers", "Base cabinet")]}
                  for n in ("Back wall", "Left wall", "Right wall")]
    pdf = C.make_pdf(d, order={"header": H})
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    assert len(doc) == 4 and "Kitchen shape: U-Shape" in doc[0].get_text()
    assert C.normalize({"v": 2, "walls": []})["shape"] == "Straight"


def test_discard_and_quotes_tab_and_tap_pairs(app, c):
    ed = c.get("/orders/editor?form=door").get_data(as_text=True)
    assert 'id="discardBtn"' in ed and "Are you sure?" in ed
    nav = c.get("/").get_data(as_text=True)
    i_trk, i_quotes, i_forms = nav.index("Order Tracking</a>"), nav.index("Quotes</a>"), nav.index("Order Forms</a>")
    assert i_trk < i_quotes < i_forms                                   # Quotes sits right after Order Tracking
    from orderapp.order_kind import TAP_GRID
    assert TAP_GRID == [["Order ready", "Order sent", "Delivered"], ["Quote ready", "Quote sent", None]]


def _save(c, cid, form, blocks, kind="order"):
    r = _post(c, "/orders/save", {"form_key": form, "customer_id": cid, "kind": kind, "action": "save",
                                  "payload": json.dumps({"header": H, "blocks": blocks})})
    return int(r.headers["Location"].rsplit("/", 1)[1])


def test_forms_in_one_pdf(app, c):
    import pymupdf
    a = _save(c, app.cid, "countertop", [{"qty": "1", "length": "96", "depth": "25-1/2"}])
    b = _save(c, app.cid, "door", [{"qty": "2", "width": "30", "height": "80"}])
    q_ = _save(c, app.cid, "drawer_box", [{"qty": "4", "width": "14"}], kind="quote")
    trk = c.get("/tracking?status=all").get_data(as_text=True)
    assert 'form="pkgSel"' in trk and "Put the checked forms in one PDF" in trk and "Make it an order" in trk
    r = _post(c, "/orders/package", {"make": "1", "ids": [str(a), str(b), str(q_)]})
    html = r.get_data(as_text=True)
    assert "Made:" in html and "Order package (3 forms)" in html
    pdf = c.get(f"/orders/package/file?ids={a},{b},{q_}").data
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    cover = doc[0].get_text()
    assert "ORDER PACKAGE" in cover and "Countertop Order Form" in cover and "Quote" in cover
    assert len(doc) >= 4 and len(doc.get_toc()) == 3
    assert "Drawer Box Quote Form" in pymupdf.open(stream=pdf, filetype="pdf")[len(doc) - 1].get_text()
    view = c.get(f"/orders/{a}").get_data(as_text=True)
    assert "With other forms in one PDF" in view


def test_route_crm_entry_under_each_stop(app, c):
    html = c.get(f"/routes/day/{DAY}").get_data(as_text=True)
    assert "CRM entry" in html and 'name="crm_how"' in html and "Sam Super" in html and "Gate 1234" in html
    assert "Visited · log it" in html
    # save a note without visiting
    _post(c, f"/routes/stop/{app.s2}", {"act": "Done", "act2": "crmnote", "notes": "Ask for Sam"})
    with app.app_context():
        from orderapp.db import q
        s2 = q("SELECT * FROM route_stops WHERE id=?", (app.s2,), one=True)
        assert s2["status"] == "Planned" and s2["notes"] == "Ask for Sam"
    # Visited: one CRM entry with how / who / notes
    _post(c, f"/routes/stop/{app.s1}", {"act": "Done", "crm_how": "Call", "crm_who": "Pat Manager",
                                        "notes": "Gate 1234 - talked turns", "ordered": "No"})
    with app.app_context():
        from orderapp.db import q
        s1 = q("SELECT * FROM route_stops WHERE id=?", (app.s1,), one=True)
        a = q("SELECT * FROM activities WHERE id=?", (s1["activity_id"],), one=True)
    assert s1["status"] == "Done" and a["kind"] == "Call" and a["contact_name"] == "Pat Manager"
    assert "talked turns" in a["notes"] and a["ordered"] == "No"
    # change it afterwards from the route
    _post(c, f"/routes/stop/{app.s1}", {"act": "crm", "crm_how": "In person", "crm_who": "Sam Super",
                                        "notes": "Met Sam on site", "ordered": "Yes", "order_items": ["Doors"]})
    with app.app_context():
        from orderapp.db import q
        a = q("SELECT * FROM activities WHERE id=?", (s1["activity_id"],), one=True)
    assert (a["kind"], a["contact_name"], a["notes"], a["ordered"], a["order_what"]) == \
        ("Site visit", "Sam Super", "Met Sam on site", "Yes", "Doors")


def test_old_stop_notes_carried_into_crm(app):
    with app.app_context():
        from orderapp.db import q, x
        from orderapp.routes.routes_plan import carry_notes
        aid = x("INSERT INTO activities(customer_id, kind, occurred_at, notes) VALUES (?, 'Site visit', ?, 'Visited')",
                (app.cid, "2030-06-01 09:00"))
        x("INSERT INTO route_stops(day, customer_id, label, status, notes, activity_id) "
          "VALUES ('2030-06-01', ?, 'x', 'Done', 'Unit 104 vacant', ?)", (app.cid, aid))
        carry_notes("2030-06-01")
        carry_notes("2030-06-01")       # only once
        assert q("SELECT notes FROM activities WHERE id=?", (aid,), one=True)["notes"] == "Visited\nUnit 104 vacant"
