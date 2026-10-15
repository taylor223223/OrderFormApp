"""v2.14: people + positions saved to property profiles from the CRM; photo reference pages on order PDFs.
All names and data are made up."""
import io
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
    a = create_app(db_path=os.path.join(d, "p.db"), testing=True)
    with a.app_context():
        from orderapp.db import set_setting, x
        set_setting("output_dir", os.path.join(d, "out"))
        a.cid = x("INSERT INTO customers(name, address, city, state, zip, status) VALUES "
                  "('Cactus Bloom Apartments', '1 Test Rd', 'Phoenix', 'AZ', '85001', 'Active')")
        a.cid2 = x("INSERT INTO customers(name, status) VALUES ('Mesa Ridge Homes', 'Active')")
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


def _people(app, cid):
    with app.app_context():
        from orderapp.db import q
        return {r["name"]: r["role"] for r in q("SELECT name, role FROM contacts WHERE customer_id=?", (cid,))}


def test_clean_role_and_matching(app):
    from orderapp.people import clean_role
    assert clean_role("maint super") == "Maint. Super"
    assert clean_role("Maintenance Supervisor") == "Maint. Super"
    assert clean_role("PM") == "Property Manager"
    assert clean_role("leasing") == "Office"
    assert clean_role("Regional VP") == "Regional VP"
    with app.app_context():
        from orderapp.people import save_person
        a = save_person(app.cid2, "Gus", "maintenance")
        b = save_person(app.cid2, "gus", "Maint. Super")           # same person, position updated
        cc = save_person(app.cid2, "Gus Larsen", "")               # fuller name fills in, no duplicate
        assert a == b == cc
    assert _people(app, app.cid2) == {"Gus Larsen": "Maint. Super"}


def test_log_people_with_positions(app, c):
    page = c.get("/crm/contacts").get_data(as_text=True)
    assert 'id="peopleRows"' in page and "Maint. Super" in page and "Property Manager" in page
    r = c.post("/crm/contacts/add", data={
        "csrf_token": c.t, "customer_id": app.cid, "kind": "In person", "temp": "Warm",
        "person_name": ["Dana Fry", "Lou", ""], "person_role": ["Maint. Super", "Office", ""],
        "person_phone": ["602-555-0101", "", ""], "subject": "Door check"})
    assert r.status_code == 302
    assert _people(app, app.cid) == {"Dana Fry": "Maint. Super", "Lou": "Office"}
    with app.app_context():
        from orderapp.db import q
        acts = q("SELECT contact_name, contact_id FROM activities WHERE customer_id=? ORDER BY id", (app.cid,))
        assert [a["contact_name"] for a in acts] == ["Dana Fry", "Lou"] and all(a["contact_id"] for a in acts)
        assert q("SELECT phone FROM contacts WHERE name='Dana Fry'", one=True)["phone"] == "602-555-0101"
    # suggestions for that property
    j = c.get(f"/crm/people/{app.cid}").get_json()
    assert {p["name"]: p["role"] for p in j["people"]} == {"Dana Fry": "Maint. Super", "Lou": "Office"}
    # shows on the profile with the position
    prof = c.get(f"/customers/{app.cid}").get_data(as_text=True)
    assert "Dana Fry" in prof and "Maint. Super" in prof and "last talked" in prof
    # log page shows the position column
    log = c.get("/crm/contacts?from=2000-01-01&to=2100-01-01").get_data(as_text=True)
    assert "<th>Position</th>" in log and 'value="Maint. Super"' in log


def test_inline_edit_saves_person(app, c):
    with app.app_context():
        from orderapp.db import x
        aid = x("INSERT INTO activities(customer_id, kind, occurred_at, created) VALUES (?, 'Call', '2030-01-02 10:00', 'x')",
                (app.cid,))
    r = c.post(f"/crm/contacts/{aid}", data={"csrf_token": c.t, "contact_name": "Rita Vance", "role": "pm",
                                              "kind": "Call", "date": "2030-01-02"},
               headers={"X-Requested-With": "fetch"})
    assert r.get_json()["ok"]
    assert _people(app, app.cid)["Rita Vance"] == "Property Manager"


def test_quick_log_saves_position(app, c):
    r = c.post("/crm/log", data={"csrf_token": c.t, "customer_id": app.cid, "kind": "Call",
                                 "contact_name": "Lou", "contact_role": "Office", "save_contact": "1"})
    assert r.status_code == 302
    assert list(_people(app, app.cid)).count("Lou") == 1


def test_old_names_backfilled(app, c):
    with app.app_context():
        from orderapp.db import x
        x("INSERT INTO activities(customer_id, kind, occurred_at, contact_name, created) "
          "VALUES (?, 'Text', '2030-01-03 10:00', 'Theo', 'x')", (app.cid2,))
    c.get("/crm/contacts")
    assert "Theo" in _people(app, app.cid2)


def _jpeg(w, h, color=(200, 80, 40)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG", quality=95)
    return b.getvalue()


def test_photo_kept_full_quality():
    from orderapp.photos import shrink
    raw = _jpeg(1600, 1200)
    out, ext = shrink(raw)
    assert out == raw and ext == ".jpg"                # already fine -> stored exactly as taken
    big, _ = shrink(_jpeg(5000, 3000))
    from PIL import Image
    assert max(Image.open(io.BytesIO(big)).size) == 3200


def test_photos_on_order_pdf(app, c):
    payload = {"header": {"name": "Cactus Bloom Apartments", "po": "PO-77", "date": "10/15/2030"},
               "blocks": [{"qty": "1", "unit": "101"}], "comments": ""}
    r = c.post("/orders/save", data={"csrf_token": c.t, "form_key": "door", "customer_id": app.cid,
                                     "payload": json.dumps(payload), "action": "pdf",
                                     "photo_caption": "Unit 101 hall closet",
                                     "photos": [(io.BytesIO(_jpeg(1600, 1200)), "wide.jpg"),
                                                (io.BytesIO(_jpeg(900, 1600, (40, 90, 200))), "tall.jpg")]},
               content_type="multipart/form-data")
    assert r.status_code == 302
    oid = int(r.headers["Location"].rsplit("/", 1)[1])
    with app.app_context():
        from orderapp.db import q
        o = q("SELECT pdf_path FROM orders WHERE id=?", (oid,), one=True)
        assert q("SELECT COUNT(*) n FROM order_photos WHERE order_id=?", (oid,), one=True)["n"] == 2
    import pymupdf as fitz
    from orderapp.catalog import FORMS  # noqa: F401
    doc = fitz.open(o["pdf_path"])
    texts = [p.get_text() for p in doc]
    photo_pages = [i for i, t in enumerate(texts) if "PHOTO REFERENCE" in t]
    assert len(photo_pages) == 2 and photo_pages[-1] == doc.page_count - 1
    first = doc[photo_pages[0]]
    assert first.rect.width > first.rect.height                    # wide photo -> sideways page
    assert doc[photo_pages[1]].rect.width < doc[photo_pages[1]].rect.height
    assert "Unit 101 hall closet" in texts[photo_pages[0]] and "Photo 1 of 2" in texts[photo_pages[0]]
    img = first.get_images(full=True)[0]
    assert (img[2], img[3]) == (1600, 1200)                         # embedded at full size
    doc.close()
    # removing a photo re-makes the PDF
    with app.app_context():
        from orderapp.db import q
        pid = q("SELECT id FROM order_photos WHERE order_id=? ORDER BY id LIMIT 1", (oid,), one=True)["id"]
    c.post(f"/photos/{pid}/delete", data={"csrf_token": c.t})
    doc = fitz.open(o["pdf_path"])
    assert sum("PHOTO REFERENCE" in p.get_text() for p in doc) == 1
    doc.close()
    # editor shows the photo card + existing photos; send page says they're in the PDF
    ed = c.get(f"/orders/editor?order={oid}").get_data(as_text=True)
    assert "Photo reference" in ed and 'enctype="multipart/form-data"' in ed and "/photos/" in ed
    snd = c.get(f"/orders/{oid}/send").get_data(as_text=True)
    assert 'value="inpdf" checked' in snd


def test_previous_orders_with_us_style_dates(app):
    """An app order dated 10/15/2030 used to crash the editor's 'Previous orders' panel."""
    with app.app_context():
        from orderapp.db import x
        from orderapp.order_insights import previous_for
        x("INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, created) VALUES "
          "(?, 'door', 'Door Order Form', 'P9', '10/15/2030', 'Sent', ?, '2030-10-15 09:00')",
          (app.cid2, json.dumps({"blocks": [{"qty": "1", "width": "30", "height": "80"}]})))
        rows = previous_for(app.cid2, "door", [])
        assert rows and rows[0]["date"] == "2030-10-15"


def test_roller_shade_fabric_fields(app):
    from orderapp.catalog import FORMS, SHADE_FABRICS
    keys = [f["key"] for f in FORMS["roller_shade"]["blocks"][0]]
    assert {"shade_brand", "shade_fabric", "openness"} <= set(keys)
    assert "VX Solar Screen 3000" in SHADE_FABRICS and "Mermet E-Screen" in SHADE_FABRICS
    with app.app_context():
        from orderapp.pdf_fill import fill_form
        pdf, _ = fill_form("roller_shade", {"header": {"name": "Test Place"}, "blocks": [
            {"qty": "1", "unit": "5", "width": "36", "length": "60", "shade_brand": "Vertilux (VX)",
             "shade_fabric": "VX Solar Screen 3000", "openness": "3%", "color": "Other", "color_other": "White Pearl"}]})
    import pymupdf as fitz
    doc = fitz.open(stream=pdf, filetype="pdf")
    vals = {w.field_name: w.field_value for p in doc for w in p.widgets() or []}
    assert "VX Solar Screen 3000" in vals.values() and "3%" in vals.values() and "Vertilux (VX)" in vals.values()


def test_order_sizes_go_to_units_automatically(app, c):
    def save(payload, oid=""):
        r = c.post("/orders/save", data={"csrf_token": c.t, "form_key": "vertical_blind", "customer_id": app.cid2,
                                         "order_id": oid, "payload": json.dumps(payload), "action": "save"})
        return int(r.headers["Location"].rsplit("/", 1)[1])

    def meas():
        with app.app_context():
            from orderapp.db import q, loads
            return [(r["unit_number"], r["room"], loads(r["data"]).get("width"), loads(r["data"]).get("height"))
                    for r in q("""SELECT m.*, u.unit_number FROM measurements m JOIN units u ON u.id=m.unit_id
                                  WHERE m.customer_id=? ORDER BY u.unit_number, m.room, m.id""", (app.cid2,))]
    p = {"header": {"name": "Mesa Ridge Homes", "po": "A1"}, "blocks": [
        {"qty": "1", "unit": "201", "room": "Living Room", "width": "72", "height": "84"},
        {"qty": "1", "unit": "201", "room": "Bedroom", "width": "36", "height": "60"},
        {"qty": "1", "unit": "305", "room": "Living Room", "width": "96", "height": "84"}]}
    oid = save(p)
    assert sorted(meas()) == sorted([("201", "Bedroom", "36", "60"), ("201", "Living Room", "72", "84"),
                                     ("305", "Living Room", "96", "84")])
    save(dict(p, header={"name": "Mesa Ridge Homes", "po": "A2"}))      # same sizes on a 2nd order: no duplicates
    assert len(meas()) == 3
    p["blocks"][1]["width"] = "35-1/2"                                  # fix a typo on the first order
    save(p, oid)
    assert ("201", "Bedroom", "35-1/2", "60") in meas() and len(meas()) == 4   # 2nd order still has 36
    with app.app_context():
        from orderapp.suggest import measurements_for_unit
        _, _, rows = measurements_for_unit(app.cid2, "305", form_key="vertical_blind")
        assert rows
    assert "Sizes saved to units automatically" in c.get(f"/orders/{oid}").get_data(as_text=True)


def test_estimate_toggle_and_status_taps(app, c):
    payload = {"header": {"name": "Mesa Ridge Homes", "po": "Q-1", "date": "10/15/2030"},
               "blocks": [{"qty": "1", "unit": "9", "width": "30", "height": "80"}]}
    ed = c.get(f"/orders/editor?form=door&customer={app.cid2}").get_data(as_text=True)
    assert 'data-kind="quote"' in ed and 'name="kind"' in ed
    r = c.post("/orders/save", data={"csrf_token": c.t, "form_key": "door", "customer_id": app.cid2, "kind": "quote",
                                     "payload": json.dumps(payload), "action": "pdf"})
    oid = int(r.headers["Location"].rsplit("/", 1)[1])
    with app.app_context():
        from orderapp.db import q
        o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    assert o["kind"] == "quote" and o["title"] == "Door Quote Request" and o["status"] == "Ready"
    assert "Door Quote Request" in os.path.basename(o["pdf_path"])
    import pymupdf as fitz
    assert "QUOTE REQUEST" in fitz.open(o["pdf_path"])[0].get_text()
    page = c.get(f"/orders/{oid}").get_data(as_text=True)
    assert 'value="Quote ready" class="on"' in page and "<select name=\"status\">" not in page
    assert "Turn into an order" in page
    assert "Please price the attached" in c.get(f"/orders/{oid}/send").get_data(as_text=True)
    # one tap -> order sent: becomes an order, banner gone
    c.post(f"/orders/{oid}/status", data={"csrf_token": c.t, "tap": "Order sent"})
    with app.app_context():
        from orderapp.db import q
        o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    assert (o["kind"], o["status"], o["title"]) == ("order", "Sent", "Door Order Form") and o["sent_at"]
    assert "QUOTE REQUEST" not in fitz.open(o["pdf_path"])[0].get_text()
    c.post(f"/orders/{oid}/status", data={"csrf_token": c.t, "tap": "Delivered"})
    trk = c.get("/tracking?status=all").get_data(as_text=True)
    assert 'value="Delivered" class="on"' in trk and 'value="Order sent"' in trk
