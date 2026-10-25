"""v2.26: contact boxes on every company form; countertop form drawn as a top view with the measurement boxes
where they're taken. All data is made up."""
import os
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402

H = {"name": "Sample Gardens (made up)", "contact": "Pat Sample", "contact_phone": "(602) 555-0101",
     "contact_email": "pat@sample.example", "po": "S-26", "date": "10/25/2026"}


@pytest.fixture(scope="module")
def app():
    d = tempfile.mkdtemp()
    return create_app(db_path=os.path.join(d, "a.db"), testing=True)


def _text(pdf):
    import pymupdf as fitz
    return fitz.open(stream=pdf, filetype="pdf")[0].get_text()


def test_every_form_has_contact_boxes(app):
    import pymupdf as fitz
    from orderapp.catalog import FORMS
    from orderapp.paths import resource_path
    from orderapp.pdf_fill import fill_form
    for fk, spec in FORMS.items():
        assert {"contact", "contact_phone", "contact_email"} <= set(spec["header"]), fk
        names = {w.field_name for w in fitz.open(resource_path(os.path.join("forms", spec["file"])))[0].widgets()}
        assert {"Contact Name", "Contact Phone", "Contact Email"} <= names, fk
        with app.app_context():
            pdf, _ = fill_form(fk, {"header": H, "blocks": [{"qty": "1"}]})
        t = _text(pdf)
        assert "Pat Sample" in t and "pat@sample.example" in t, fk
        assert "Contact: Pat Sample" not in t, fk          # not in the comments any more


def test_countertop_plan_boxes(app):
    from orderapp.catalog import FORMS
    from orderapp.pdf_fill import fill_form, validate_catalog
    assert validate_catalog() == []
    plan = {f["key"]: f["plan"] for f in FORMS["countertop"]["blocks"][0] if f.get("plan")}
    assert plan == {"length": "back", "depth": "depth", "overhang": "overhang", "sink_size": "sink_size",
                    "sink_center": "sink_left", "sink_center_r": "sink_right", "sink_front": "sink_front"}
    data = {"header": H, "blocks": [{"qty": "1", "ct_layout": "Straight", "sink": "Single Bowl", "length": "96-1/4",
                                     "depth": "25-1/2", "overhang": "1", "sink_size": "33 x 22",
                                     "sink_center": "41-3/8", "sink_center_r": "54-7/8", "sink_front": "12-3/4"}]}
    with app.app_context():
        pdf, _ = fill_form("countertop", data)
    t = _text(pdf)
    for v in ("96-1/4", "25-1/2", "33 x 22", "41-3/8", "54-7/8", "12-3/4", "BACK WALL", "FRONT EDGE"):
        assert v in t, v


def test_layout_fills_sink_spots():
    from orderapp import cabinet_layout as C
    d = C.starter()
    lines = C.order_lines(d, "countertop")
    sinks = [ln for ln in lines if ln["sink"] != "None"]
    assert sinks and "sink_center" in sinks[0] and "sink_center_r" in sinks[0]
