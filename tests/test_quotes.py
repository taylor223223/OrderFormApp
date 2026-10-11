"""Quotes, account pricing, one-click reorder, holds aging CSV, morning delivery list."""
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402


@pytest.fixture(scope="module")
def app():
    d = tempfile.mkdtemp()
    a = create_app(db_path=os.path.join(d, "q.db"), testing=True, demo=True)
    with a.app_context():
        from orderapp.db import set_setting
        set_setting("output_dir", os.path.join(d, "out"))
    return a


def _csrf(c):
    return re.search(rb'name="csrf-token" content="([^"]+)"', c.get("/").data).group(1).decode()


def Q(app, sql, a=(), one=False):
    with app.app_context():
        from orderapp.db import q
        return q(sql, a, one)


def test_pricing_order(app):
    from orderapp import pricing as P
    with app.app_context():
        from orderapp.db import q
        palo = q("SELECT id FROM customers WHERE name='Palo Verde Commons'", one=True)["id"]
        p = P.price_for(palo, "VB-35-ALB")
        assert p["source"] == "Account price" and p["price"] == 99.0
        p = P.price_for(palo, "MP-1-WHT")
        assert p["source"] == "Last paid"
        assert P.price_for(None, "WS-BRZ")["source"] == "Latest price"
        # a vertical blind is never priced like a faux wood blind
        p = P.price_for(palo, None, 'Vertical Blind 72" x 80"', "Blinds & Shades")
        assert "vertical" in p["description"].lower()
        book, _ = P.price_book(palo)
        assert any(b["agreed"] == 99.0 for b in book) and all(b["last_price"] for b in book)
    assert P._match_score("bi-fold closet door 36", "60x80 bi-pass closet door") == 0


def test_quote_flow(app):
    c = app.test_client()
    t = _csrf(c)
    cid = Q(app, "SELECT id FROM customers WHERE name='Saguaro Ridge Apartments'", one=True)["id"]
    fid = Q(app, "SELECT id FROM floorplans WHERE customer_id=? AND name='A1'", (cid,), one=True)["id"]
    r = c.post("/quotes/new", data={"csrf_token": t, "floorplan": fid, "units": "3"})
    qid = int(r.headers["Location"].rsplit("/", 1)[1])
    lines = Q(app, "SELECT * FROM quote_lines WHERE quote_id=?", (qid,))
    assert len(lines) >= 6 and all(ln["qty"] >= 3 for ln in lines if ln["unit"] == "ea")
    assert c.get(f"/quotes/{qid}").status_code == 200
    # edit: change a price, add a line, make the PDF
    data = {"csrf_token": t, "title": "Turn kit A1", "valid_until": "2026-12-31", "notes": "", "terms": "Net 30",
            "action": "pdf", "line_id": [], "item_no": [], "description": [], "category": [], "qty": [], "unit": [],
            "unit_price": [], "price_source": []}
    for ln in lines:
        for k, v in (("line_id", str(ln["id"])), ("item_no", ln["item_no"] or ""), ("description", ln["description"]),
                     ("category", ln["category"] or ""), ("qty", str(ln["qty"])), ("unit", ln["unit"]),
                     ("unit_price", "10" if ln["unit_price"] is None else str(ln["unit_price"])),
                     ("price_source", ln["price_source"] or "")):
            data[k].append(v)
    for k, v in (("line_id", "new"), ("item_no", "X-1"), ("description", "Delivery"), ("category", "Other"),
                 ("qty", "1"), ("unit", "ea"), ("unit_price", "75"), ("price_source", "")):
        data[k].append(v)
    c.post(f"/quotes/{qid}", data=data)
    qu = Q(app, "SELECT * FROM quotes WHERE id=?", (qid,), one=True)
    assert qu["title"] == "Turn kit A1" and qu["pdf_path"] and os.path.exists(qu["pdf_path"])
    assert Q(app, "SELECT COUNT(*) n FROM quote_lines WHERE quote_id=?", (qid,), one=True)["n"] == len(lines) + 1
    import fitz
    txt = fitz.open(qu["pdf_path"])[0].get_text()
    assert "QUOTE" in txt and qu["number"] in txt and "Delivery" in txt
    # email draft (eml in the demo) marks it Sent
    c.post(f"/quotes/{qid}/send", data={"csrf_token": t, "to": "x@example.com", "subject": "", "body": ""})
    assert Q(app, "SELECT status FROM quotes WHERE id=?", (qid,), one=True)["status"] == "Sent"
    # approved -> draft orders on the right forms
    c.post(f"/quotes/{qid}/status", data={"csrf_token": t, "status": "Approved"})
    r = c.post(f"/quotes/{qid}/order", data={"csrf_token": t})
    qu = Q(app, "SELECT * FROM quotes WHERE id=?", (qid,), one=True)
    assert qu["status"] == "Ordered" and qu["order_ids"]
    forms = {r["form_key"] for r in Q(app, f"SELECT form_key FROM orders WHERE id IN ({qu['order_ids']})")}
    assert "horizontal_blind" in forms and "trim" in forms
    # re-quote an invoice
    sid = Q(app, "SELECT id FROM sales WHERE customer_id=? LIMIT 1", (cid,), one=True)["id"]
    r = c.post("/quotes/new", data={"csrf_token": t, "invoice": sid})
    qid2 = int(r.headers["Location"].rsplit("/", 1)[1])
    assert Q(app, "SELECT COUNT(*) n FROM quote_lines WHERE quote_id=?", (qid2,), one=True)["n"] == \
        Q(app, "SELECT COUNT(*) n FROM sales_lines WHERE sale_id=?", (sid,), one=True)["n"]
    j = c.get(f"/api/items?q=vertical&customer={cid}").get_json()
    assert j and j[0]["price"]
    for url in ("/quotes", "/quotes?status=Sent", f"/customers/{cid}/prices"):
        assert c.get(url).status_code == 200


def test_reorder(app):
    c = app.test_client()
    t = _csrf(c)
    o = Q(app, "SELECT * FROM orders WHERE status='Completed' AND form_key IS NOT NULL LIMIT 1", one=True)
    r = c.post(f"/orders/{o['id']}/reorder", data={"csrf_token": t})
    n = Q(app, "SELECT * FROM orders ORDER BY id DESC LIMIT 1", one=True)
    assert n["id"] != o["id"] and n["status"] == "Draft" and n["form_key"] == o["form_key"] and n["po"] == ""
    from orderapp.db import loads
    assert loads(n["data"])["blocks"] == loads(o["data"])["blocks"]
    assert f"order={n['id']}" in r.headers["Location"]


def test_holds_aging_csv_and_exposure(app):
    c = app.test_client()
    t = _csrf(c)
    csv = ("Customer,Current,1-30,31-60,61-90,Over 90,Total,Rep,Last Payment\n"
           "Mesquite Flats,0,0,500.00,250.00,0,750.00,DR,2026-07-01\n"
           "Unknown Place LLC,0,100,0,0,0,100.00,DR,\n")
    c.post("/holds/import", data={"csrf_token": t, "report": csv})
    h = Q(app, "SELECT * FROM account_holds WHERE name='Mesquite Flats' AND status='hold'", one=True)
    assert h and h["balance"] == 750 and h["days_late"] == 61 and h["last_payment"] == "2026-07-01" and h["customer_id"]
    with app.app_context():
        from orderapp import holds as H
        ex = H.exposure()
        assert h["id"] in ex
    assert b"Mesquite Flats" in c.get("/holds").data


def test_delivery_list(app):
    from orderapp import deliveries as DL
    c = app.test_client()
    t = _csrf(c)
    with app.app_context():
        from orderapp.db import x
        x("UPDATE orders SET status='Shipped', po='PO-7777', vendor_ref='SO-123456' WHERE id=(SELECT MIN(id) FROM orders "
          "WHERE status NOT IN ('Delivered','Completed','Installed','Cancelled','Draft'))")
        o = Q(app, "SELECT o.*, c.name cname FROM orders o JOIN customers c ON c.id=o.customer_id WHERE po='PO-7777'", one=True)
        res = DL.match("Truck 1\n 1. Somewhere  po# 7777  2 boxes\n 2. Other place  SO 99999\n")
        assert [r["order_id"] for r in res if r["sure"]] == [o["id"]]
        res = DL.match(f"3   {o['cname']}   (no po)")
        assert res and not any(r["sure"] for r in res)
    c.post("/deliveries", data={"csrf_token": t, "text": "Deliveries today\nSO-123456   blinds"})
    chk = Q(app, "SELECT * FROM delivery_checks ORDER BY id DESC LIMIT 1", one=True)
    assert Q(app, "SELECT status FROM orders WHERE id=?", (o["id"],), one=True)["status"] == "Shipped"   # pasted = confirm
    c.post("/deliveries", data={"csrf_token": t, "apply": "1", "check_id": chk["id"], "order_id": [str(o["id"])]})
    assert Q(app, "SELECT status FROM orders WHERE id=?", (o["id"],), one=True)["status"] == "Delivered"
    assert c.get("/deliveries").status_code == 200
    assert DL.attachment_text([("list.csv", b"PO,Customer\nPO-1,A\n")]).startswith("PO,Customer")
