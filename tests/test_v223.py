"""v2.23: fire-rated doors, Pre-Hung form measurements (peephole, threshold size, hinges, bores like the Door
form), cabinet hinge bottom = top. All data is made up."""
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


def _fields(fk):
    from orderapp.catalog import FORMS
    return {f["key"]: f for f in FORMS[fk]["blocks"][0]}


def test_catalog_fields():
    from orderapp.pdf_fill import validate_catalog
    assert validate_catalog() == []
    ph, door = _fields("prehung"), _fields("door")
    assert not {"bore_qty", "one_sided_bore", "one_sided_bore_size"} & set(ph)          # replaced
    for k in ("one_sided_deadbolt", "deadbolt", "entry_lock", "peephole", "hinge1", "hinge4", "threshold_size",
              "threshold_thick", "fire_rated", "fire_rating"):
        assert k in ph, k
    assert ph["peephole"]["show_if"][0] == "peephole_yn"
    assert door["self_closing"]["kind"] == "choice" and door["self_closing"]["show_if"] == ("fire_rated", ["Yes"])
    assert [o[0] for o in door["fire_rating"]["options"]] == ["20 min", "45 min", "60 min", "90 min"]
    keys = list(door)
    assert keys.index("fire_rated") == keys.index("door_location") + 1     # shown next to interior / exterior


def _widgets(pdf_bytes):
    import pymupdf as fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return doc[0].get_text()


def test_prehung_pdf_has_new_boxes(app):
    from orderapp.pdf_fill import fill_form
    data = {"header": {"name": "Sample Gardens (made up)", "po": "S-1", "date": "10/21/2026"},
            "blocks": [{"qty": "1", "fire_rated": "Yes", "fire_rating": "20 min", "self_closing": "Y",
                        "rough_width": "38", "rough_height": "82-1/2", "width": "36", "height": "80",
                        "hinge1": "7", "hinge2": "35-5/8", "hinge3": "64-1/4",
                        "one_sided_deadbolt": "", "deadbolt": "36", "entry_lock": "42",
                        "threshold_type": "Adjustable", "threshold_color": "Mill", "threshold_size": '5-5/8" x 36"',
                        "threshold_thick": '1/2"', "peephole_yn": "Y", "peephole": "58"}]}
    with app.app_context():
        pdf, warns = fill_form("prehung", data)
    txt = _widgets(pdf)
    for v in ("82-1/2", "35-5/8", "64-1/4", '5-5/8" x 36"', "58", "42"):
        assert v in txt, v
    assert not any("fire-rated" in w for w in warns)


def test_fire_warnings(app):
    from orderapp.pdf_fill import check_order
    w = check_order("door", {"blocks": [{"fire_rated": "Yes", "self_closing": "No"}]})
    assert any("pick the rating" in x for x in w) and any("self-closing" in x for x in w)
    assert check_order("door", {"blocks": [{"fire_rated": "Yes", "fire_rating": "45 min", "self_closing": "Yes"}]}) == []


def test_cabinet_bottom_hinge_follows_top(app, c):
    payload = {"header": {"name": "Sample Gardens (made up)", "po": "S-2", "date": "10/21/2026"},
               "blocks": [{"qty": "2", "hinge_cut": "Yes", "hinge_top": "3"},
                          {"qty": "1", "hinge_cut": "Yes", "hinge_top": "3", "hinge_bottom": "4"}]}
    r = c.post("/orders/save", data={"csrf_token": c.t, "form_key": "cabinet_custom", "customer_id": app.cid,
                                     "payload": json.dumps(payload), "action": "save"})
    assert r.status_code == 302
    with app.app_context():
        from orderapp.db import loads, q
        d = loads(q("SELECT data FROM orders ORDER BY id DESC LIMIT 1", one=True)["data"])
    assert d["blocks"][0]["hinge_bottom"] == "3" and d["blocks"][1]["hinge_bottom"] == "4"


def test_editor_has_fire_and_hinge_helpers(c):
    html = c.get("/orders/editor?form=prehung").get_data(as_text=True)
    assert "function fireRated" in html and "function hingeMirror" in html
