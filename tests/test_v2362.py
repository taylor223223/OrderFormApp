"""v2.36.2: order emails to the desk are titled '(APARTMENT NAME) (ORDER TYPE)' (+ ' Quote'); PO only if there is one.
All data is made up."""
import json
import os
import tempfile

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402


def test_subject_is_property_and_type():
    a = create_app(db_path=os.path.join(tempfile.mkdtemp(), "s.db"), testing=True)
    with a.app_context():
        from orderapp.db import q, setting, x
        from orderapp.routes.orders import _fmt
        hdr = json.dumps({"header": {"name": "Sample Palms"}})

        def subj(form, kind, po=""):
            oid = x("INSERT INTO orders(form_key, title, po, status, data, kind) VALUES (?,?,?,?,?,?)",
                    (form, "x", po, "Ready", hdr, kind))
            o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
            return _fmt(setting("subject_template"), o, json.loads(hdr))
        assert subj("roller_shade", "order") == "Sample Palms Roller Shade"
        assert subj("window_screen", "quote") == "Sample Palms Window Screen Quote"
        assert subj("vertical_blind_2", "order", "SP-9") == "Sample Palms Vertical Blind - PO SP-9"


def test_old_default_subject_is_upgraded(tmp_path):
    import sqlite3
    db = str(tmp_path / "old.db")
    create_app(db_path=db, testing=True)
    con = sqlite3.connect(db)
    con.execute("UPDATE settings SET value='{title} - {customer} - PO {po}' WHERE key='subject_template'")
    con.execute("DELETE FROM settings WHERE key='dm_subject_v2'")
    con.commit(); con.close()
    a = create_app(db_path=db, testing=True)
    with a.app_context():
        from orderapp.db import setting
        assert setting("subject_template") == "{customer} {type}{quote}{po_tail}"
