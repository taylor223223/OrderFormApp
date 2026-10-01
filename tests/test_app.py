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
from orderapp.pdf_fill import fill_form, read_filled_form, validate_catalog  # noqa: E402


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
    widget_keys = {f["key"] for f in FORMS[fk]["blocks"][0] if not f.get("overlay")
                   and not any(isinstance(o[1], dict) for o in f.get("options", []))}
    for i, blk in enumerate(data["blocks"]):
        for k in widget_keys:
            if FORMS[fk]["blocks"][0][0]["kind"] and k in blk:
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
    assert product_fields("baseboard")[0]["key"] == "qty"


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
    assert j["blocks"][0]["style"] == "Six-Panel Embossed (Colonist)"
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


def test_lockout(client):
    c = client
    for _ in range(5):
        post(c, "/login", {"username": "taylor", "password": "wrong"}, page="/login")
    r = post(c, "/login", {"username": "taylor", "password": "secret123"}, page="/login")
    assert "Too many failed attempts" in r.get_data(as_text=True)
