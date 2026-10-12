"""Company system (QuickBooks-style) report exports: detection, grouped parsing, rep filter, items table.
All names and numbers here are made up."""
import io
import os
import re
import tempfile

import pytest

os.environ.setdefault("ORDERAPP_DATA", tempfile.mkdtemp())

from orderapp import create_app  # noqa: E402

REP_DETAIL = '''\
,"Type","Date","Num","Memo","Name","Item","Qty","Sales Price","Amount","Balance"
"Pat Example",,,,,,,,,,""
,"Invoice","01/05/2026","500001","35"" x 72"" White 2"" Faux Wood Blind *101","Cactus Wren Villas","21335072 (35"" x 72"" White 2"" Faux Wood Blind (Cordless))",2,50.00,100.00,100.00
,"Invoice","01/05/2026","500001","Fuel Surcharge","Cactus Wren Villas","Fuel Surcharge (Fuel Surcharge)",1,2.50,2.50,102.50
,"Credit Memo","02/01/2026","500050","Return blind","Cactus Wren Villas","21335072 (35"" x 72"" White 2"" Faux Wood Blind (Cordless))",-1,50.00,-50.00,52.50
"Total Pat Example",,,,,,,1,,52.50,52.50
"Taylor Anderson",,,,,,,,,,""
,"Invoice","03/10/2026","500100","2'-6"" x 6'-8"" HC Primecoat Door (LH) *12","Mesa Example Flats","100020 (2'-6"" x 6'-7-1/2"" x 1-3/8"" HC Primecoat Door)",3,80.00,240.00,240.00
,"Invoice","03/10/2026","500100","H/C Door Milling  Charge","Mesa Example Flats","137777 (H/C Door Milling  Charge)",3,10.50,31.50,271.50
,"Invoice","04/02/2026","500200","Vertical blind 96 x 84","Saguaro Ridge Apartments","218055 (36"" x 82-1/2"" White Vertical Blind (Premium Series))",1,30.00,30.00,301.50
"Total Taylor Anderson",,,,,,,7,,301.50,301.50
"TOTAL",,,,,,,8,,354.00,354.00
'''

CUST_DETAIL = '''\
,"Type","Date","Num","Memo","Name","Item","Qty","Sales Price","Amount","Balance"
"Mesa Example Flats",,,,,,,,,,""
,"Invoice","03/10/2026","500100","2'-6"" x 6'-8"" HC Primecoat Door (LH) *12","Mesa Example Flats","100020 (2'-6"" x 6'-7-1/2"" x 1-3/8"" HC Primecoat Door)",3,80.00,240.00,240.00
,"Invoice","03/10/2026","500100","H/C Door Milling  Charge","Mesa Example Flats","137777 (H/C Door Milling  Charge)",3,10.50,31.50,271.50
"Total Mesa Example Flats",,,,,,,6,,271.50,271.50
"Ocotillo Test Homes",,,,,,,,,,""
,"Invoice","05/01/2026","500300","Window screen","Ocotillo Test Homes","Screens (Screens)",4,20.00,80.00,80.00
'''

SHIP_TO = '''\
,"Type","Date","Num","Ship To Address 1","Ship To Address 2","Ship Zip","Item","Qty","Sales Price","Amount"
"Mesa , AZ",,,,,,,,,,
"Mesa Example Flats",,,,,,,,,,
,"Invoice","03/10/2026","500100","Mesa Example Flats","100 E. Example Rd","85201","137777 (H/C Door Milling  Charge)",3,10.50,31.50
"Total Mesa Example Flats",,,,,,,,,,
"Total Mesa , AZ",,,,,,,,,,
"TOTAL",,,,,,,,,,
'''

PRICE_LIST = '''\
,"Item","Description","Preferred Vendor","Price"
,"137777","H/C Door Milling  Charge",,10.50
,"100020","2'-6"" x 6'-7-1/2"" x 1-3/8"" HC Primecoat Door","Example Door Co",82.00
,"21399099","99"" x 99"" White 2"" Faux Wood Blind (Cordless)",,140.00
'''

INVENTORY = '''\
,"Category","Pref Vendor","Reorder Pt (Min)","Max","On Hand","On Sales Order","For Assemblies","Available","Order","On PO","Reorder Qty","Next Deliv","Sales/Week"
"Uncategorized",,,,,,,,,,,,,
"Inventory",,,,,,,,,,,,,
"100020 (2'-6"" x 6'-7-1/2"" x 1-3/8"" HC Primecoat Door)","Uncategorized","Example Door Co",10,40,25,5,0,20,,12,30,"10/20/2026",6.5
"Total Inventory",,,,,25,5,0,20,,12,,,6.5
"TOTAL",,,,,25,5,0,20,,12,,,6.5
'''

TURNOVER = '''\
"Item","Description","Category","Qty Sold","Amount","Turnover Ratio","Turnover Days","Last Sell Date"
"Inventory",,,,,,,
"100020","2'-6"" x 6'-7-1/2"" x 1-3/8"" HC Primecoat Door","Uncategorized",300,24000.00,12,30,"10/01/2026"
'''

ITEM_SUMMARY = '''\
,"Jan 1, '23 - Oct 1, 26"
,"Qty","Amount","% of Sales","Avg Price","COGS","Avg COGS","Gross Margin","Gross Margin %"
"Uncategorized","","",,"","","","",
"100020 (2'-6"" x 6'-7-1/2"" x 1-3/8"" HC Primecoat Door)",300,24000.00,1.0%,80.00,14400.00,48.00,9600.00,40.0%
"TOTAL",300,24000.00,100.0%,80.00,"",48.00,"",
'''

REP_SUMMARY = ''',"Jan 1, '23 - Oct 1, 26"
"Pat Example",52.50
"Taylor Anderson",301.50
"TOTAL",354.00
'''


@pytest.fixture(scope="module")
def app():
    d = tempfile.mkdtemp()
    a = create_app(db_path=os.path.join(d, "c.db"), testing=True, demo=True)    # demo = signed in
    with a.app_context():
        from orderapp.db import set_setting, x
        set_setting("output_dir", os.path.join(d, "out"))
        set_setting("sales_rep", "Taylor M. Anderson")
        x("DELETE FROM sales")
        x("DELETE FROM customers WHERE name IN ('Mesa Example Flats', 'Cactus Wren Villas')")
    return a


def _csrf(c):
    return re.search(rb'name="csrf-token" content="([^"]+)"', c.get("/").data).group(1).decode()


def Q(app, sql, a=(), one=False):
    with app.app_context():
        from orderapp.db import q
        return q(sql, a, one)


def test_detect_and_parse():
    from orderapp import company_reports as CR
    assert CR.detect(REP_DETAIL) == "sales_rep"
    assert CR.detect(CUST_DETAIL) == "sales_customer"
    assert CR.detect(SHIP_TO) == "ship_to"
    assert CR.detect(PRICE_LIST) == "price_list"
    assert CR.detect(INVENTORY) == "inventory"
    assert CR.detect(TURNOVER) == "turnover"
    assert CR.detect(ITEM_SUMMARY) == "item_summary"
    assert CR.detect(REP_SUMMARY, "sales by rep summary.CSV") == "summary_rep"
    assert CR.detect("a,b,c\n1,2,3\n") is None
    assert CR.split_item('130015 (3\'-0" X 6\'-8" Bi-Fold Door Set (Primecoat))') == \
        ("130015", '3\'-0" X 6\'-8" Bi-Fold Door Set (Primecoat)')
    recs = CR.sales_records(REP_DETAIL, "sales_rep")
    assert len(recs) == 6 and recs[0]["rep"] == "Pat Example" and recs[0]["item_no"] == "21335072"
    assert recs[0]["description"].startswith('35" x 72"') and recs[-1]["rep"] == "Taylor Anderson"
    info = CR.describe(REP_DETAIL, "sales_rep")
    assert info["complete"] and info["reps"]["Taylor Anderson"]["total"] == 301.5
    cut = CR.describe(CUST_DETAIL, "sales_customer")       # no TOTAL line = export was cut off
    assert not cut["complete"] and cut["last"] == "2026-05-01"
    assert CR.decode("é".encode("cp1252")) == "é"
    addrs = CR.ship_to_addresses(SHIP_TO)
    a = list(addrs.values())[0]
    assert a["city"] == "Mesa" and a["state"] == "AZ" and a["zip"] == "85201"


def test_import_through_the_app(app):
    c = app.test_client()
    t = _csrf(c)
    files = [(io.BytesIO(s.encode("cp1252")), n) for s, n in
             ((REP_DETAIL, "Sales By Rep Detail.CSV"), (CUST_DETAIL, "Sales By Customer Detail.CSV"),
              (SHIP_TO, "sales by ship to address.CSV"), (PRICE_LIST, "item price list.CSV"),
              (INVENTORY, "inventory status and stock.CSV"), (TURNOVER, "item turnover by item.CSV"),
              (ITEM_SUMMARY, "sales by item summary.CSV"), (REP_SUMMARY, "sales by rep summary.CSV"),
              ("x,y\n1,2\n", "other.csv"))]
    r = c.post("/sales/company", data={"csrf_token": t, "files": files}, content_type="multipart/form-data")
    html = r.data.decode()
    assert r.status_code == 200 and "Cut off" in html and "Taylor Anderson" in html and "other.csv" in html
    token = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)
    assert 'value="Taylor Anderson" checked' in html          # matches the Sales rep setting (Taylor M. Anderson)
    c.post("/sales/company/import", data={"csrf_token": t, "token": token, "rep_filter": "1",
                                          "rep": "Taylor Anderson", "add_customers": "1"})
    s = Q(app, "SELECT COUNT(*) n, SUM(total) t FROM sales", one=True)
    assert s["n"] == 2 and round(s["t"], 2) == 301.5       # only Taylor's; invoice 500100 not doubled
    assert not Q(app, "SELECT 1 FROM sales WHERE invoice_no='500300'")   # customer detail, no rep -> left out
    assert Q(app, "SELECT COUNT(*) n FROM sales_lines l JOIN sales s ON s.id=l.sale_id WHERE invoice_no='500100'",
             one=True)["n"] == 2
    cu = Q(app, "SELECT * FROM customers WHERE name='Mesa Example Flats'", one=True)
    assert cu and cu["zip"] == "85201" and cu["city"] == "Mesa"
    it = Q(app, "SELECT * FROM items WHERE item_no='100020'", one=True)
    assert it["list_price"] == 82 and it["available"] == 20 and it["on_po"] == 12 and it["next_deliv"] == "2026-10-20"
    assert it["last_sold"] == "2026-10-01" and it["avg_cost"] == 48 and it["margin_pct"] == 40
    with app.app_context():
        from orderapp import pricing as P
        from orderapp.db import setting
        assert setting("report_rep") == "Taylor Anderson"
        # this account paid 80 -> last paid wins; a new account gets the list price
        assert P.price_for(cu["id"], "100020")["source"] == "Last paid"
        p = P.price_for(None, "100020")
        assert p["source"] == "List price" and p["price"] == 82
        hits = P.search_items("99\" x 99", None)          # on the price list, never sold
        assert hits and hits[0]["price"] == 140
        assert "20 available" in P.search_items("HC Primecoat", None)[0]["stock"]
    for url in ("/items", "/items?q=primecoat&stock=po", "/items?stock=short", "/sales/import"):
        assert c.get(url).status_code == 200
    assert b"Example Door Co" in c.get("/items?q=100020").data
    # importing again (all reps) updates, never duplicates
    files = [(io.BytesIO(REP_DETAIL.encode()), "rep.csv")]
    html = c.post("/sales/company", data={"csrf_token": t, "files": files},
                  content_type="multipart/form-data").data.decode()
    token = re.search(r'name="token" value="([0-9a-f]+)"', html).group(1)
    c.post("/sales/company/import", data={"csrf_token": t, "token": token})
    s = Q(app, "SELECT COUNT(*) n, SUM(total) t FROM sales", one=True)
    assert s["n"] == 4 and round(s["t"], 2) == 354.0
