"""v2.32.0: Pre-Hung is part of the Door form (Pre-Hung Yes/No changes the form and the paper it prints on);
no rough opening on pre-hung doors. All data is made up."""
import os
import tempfile

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

import pymupdf as fitz  # noqa: E402

from orderapp.catalog import FORMS, choose_variant, form_list, public_spec  # noqa: E402
from orderapp.pdf_fill import fill_form, read_filled_form  # noqa: E402


def _text(pdf):
    return "\n".join(p.get_text() for p in fitz.open(stream=pdf, filetype="pdf"))


def test_one_door_form_in_the_app():
    keys = [k for k, _ in form_list()]
    assert "door" in keys and "prehung" not in keys
    assert "prehung" in FORMS                                          # old pre-hung orders still open
    f = {x["key"]: x for x in public_spec("door")["fields"]}
    order = list(f)
    assert order.index("prehung") < order.index("style")             # asked up top
    assert f["prehung"]["label"].startswith("Pre-Hung")
    for k in ("swing_dir", "jamb", "jamb_thick", "threshold_type", "threshold_size", "hinge_color", "nrp_hinges",
              "glass_insert"):
        assert f[k]["show_if"] == {"key": "prehung", "in": ["Yes"]}, k
    for k in ("hinge1", "hinge4", "door_edge"):
        assert f[k]["show_if"] == {"key": "prehung", "in": ["No", ""]}, k   # door only
    assert "show_if" not in f["self_closing"] and {"key": "prehung", "in": ["Yes"]} in f["self_closing"]["show_any"]
    assert "rough_width" not in f and "rough_height" not in f


def test_no_rough_opening():
    keys = {x["key"] for x in FORMS["prehung"]["blocks"][0]}
    assert not {"rough_width", "rough_height"} & keys and "jamb_thick" in keys
    pg = fitz.open(os.path.join("orderapp", "forms", "OF-Pre-Hung.pdf"))[0]
    assert not any(w.field_name.startswith("Rough") for w in pg.widgets())


def test_prehung_prints_on_the_prehung_form():
    d = {"header": {"name": "Sample Palms (made up)", "po": "P-1"},
         "blocks": [{"qty": "1", "prehung": "Yes", "width": "36", "height": "80", "jamb_thick": "4-9/16",
                     "peephole": "58", "self_closing": "Yes", "threshold_type": "ADA", "hinge1": "7"}]}
    assert choose_variant("door", d) == "prehung"
    pdf, _ = fill_form("door", d)
    fk, back = read_filled_form(pdf)
    b = back["blocks"][0]
    assert fk == "prehung" and b["jamb_thick"] == "4-9/16" and b["threshold_type"] == "ADA"
    assert b["peephole"] == "58" and b["peephole_yn"] == "Y" and b["self_closing"] == "Y"
    assert "hinge1" not in b
    d["blocks"][0]["prehung"] = "No"
    assert choose_variant("door", d) == "door" and read_filled_form(fill_form("door", d)[0])[0] == "door"


def test_mixed_order_prints_both_forms():
    d = {"header": {"name": "Sample Palms (made up)"},
         "blocks": [{"qty": "1", "width": "30", "height": "80"},
                    {"qty": "1", "prehung": "Yes", "width": "36", "height": "80"}]}
    pdf, warns = fill_form("door", d)
    doc = fitz.open(stream=pdf, filetype="pdf")
    assert doc.page_count == 2
    assert "PRE-HUNG DOOR FORM" not in doc[0].get_text().upper() and "PRE-HUNG DOOR FORM" in doc[1].get_text().upper()
    assert any("Pre-Hung form" in w for w in warns)


def test_quote_keeps_the_prehung_title():
    from orderapp.order_kind import stamp_quote
    d = {"blocks": [{"qty": "1", "width": "30", "height": "80"}, {"qty": "1", "prehung": "Yes", "width": "36"}]}
    q = fitz.open(stream=stamp_quote(fill_form("door", d)[0], "Door Order Form"), filetype="pdf")
    assert "Door Quote Form" in q[0].get_text() and "PRE-HUNG DOOR QUOTE FORM" in q[1].get_text()
