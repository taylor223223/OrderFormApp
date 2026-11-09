"""v2.31.0: bulk download / compile orders, blind settings once per order, no edge bevel on the PDF, width first,
Unit # in its own box, no hinge locations on the Pre-Hung form, no prices on reorder requests, faux wood smooth /
embossed, notes for a future day, no automatic CRM follow-ups, Routes tab second. All data is made up."""
import io
import os
import re
import tempfile
import zipfile
from datetime import date, timedelta

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402

TODAY = date.today().isoformat()
FRIDAY = (date.today() + timedelta(days=2)).isoformat()


@pytest.fixture(scope="module")
def app():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)
    with a.app_context():
        from orderapp.db import dumps, x
        a.cid = x("INSERT INTO customers(name, address, city, state, zip, status) VALUES "
                  "('Sample Palms (made up)', '1 Test Rd', 'Phoenix', 'AZ', '85020', 'Active')")
        a.oids = [x("INSERT INTO orders(customer_id, form_key, title, po, status, data, created, updated, kind) "
                    "VALUES (?, ?, ?, ?, 'Draft', ?, '2026-11-05', '2026-11-05', 'order')",
                    (a.cid, fk, t, po, dumps({"header": {"name": "Sample Palms (made up)", "po": po},
                                              "blocks": [b]})))
                  for fk, t, po, b in (("door", "Door Order Form", "D-1", {"qty": "1", "width": "30", "height": "80"}),
                                       ("window_screen", "Window Screen Order Form", "W-1",
                                        {"qty": "2", "width": "30", "height": "40"}))]
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
    import pymupdf as fitz
    return "\n".join(p.get_text() for p in fitz.open(stream=pdf, filetype="pdf"))


def _vals(pdf):
    import pymupdf as fitz
    d = fitz.open(stream=pdf, filetype="pdf")
    pages = list(d)
    return {w.field_name: w.field_value for p in pages for w in list(p.widgets() or [])}


# ---------------------------------------------------------------- orders: download each / compile into one
def test_download_checked_orders_as_separate_pdfs(app, c):
    trk = c.get("/tracking").get_data(as_text=True)
    assert 'id="pkgAll"' in trk and "Download checked" in trk and "Compile into one PDF" in trk
    r = c.post("/orders/download", data={"csrf_token": c.t, "ids": [str(i) for i in app.oids]})
    assert r.status_code == 200 and r.mimetype == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.data))
    names = z.namelist()
    assert len(names) == 2 and all(n.lower().endswith(".pdf") for n in names)
    assert all(z.read(n)[:4] == b"%PDF" for n in names)
    r = c.post("/orders/download", data={"csrf_token": c.t})
    assert r.status_code == 302          # nothing checked


# ---------------------------------------------------------------- blinds: settings once per order
def test_blind_forms_have_order_settings():
    from orderapp.catalog import public_spec
    h = public_spec("horizontal_blind")
    assert h["shared"] == ["style", "slat_size", "slat_finish", "color"]
    assert "width" not in h["shared"] and "height" not in h["shared"] and "room" not in h["shared"]
    assert {"mount", "color", "slat_width"} <= set(public_spec("vertical_blind")["shared"])
    assert {"shade_fabric", "color", "bracket_size"} <= set(public_spec("roller_shade")["shared"])
    assert public_spec("door")["shared"] == []


def test_editor_has_order_settings_card(app, c):
    html = c.get(f"/orders/editor?form=horizontal_blind&customer={app.cid}").get_data(as_text=True)
    assert 'id="orderDefaults"' in html and "Same for every window" in html and "Different for this window" in html
    assert "fullBlock" in html and "defaults: SHARED.size ? DEF : undefined" in html


def test_saved_blind_order_keeps_settings(app, c):
    import json
    blocks = [{"qty": "1", "unit": "101", "room": "Kitchen", "width": "35", "height": "60", "style": "Faux Wood",
               "slat_size": '2"', "slat_finish": "Embossed", "color": "White"},
              {"qty": "1", "unit": "102", "room": "Living Room", "width": "70", "height": "60", "style": "Faux Wood",
               "slat_size": '2"', "slat_finish": "Smooth", "color": "White"}]     # this window differs
    payload = {"header": {"name": "Sample Palms (made up)", "po": "H-1"}, "blocks": blocks,
               "defaults": {"style": "Faux Wood", "slat_size": '2"', "slat_finish": "Embossed", "color": "White"}}
    r = c.post("/orders/save", data={"csrf_token": c.t, "form_key": "horizontal_blind", "customer_id": app.cid,
                                     "payload": json.dumps(payload), "action": "save"})
    assert r.status_code == 302
    with app.app_context():
        from orderapp.db import loads, q
        o = q("SELECT * FROM orders WHERE po='H-1'", one=True)
        d = loads(o["data"])
    assert d["defaults"]["style"] == "Faux Wood" and d["blocks"][1]["slat_finish"] == "Smooth"
    from orderapp.pdf_fill import fill_form
    v = _vals(fill_form("horizontal_blind", d)[0])
    assert v["Blind 1 Slat Embossed"] in ("Yes", True) and v["Blind 2 Slat Smooth"] in ("Yes", True)
    assert v["Blind 1 Style Faux Wood"] in ("Yes", True) and v["Blind 1 Unit #"] == "101"


def test_faux_wood_slats_only_for_faux_wood():
    from orderapp.catalog import FORMS
    f = next(f for f in FORMS["horizontal_blind"]["blocks"][0] if f["key"] == "slat_finish")
    assert [o[0] for o in f["options"]] == ["Smooth", "Embossed"] and f["show_if"] == ("style", ["Faux Wood"])
    from orderapp.pdf_fill import fill_form
    v = _vals(fill_form("horizontal_blind", {"blocks": [{"qty": "1", "style": "Basswood", "slat_finish": "Embossed"}]})[0])
    assert v.get("Blind 1 Slat Embossed") in (False, "Off", "", None)


# ---------------------------------------------------------------- door edge: bevel is assumed
def test_beveled_edge_left_off_the_pdf():
    from orderapp.door_edge import BEVELED, FULL_BOOK
    from orderapp.pdf_fill import fill_form
    t = _text(fill_form("door", {"blocks": [{"qty": "1", "width": "29-3/4", "height": "79-1/2",
                                             "door_edge": BEVELED}]})[0])
    assert "Bevel" not in t
    t = _text(fill_form("door", {"blocks": [{"qty": "1", "width": "30", "height": "80", "door_edge": FULL_BOOK}]})[0])
    assert "Full Book" in t


# ---------------------------------------------------------------- width first, everywhere
def test_width_always_first():
    from orderapp.catalog import FORMS, GENERIC_FIELDS, PRODUCTS
    def check(name, fields):
        keys = [f["key"] for f in fields]
        for pre in {k.rsplit("_", 1)[0] + "_" if "_" in k else "" for k in keys}:
            if pre + "width" in keys:
                for other in ("height", "length", "depth"):
                    if pre + other in keys:
                        assert keys.index(pre + "width") < keys.index(pre + other), (name, pre, other)
    for fk, spec in FORMS.items():
        for b in spec["blocks"]:
            check(fk, b)
    for pk, p in PRODUCTS.items():
        check(pk, p.get("fields") or [])
    check("generic", GENERIC_FIELDS)
    keys = [f["key"] for f in FORMS["roller_shade"]["blocks"][0]]
    assert keys.index("width") < keys.index("length")            # roller shade: width, then length (= height)


def test_roller_shade_pdf_width_before_length():
    import pymupdf as fitz
    from orderapp.pdf_fill import template_path
    words = fitz.open(template_path("roller_shade"))[0].get_text("words")
    w = min(x[0] for x in words if x[4] == "Width" and x[1] < 400)
    ln = min(x[0] for x in words if x[4] == "Length" and x[1] < 400)
    assert w < ln


# ---------------------------------------------------------------- unit # in its own box
@pytest.mark.parametrize("fk", ["door", "prehung", "new_door", "bypass", "screen_door", "window_screen",
                                "vertical_blind", "vertical_blind_2", "trim", "door_hardware", "countertop"])
def test_unit_box_at_the_top(fk):
    from orderapp.pdf_fill import fill_form
    pdf, _ = fill_form(fk, {"header": {"name": "Sample (made up)", "po": "P-9", "date": "11/05/2026"},
                            "blocks": [{"qty": "1", "unit": "1043", "width": "30", "height": "80"}]})
    v = _vals(pdf)
    assert v.get("Unit#") == "1043", fk
    assert "Unit 1043" not in _text(pdf)                         # not in the comments any more


@pytest.mark.parametrize("fk", ["bypass", "screen_door", "window_screen", "vertical_blind", "vertical_blind_2"])
def test_unit_per_item_when_several_units(fk):
    from orderapp.pdf_fill import fill_form
    pdf, _ = fill_form(fk, {"header": {"name": "Sample (made up)"},
                            "blocks": [{"qty": "1", "unit": "101"}, {"qty": "1", "unit": "205"}]})
    v = _vals(pdf)
    assert v.get("Unit#.0") == "101" and v.get("Unit#.1") == "205" and not v.get("Unit#"), fk


def test_filled_form_reads_unit_back():
    from orderapp.pdf_fill import fill_form, read_filled_form
    fk, d = read_filled_form(fill_form("door", {"header": {"name": "X"}, "blocks": [{"qty": "1", "unit": "77"}]})[0])
    assert fk == "door" and d["blocks"][0]["unit"] == "77"


# ---------------------------------------------------------------- pre-hung: no hinge locations
def test_prehung_has_no_hinge_locations():
    import pymupdf as fitz
    from orderapp.catalog import FORMS
    from orderapp.pdf_fill import template_path
    keys = {f["key"] for f in FORMS["prehung"]["blocks"][0]}
    assert not {"hinge1", "hinge2", "hinge3", "hinge4"} & keys
    assert {"hinge_dim", "hinge_color", "self_closing"} <= keys
    pg = fitz.open(template_path("prehung"))[0]
    assert not any(w.field_name.startswith("Hinge") and w.field_name[5:6].isdigit() for w in pg.widgets())
    assert "Hinge 1" not in pg.get_text()
    assert "hinge1" in {f["key"] for f in FORMS["door"]["blocks"][0]}         # the Door form keeps them


# ---------------------------------------------------------------- reorder requests: no prices
def test_reorder_request_has_no_prices(app):
    from orderapp import reorder as RO
    data = {"header": {"name": "Sample Palms (made up)", "po": "R-1"},
            "lines": [{"qty": "2", "item_no": "900100", "desc": "Sample door", "size": '30" x 80"',
                       "invoice": "T-1", "last_date": "2026-02-03", "last_price": "123.45"}]}
    t = _text(RO.render({"id": 1}, data, "Sample Palms (made up)"))
    assert "Sample door" in t and "123.45" not in t and "Last $" not in t and "$" not in t


# ---------------------------------------------------------------- notes for a future day
def test_notes_for_a_future_day(app, c):
    r = c.post(f"/routes/day/{FRIDAY}/note", data={"csrf_token": c.t, "note": "Gate code 4321 - bring samples",
                                                   "next": "/plan"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/plan")
    assert "Gate code 4321" in c.get(f"/routes/day/{FRIDAY}").get_data(as_text=True)
    assert "Gate code 4321" in c.get(f"/plan?view=week&start={FRIDAY}").get_data(as_text=True)   # that day's week
    assert "Notes for today" not in c.get("/").get_data(as_text=True)          # not today's
    c.post(f"/routes/day/{TODAY}/note", data={"csrf_token": c.t, "note": "Maintenance walk at 10"})
    dash = c.get("/").get_data(as_text=True)
    assert "Notes for today" in dash and "Maintenance walk at 10" in dash
    r = c.post(f"/routes/day/{TODAY}/note", data={"csrf_token": c.t, "note": "x", "next": "https://evil.example"})
    assert "evil" not in r.headers["Location"]


# ---------------------------------------------------------------- CRM: only follow-ups Taylor sets
def test_no_automatic_followups(app, c):
    c.post("/crm/log", data={"csrf_token": c.t, "customer_id": app.cid, "kind": "Call",
                             "notes": "Talked about screens", "next": "/"})
    with app.app_context():
        from orderapp.db import q
        assert q("SELECT COUNT(*) n FROM tasks", one=True)["n"] == 0
    c.post("/crm/log", data={"csrf_token": c.t, "customer_id": app.cid, "kind": "Call", "notes": "Wants a quote",
                             "follow_up": TODAY, "follow_up_title": "Send screen quote", "next": "/"})
    with app.app_context():
        from orderapp.db import q
        assert [r["title"] for r in q("SELECT title FROM tasks")] == ["Send screen quote"]
    crm = c.get("/crm").get_data(as_text=True)
    assert "Send screen quote" in crm and "30+ days" not in crm


# ---------------------------------------------------------------- Routes tab right after Dashboard
def test_routes_tab_second(c):
    nav = c.get("/").get_data(as_text=True).split("<nav>")[1].split("</nav>")[0]
    labels = re.findall(r"</span>([^<]+)", nav)
    assert labels[:2] == ["Dashboard", "Routes"], labels[:4]


def test_route_day_calendar_goes_to_the_picked_day(c):
    html = c.get(f"/routes/day/{TODAY}").get_data(as_text=True)
    assert 'class="goday"' in html and f'data-url="/routes/day/DAY"' in html
    assert 'onchange="location.href="' not in html            # the old handler broke on its own quote marks


def test_note_box_has_its_own_date(app, c):
    """2.34.7: the notes box on Routes and the Dashboard can write any day's note without leaving the page."""
    r = c.post("/routes/note", data={"csrf_token": c.t, "day": FRIDAY, "note": "Bring the Avalon sample",
                                     "next": f"/routes/day/{TODAY}"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/routes/day/{TODAY}")   # stayed on today
    j = c.get(f"/routes/day/{FRIDAY}/note.json").get_json()
    assert j["note"] == "Bring the Avalon sample" and j["day"] == FRIDAY
    day_page = c.get(f"/routes/day/{TODAY}").get_data(as_text=True)
    assert 'data-nb-day' in day_page and 'type="date" name="day"' in day_page
    assert 'data-nb-day' in c.get("/").get_data(as_text=True)
