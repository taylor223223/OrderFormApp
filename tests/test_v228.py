"""v2.28: cabinet hardware Satin Square / Custom; material, style and color on the cabinet and countertop forms;
peel & stick color list left blank. All data is made up."""
import os
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return create_app(db_path=os.path.join(tempfile.mkdtemp(), "a.db"), testing=True)


def _keys(fk):
    from orderapp.catalog import FORMS
    return {f["key"]: f for f in FORMS[fk]["blocks"][0]}


def _text(pdf):
    import pymupdf
    return "".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))


def test_fields():
    from orderapp.cabinet_layout import LAMINATE_COLORS
    from orderapp.pdf_fill import validate_catalog
    assert validate_catalog() == [] and LAMINATE_COLORS == []
    for fk in ("cabinet_prefab", "cabinet_custom"):
        f = _keys(fk)
        assert [o[0] for o in f["cab_hw"]["options"]] == ["Satin Square", "Custom"], fk
        assert f["cab_hardware"]["show_if"] == ("cab_hw", ["Custom"]), fk
        assert {"style", "cab_material", "cab_finish"} <= set(f), fk
    ct = _keys("countertop")
    assert {"ct_material", "ct_material_other", "ct_color"} <= set(ct) and "suggest" not in ct["ct_color"]
    assert "ct_style" not in ct                                    # material + color only
    assert not {"ct_yn", "ct_material", "cab_material"} & set(_keys("cabinet"))   # retired combined form as it was


def test_pdfs_print_them(app):
    from orderapp.pdf_fill import fill_form
    with app.app_context():
        pdf, _ = fill_form("cabinet_prefab", {"header": {"name": "Sample (made up)"}, "blocks": [
            {"qty": "1", "style": "Shaker", "cab_material": "Plywood", "cab_finish": "White", "cab_hw": "Custom",
             "cab_hardware": "Matte black bar pull 5 in"}]})
        t = _text(pdf)
        assert "Plywood" in t and "White" in t and "Matte black bar pull 5 in" in t
        pdf, _ = fill_form("countertop", {"header": {"name": "Sample (made up)"}, "blocks": [
            {"qty": "1", "ct_material": "Custom", "ct_material_other": "Quartz remnant", "ct_color": "Sample Gray"}]})
        t = _text(pdf)
        assert "Quartz remnant" in t and "Sample Gray" in t


def test_old_orders_keep_typed_hardware_and_countertop(app):
    from orderapp.catalog import upgrade_blocks
    d = upgrade_blocks({"blocks": [{"cab_hardware": "Brushed knob"}, {"cab_hardware": "satin square"}]})
    assert d["blocks"][0]["cab_hw"] == "Custom" and d["blocks"][1]["cab_hw"] == "Satin Square"
    from orderapp.pdf_fill import fill_form
    with app.app_context():
        pdf, _ = fill_form("cabinet", {"header": {"name": "Sample (made up)"},
                                       "blocks": [{"qty": "1", "cab_hardware": "Brushed knob"}]})
    assert "Brushed knob" in _text(pdf)


def test_kitchen_layout_material_and_color():
    from orderapp import cabinet_layout as C
    d = C.starter()
    d["cab"] = {"material": "Plywood", "color": "White"}
    d["top"]["material"], d["top"]["color"] = "Quartz remnant", "Sample Gray"
    t = _text(C.make_pdf({"data": d, "name": "Sample kitchen (made up)"}, with_top=False))
    assert "Cabinet material: Plywood" in t and "Cabinet color: White" in t
    assert "Quartz remnant" not in t and "Cabinet Order - Kitchen Layout" in t     # countertop is its own form
    assert "Quartz remnant" in _text(C.make_pdf({"data": d, "name": "Sample kitchen (made up)"}))
    lines = C.order_lines(d, "cabinet_prefab")
    assert lines and lines[0]["cab_material"] == "Plywood" and lines[0]["cab_finish"] == "White"
    ct = C.order_lines(d, "countertop")
    assert ct[0]["ct_material"] == "Custom" and ct[0]["ct_material_other"] == "Quartz remnant"
    assert C.normalize({"v": 2, "walls": []})["cab"] == {"style": "", "material": "", "color": "", "hw": "",
                                                         "hw_custom": ""}
