import io
import json
import os
import re
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ["ORDERAPP_DATA"] = _tmp

from orderapp import create_app  # noqa: E402
from orderapp.catalog import FORMS, match_option, product_fields  # noqa: E402
from orderapp.email_parse import parse_lines  # noqa: E402
from orderapp.pdf_fill import _visible, fill_form, read_filled_form, validate_catalog  # noqa: E402


@pytest.fixture(scope="module")
def client():
    app = create_app(db_path=os.path.join(_tmp, "test.db"), testing=True)
    c = app.test_client()
    c.app = app
    return c


def csrf(c, path="/"):
    html = c.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    return m.group(1)


def post(c, path, data=None, page="/", **kw):
    data = dict(data or {})
    data["csrf_token"] = csrf(c, page)
    return c.post(path, data=data, **kw)


# ---------------------------------------------------------------- PDF layer
def test_catalog_matches_pdfs():
    assert validate_catalog() == []


def _sample_block(form_key, i=0):
    b = {}
    for f in FORMS[form_key]["blocks"][0]:
        if f["kind"] == "choice":
            b[f["key"]] = f["options"][(i + 1) % len(f["options"])][0]
        elif f["kind"] == "bool":
            b[f["key"]] = "Yes"
        elif f.get("measure"):
            b[f["key"]] = str(30 + i)
        else:
            b[f["key"]] = f"{f['key'][:6]}{i}"
    return b


@pytest.mark.parametrize("fk", list(FORMS))
def test_fill_every_form_roundtrip(fk):
    n = len(FORMS[fk]["blocks"])
    data = {"header": {"name": "Sunrise Villas", "acct": "1045", "address": "1 Main", "city": "Phoenix",
                       "state": "AZ", "zip": "85004", "mgmt": "Greystar", "phone": "602-555-1212",
                       "po": "PO77", "date": "09/30/2026"},
            "sales_rep": "Taylor", "comments": "Deliver to office",
            "blocks": [_sample_block(fk, i) for i in range(n)]}
    pdf, warns = fill_form(fk, data)
    assert pdf[:4] == b"%PDF"
    got_fk, got = read_filled_form(pdf)
    assert got_fk == fk
    assert got["header"]["name"] == "Sunrise Villas"
    gen = FORMS[fk].get("app_made")
    widget_keys = {f["key"] for f in FORMS[fk]["blocks"][0] if not f.get("overlay")
                   and not any(isinstance(o[1], dict) for o in f.get("options", []))
                   and (gen or not f.get("extra"))}
    opts = {f["key"]: dict(f["options"]) for f in FORMS[fk]["blocks"][0] if f["kind"] == "choice"}
    for i, blk in enumerate(data["blocks"]):
        for k in widget_keys:
            fd = next(f for f in FORMS[fk]["blocks"][0] if f["key"] == k)
            if not _visible(fd, blk):
                continue   # follow-up question that doesn't apply to this line
            if not gen and k in opts and opts[k].get(blk.get(k)) is None:
                continue   # app-only option: goes in the comments, not a box
            if FORMS[fk]["blocks"][0][0]["kind"] and k in blk:
                if k == "line_comments":   # app-only details are added after the rep's own comments
                    assert str(got["blocks"][i].get(k)).startswith(str(blk[k])), (fk, i, k)
                    continue
                assert str(got["blocks"][i].get(k)) == str(blk[k]), (fk, i, k)


def test_multi_page_and_custom_values():
    blocks = [{"qty": "1", "width": "30", "height": "80", "color": "Bronze", "unit": str(100 + i)} for i in range(7)]
    pdf, warns = fill_form("vertical_blind", {"header": {"name": "X"}, "blocks": blocks})
    import pymupdf
    assert pymupdf.open(stream=pdf, filetype="pdf").page_count == 2
    assert any("pages" in w for w in warns)


def test_match_option():
    opts = FORMS["door"]["blocks"][0][3]["options"]
    assert match_option("colonist", opts) == "Six-Panel Embossed (Colonist)"
    vs = [o for o in FORMS["vertical_blind"]["blocks"][0] if o["key"] == "slat_width"][0]["options"]
    assert match_option('3.5"', vs) == '3-1/2"'
    assert match_option("purple", opts) is None
    assert "length" in [f["key"] for f in product_fields("baseboard")]
    assert product_fields("garage_door")[0]["key"] == "qty"


def test_email_line_parser():
    items = parse_lines("Hi,\nUnit 1204 needs 2 vertical blinds 72 x 84 alabaster\n"
                        "Apt 305: bedroom door 30\" x 80\" left hand solid core\nThanks")
    assert items[0]["unit"] == "1204" and items[0]["product"] == "vertical_blind"
    assert items[0]["width"] == "72" and items[0]["height"] == "84" and items[0]["qty"] == "2"
    assert items[1]["swing"] == "Left Hand" and items[1]["core"] == "Solid Core"


# ---------------------------------------------------------------- web app
def test_full_flow(client, tmp_path):
    c = client
    assert c.get("/").status_code == 302                    # must log in
    assert "/setup" in c.get("/login").headers["Location"]  # first run
    r = post(c, "/setup", {"username": "taylor", "password": "secret123", "password2": "secret123"}, page="/setup")
    assert r.status_code == 302
    # CSRF required
    assert c.post("/customers/new", data={"name": "X"}).status_code == 400
    # settings: output folder + draft-file email
    post(c, "/settings", {"output_dir": str(tmp_path), "email_provider": "eml", "sales_rep": "Taylor A."})
    # customer
    r = post(c, "/customers/new", {"name": "Sunrise Villas Apartments", "acct": "1045", "city": "Phoenix"})
    cid = int(r.headers["Location"].rsplit("/", 1)[1])
    post(c, f"/customers/{cid}/contacts", {"name": "Maria", "email": "maria@sunrisevillas.com"})
    r = post(c, f"/customers/{cid}/floorplans", {"name": "A1", "beds": "1"})
    fid = int(r.headers["Location"].rsplit("/", 1)[1])
    post(c, f"/customers/{cid}/units", {"units": "101-104, 201", "floorplan_id": str(fid)})
    r = post(c, "/measurements/new", {"customer_id": cid, "floorplan_id": fid, "product": "vertical_blind",
                                      "room": "Living Room", "f_width": "72", "f_height": "84",
                                      "f_color": "Alabaster", "f_qty": "1", "verified": "1"})
    assert r.status_code == 302
    post(c, "/measurements/new", {"customer_id": cid, "floorplan_id": fid, "product": "interior_door",
                                  "room": "Bedroom", "f_width": "30", "f_height": "80", "f_style": "Colonist",
                                  "f_swing": "Left Hand"})
    assert c.get(f"/customers/{cid}?tab=units").status_code == 200
    assert c.get(f"/floorplans/{fid}").status_code == 200
    # editor + unit lookup
    assert c.get(f"/orders/editor?form=vertical_blind&customer={cid}").status_code == 200
    j = c.get(f"/api/unit?customer={cid}&form=vertical_blind&unit=103").get_json()
    assert j["ok"] and j["blocks"][0]["width"] == "72" and j["blocks"][0]["room"] == "Living Room"
    j = c.get(f"/api/unit?customer={cid}&form=door&unit=103").get_json()
    assert j["blocks"][0]["style"] == "6 Panel (Colonist)"
    # save + pdf
    payload = {"header": {"name": "Sunrise Villas Apartments", "acct": "1045", "po": "PO-1", "date": "09/30/2026",
                          "phone": "602-555-0000"},
               "blocks": [dict(j["blocks"][0], qty="2")], "comments": "rush", "sales_rep": "Taylor A."}
    r = post(c, "/orders/save", {"form_key": "door", "customer_id": cid, "payload": json.dumps(payload),
                                 "action": "pdf"})
    oid = int(r.headers["Location"].rsplit("/", 1)[1])
    page = c.get(f"/orders/{oid}").get_data(as_text=True)
    assert "Ready" in page
    assert c.get(f"/orders/{oid}/pdf").data[:4] == b"%PDF"
    pdfs = [p for p in tmp_path.rglob("*.pdf")]
    assert pdfs, "PDF written to output folder"
    # order revealed a phone number not on the customer -> review batch exists
    assert "differ from the saved record" in page or c.get("/review").status_code == 200
    # email send via draft file
    r = post(c, f"/orders/{oid}/send", {"to": "orders@example.com", "subject": "s", "body": "b", "mode": "review"},
             page=f"/orders/{oid}/send")
    assert r.status_code == 302
    assert list(tmp_path.rglob("*.eml"))
    # tracking
    post(c, f"/orders/{oid}/status", {"status": "Sent", "vendor_ref": "SO-55"})
    post(c, "/tracking/add", {"title": "Phone order baseboards", "customer_id": cid, "status": "Confirmed"})
    t = c.get("/tracking").get_data(as_text=True)
    assert "SO-55" in t and "Phone order baseboards" in t
    # learn sizes from order
    assert post(c, f"/orders/{oid}/learn").status_code == 302


def test_csv_import_and_review(client):
    c = client
    csv_text = ("Property,Acct #,Phone,Contact,Contact Email,Unit,Floorplan,Product,Room,Width,Height,Color,PO,Date\n"
                "Sunrise Villas,1045,602-555-9999,Bob,bob@sunrisevillas.com,301,B2,Vertical Blind,Bedroom 2,60,72,White,P-9,1/5/2026\n"
                "Desert Palms,,480-555-1111,,,12,,Interior Door,Bath,24,80,,P-10,2/1/2026\n")
    r = post(c, "/import", {"file": (io.BytesIO(csv_text.encode()), "old.csv")}, page="/import",
             content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    token = re.search(r'name="token" value="([^"]+)"', html).group(1)
    headers = ["Property", "Acct #", "Phone", "Contact", "Contact Email", "Unit", "Floorplan", "Product", "Room",
               "Width", "Height", "Color", "PO", "Date"]
    selected = {}
    for i, _ in enumerate(headers):
        m = re.search(rf'name="map_{i}".*?<option value="([^"]*)" selected', html, re.S)
        selected[f"map_{i}"] = m.group(1) if m else ""
    assert selected["map_0"] == "name" and selected["map_1"] == "acct" and selected["map_9"] == "width"
    r = post(c, "/import/apply_mapping", {"token": token, "filename": "old.csv", **selected}, page="/import")
    bid = int(r.headers["Location"].rsplit("/", 1)[1])
    page = c.get(f"/review/{bid}").get_data(as_text=True)
    assert "NEW: Desert Palms" in page and "conflict" in page or "fill blank" in page
    ids = re.findall(r'name="accept" value="(\d+)"', page)
    r = post(c, f"/review/{bid}", {"accept": ids}, page=f"/review/{bid}")
    assert r.status_code == 302
    cust = c.get("/customers?q=Desert").get_data(as_text=True)
    assert "Desert Palms" in cust
    # existing customer got new floorplan/unit/measurement/contact
    j = c.get("/api/unit?customer=1&form=vertical_blind&unit=301").get_json()
    assert j["blocks"] and j["blocks"][0]["width"] == "60"


def test_email_to_order(client):
    c = client
    body = ("Hi Taylor,\nPO 4471\nUnit 102 needs a new bedroom door, left hand\n"
            "Unit 104 - vertical blind living room 70 x 84\n\nMaria\n602-555-3434")
    r = post(c, "/email/paste", {"sender": "maria@sunrisevillas.com", "body": body, "subject": "order"},
             page="/email")
    rid = int(r.headers["Location"].rsplit("/", 1)[1])
    page = c.get(f"/email/msg/{rid}").get_data(as_text=True)
    assert "matched by sender email" in page and "4471" in page
    r = post(c, f"/email/msg/{rid}/order", {"form_key": "door", "customer_id": "1", "po": "4471",
                                             "units": "102"}, page=f"/email/msg/{rid}")
    assert "/orders/editor?order=" in r.headers["Location"]
    oid = int(r.headers["Location"].split("=")[1])
    view = c.get(f"/orders/{oid}").get_data(as_text=True)
    assert "Unit 102" in view and "Left Hand" in view and "30" in view   # merged email + saved floorplan door


def test_backup_export_logout(client):
    c = client
    assert c.get("/settings/backup").status_code == 200
    assert b"Sunrise" in c.get("/settings/export/customers").data
    assert c.get("/settings/export/measurements").status_code == 200
    for p in ["/", "/customers", "/orders/new", "/tracking", "/email", "/import", "/review", "/settings"]:
        assert c.get(p).status_code == 200, p
    post(c, "/logout")
    assert c.get("/customers").status_code == 302


def _jpeg(color=(200, 30, 30), size=(3000, 2000)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "JPEG")
    return b.getvalue()


def test_photo_field_order_and_send(client, tmp_path):
    c = client
    post(c, "/login", {"username": "taylor", "password": "secret123"}, page="/login")
    if c.get("/").status_code != 200:   # account may be locked by test_lockout ordering
        pytest.skip("login locked")
    post(c, "/settings", {"output_dir": str(tmp_path), "email_provider": "eml"})
    r = post(c, "/field-order", {"customer_id": "1", "what": "Door order", "po": "F-1", "units": "204",
                                 "notes": "see sketch", "action": "send",
                                 "photos": [(io.BytesIO(_jpeg()), "IMG_1.jpg"), (io.BytesIO(_jpeg((0, 90, 0))), "IMG_2.jpg")]},
             page="/field-order", content_type="multipart/form-data")
    assert "/send" in r.headers["Location"]
    oid = int(r.headers["Location"].split("/")[2])
    page = c.get(f"/orders/{oid}/send").get_data(as_text=True)
    assert "2 photo(s)" in page and "Units: 204" in page
    from orderapp.photos import photos_for
    with c.app.app_context():
        ph = photos_for(oid)
        assert len(ph) == 2 and os.path.getsize(ph[0]["path"]) < 400_000   # downsized
    r = post(c, f"/orders/{oid}/send", {"to": "orders@example.com", "subject": "s", "body": "b",
                                       "mode": "review", "photos_mode": "pdf"}, page=f"/orders/{oid}/send")
    assert r.status_code == 302
    assert list(tmp_path.rglob("*photos*.pdf")) and list(tmp_path.rglob("*.eml"))
    # add a photo to a form order and view it
    r = post(c, "/orders/1/photos", {"photos": [(io.BytesIO(_jpeg()), "door.png")], "caption": "Unit 101"},
             page="/orders/1", content_type="multipart/form-data")
    view = c.get("/orders/1").get_data(as_text=True)
    assert "Unit 101" in view
    pid = int(re.search(r'/photos/(\d+)"', view).group(1))
    assert c.get(f"/photos/{pid}").status_code == 200



def test_lockout(client):
    c = client
    for _ in range(5):
        post(c, "/login", {"username": "taylor", "password": "wrong"}, page="/login")
    r = post(c, "/login", {"username": "taylor", "password": "secret123"}, page="/login")
    assert "Too many failed attempts" in r.get_data(as_text=True)


def test_merged_forms_pick_paper_version():
    """Door / Vertical Blind are one choice in the app; the right paper form is printed."""
    from orderapp.catalog import choose_variant, form_list, public_spec
    keys = [k for k, _ in form_list()]
    assert "new_door" not in keys and "vertical_blind_2" not in keys
    styles = next(f for f in public_spec("door")["fields"] if f["key"] == "style")["options"]
    assert "2 Panel (Carrera)" in styles and "3 Panel Shaker Craftsman" in styles and "HC Primecoat" in styles
    assert "mount" in [f["key"] for f in public_spec("vertical_blind")["fields"]]
    hdr = {"name": "Sunrise", "po": "P1", "date": "10/02/2026"}
    # one Door form: types without their own box are written in its "Other" box
    d = {"header": hdr, "blocks": [{"qty": "1", "style": "2 Panel (Carrera)", "finish": "Primecoat",
                                    "width": "30", "height": "80", "swing": "Left Hand"}]}
    assert choose_variant("door", d) == "door"
    fk, back = read_filled_form(fill_form("door", d)[0])
    assert fk == "door" and back["blocks"][0]["style"] == "Other"
    assert back["blocks"][0]["style_other"] == "2 Panel (Carrera)"
    # a type with no box anywhere -> original Door form, written in the "Other" box
    d["blocks"][0]["style"] = "3 Panel Shaker Craftsman"
    assert choose_variant("door", d) == "door"
    fk, back = read_filled_form(fill_form("door", d)[0])
    assert fk == "door" and back["blocks"][0]["style"] == "Other"
    assert back["blocks"][0]["style_other"] == "3 Panel Shaker Craftsman"
    # old saved name still works; 6 panel fits both -> original
    d["blocks"][0]["style"] = "Colonist (6-Panel)"
    assert choose_variant("door", d) == "door"
    fk, back = read_filled_form(fill_form("door", d)[0])
    assert back["blocks"][0]["style"] == "Six-Panel Embossed (Colonist)"
    # app-only door options land in the comments
    d["blocks"][0].update(prehung="Yes", threshold="36", hardware="Yes", hw_collection="Soma",
                          hw_function="Privacy", hw_finish="Matte Black")
    fk, back = read_filled_form(fill_form("door", d)[0])
    assert "Threshold" in back["comments"] and "Soma" in back["comments"] and fk == "door"
    d["blocks"][0]["hardware"] = "No"   # hidden follow-up answers are not printed
    fk, back = read_filled_form(fill_form("door", d)[0])
    assert "Soma" not in back["comments"]
    # verticals: a mount choice with no valance -> the inside/outside mount paper form
    v = {"header": hdr, "blocks": [{"qty": "1", "mount": "Outside Mount", "om_headrail": "98", "om_slat": "84",
                                    "color": "White"}]}
    assert choose_variant("vertical_blind", v) == "vertical_blind_2"
    assert read_filled_form(fill_form("vertical_blind", v)[0])[0] == "vertical_blind_2"
    v["blocks"][0]["valance"] = "Upgrade Valance"
    assert choose_variant("vertical_blind", v) == "vertical_blind"
    assert read_filled_form(fill_form("vertical_blind", v)[0])[0] == "vertical_blind"


class _FakeInbox:
    """Stands in for Outlook: three emails, only two have 'Order' in the subject."""
    name = "graph"

    def __init__(self):
        self.read_bodies = []
        self.msgs = [
            {"msg_id": "m1", "subject": "ORDER - Sunrise Villas", "sender": "maria@sunrisevillas.com",
             "sender_name": "Maria", "received": "2026-10-03 08:00",
             "body": "PO 5512\nUnit 102 needs a new bedroom door, left hand\n"
                     "Unit 104 - vertical blind living room 70 x 84\nMaria"},
            {"msg_id": "m2", "subject": "Lunch friday?", "sender": "friend@example.com", "sender_name": "Bob",
             "received": "2026-10-03 09:00", "body": "tacos"},
            {"msg_id": "m3", "subject": "Re: order for Desert Palms", "sender": "unknown@nowhere.com",
             "sender_name": "Sam", "received": "2026-10-03 10:00", "body": "Can you call me back?"},
        ]

    def available(self):
        return True, ""

    def list_messages(self, days=14, limit=75, unread_only=False, search="", subject_word=""):
        return [dict(m, preview="") for m in self.msgs if subject_word.lower() in m["subject"].lower()]

    def get_message(self, msg_id):
        m = next(x for x in self.msgs if x["msg_id"] == msg_id)
        self.read_bodies.append(msg_id)
        return dict(m, attachments=[])


def test_auto_draft_order_emails(client):
    c = client
    from orderapp.routes.emails import auto_check
    fake = _FakeInbox()
    with c.app.test_request_context():
        from orderapp.db import q
        before = q("SELECT COUNT(*) n FROM orders", one=True)["n"]
        r = auto_check(fake)
        assert r["found"] == 2 and r["new"] == 2 and not r["error"]
        assert "m2" not in fake.read_bodies                     # non-order email never opened
        drafts = q("SELECT * FROM orders WHERE notes LIKE 'Auto-drafted%' ORDER BY id")
        assert len(drafts) >= 2 and q("SELECT COUNT(*) n FROM orders", one=True)["n"] == before + len(drafts)
        assert {d["form_key"] for d in drafts} >= {"door", "vertical_blind"}
        assert all(d["status"] == "Draft" and d["customer_id"] == 1 and d["po"] == "5512" for d in drafts)
        m3 = q("SELECT * FROM email_messages WHERE msg_id='m3'", one=True)
        assert m3["status"] == "needs form" and not m3["order_id"]
        again = auto_check(fake)                                 # second run: nothing new
        assert again["new"] == 0 and q("SELECT COUNT(*) n FROM orders", one=True)["n"] == before + len(drafts)
    dash = c.get("/").get_data(as_text=True)
    assert "turned into draft orders" in dash
    page = c.get("/email").get_data(as_text=True)
    assert "Review draft #" in page and "pick form" in page and "ORDER - Sunrise Villas" in page


def test_new_products_and_app_made_forms():
    from orderapp.catalog import form_list, public_spec
    from orderapp import option_photos
    keys = [k for k, _ in form_list()]
    for fk in ("roller_shade", "trim", "door_hardware", "bath_hardware", "cabinet", "closet_shower"):
        assert fk in keys
    spec = public_spec("door")
    style = next(f for f in spec["fields"] if f["key"] == "style")
    assert style["photos"] == "door_style" and "2 Panel Arch" in style["photo_map"]
    thr = next(f for f in spec["fields"] if f["key"] == "threshold")
    assert thr["show_if"] == {"key": "prehung", "in": ["Yes"]}
    prof = next(f for f in public_spec("trim")["fields"] if f["key"] == "profile")
    assert len(prof["options"]) == 10 and prof["photo_map"]
    assert option_photos.slug("A: #103 Casing") == "a" and option_photos.slug("Soma (Matte Black)") == "soma"
    hz = next(f for f in public_spec("horizontal_blind")["fields"] if f["key"] == "style")
    assert 'Premium Basswood 2-1/2"' in hz["options"]
    fk, back = read_filled_form(fill_form("horizontal_blind", {"blocks": [{"style": '1" Metal Plus Mini Blind'}]})[0])
    assert fk == "horizontal_blind" and back["blocks"][0]["style"] == '1" Metal Plus Mini Blind' 
    # vertical: 2" ribbed only in white
    w = fill_form("vertical_blind", {"blocks": [{"slat_width": '2"', "slat_style": "Ribbed", "color": "Alabaster"}]})[1]
    assert any("only come in White" in x for x in w)
    # cabinet form: drawn by the app, data comes back out of the PDF
    d = {"header": {"name": "Sunrise"}, "comments": "x",
         "blocks": [{"qty": "1", "cab_room": "Kitchen", "cab_item": "Double Box with Sink", "style": "Shaker",
                     "refacing": "No", "thermofoil_color": "hidden", "mirror_frame": "Yes", "mirror_height": "36"}]}
    pdf, warns = fill_form("cabinet", d)
    fk, back = read_filled_form(pdf)
    assert fk == "cabinet" and back["blocks"][0]["style"] == "Shaker"
    import pymupdf
    txt = pymupdf.open("pdf", pdf)[0].get_text()
    assert "Double Box with Sink" in txt and "hidden" not in txt and "Mirror Frame Height" in txt
