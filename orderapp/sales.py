"""Sales history: invoices + line items imported from the company system's CSV exports.
Reports by month / year, customer and product, plus monthly goals and commission."""
import csv
import io
import re
from datetime import date, datetime

from .db import find_customer, now, q, setting, x

SALES_SCHEMA = """
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY, invoice_no TEXT, invoice_date TEXT NOT NULL,
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL, customer_name TEXT, acct TEXT,
    po TEXT, rep TEXT, total REAL DEFAULT 0, cost REAL, batch TEXT, created TEXT
);
CREATE TABLE IF NOT EXISTS sales_lines (
    id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
    category TEXT, item_no TEXT, description TEXT, qty REAL, unit_price REAL, amount REAL, cost REAL
);
CREATE TABLE IF NOT EXISTS sales_goals (
    period TEXT PRIMARY KEY, goal REAL, rate REAL
);
CREATE INDEX IF NOT EXISTS ix_sales_date ON sales(invoice_date);
CREATE INDEX IF NOT EXISTS ix_sales_cust ON sales(customer_id);
CREATE INDEX IF NOT EXISTS ix_lines_sale ON sales_lines(sale_id);
"""

# product categories (match the app's products, grouped the way the brochure groups them)
CATEGORIES = ["Blinds & Shades", "Doors", "Closet & Wardrobe Doors", "Patio Doors", "Screens",
              "Baseboard & Casing", "Door Hardware", "Bath Hardware", "Cabinets", "Countertops",
              "Shower Doors", "Mirror Frames", "Other"]
_KEYWORDS = [   # first match wins - more specific words first
    ("Mirror Frames", r"mirror ?mate|mirror frame"),
    ("Shower Doors", r"shower|enclosure|tub door"),
    ("Closet & Wardrobe Doors", r"bi-?pass|bypass|bi-?fold|closet|wardrobe|mirrored"),
    ("Patio Doors", r"patio door|sliding (glass|patio)|slider"),
    ("Screens", r"screen|suntex|sunscreen"),
    ("Blinds & Shades", r"blind|vertical|shade|valance|slat|headrail|faux wood|basswood|mini"),
    ("Door Hardware", r"knob|lever|lockset|deadbolt|passage|privacy|entry set|hinge|door hardware|"
                      r"marina|soma lever|lombard|sea cliff|tiburon lever"),
    ("Bath Hardware", r"towel|paper holder|tissue|bath hardware|robe hook|waterfront|palo alto|santa cruz"),
    ("Baseboard & Casing", r"baseboard|base board|casing|trim|mdf|moulding|molding|craftsman base"),
    ("Countertops", r"counter ?top|laminate top|thinscape|backsplash"),
    ("Cabinets", r"cabinet|vanity|drawer|thermofoil|shaker|italia|bradford|elegante"),
    ("Doors", r"door|pre-?hung|slab|panel|primecoat|hollow core|solid core|jamb"),
]
_CAT_RE = [(c, re.compile(p, re.I)) for c, p in _KEYWORDS]

# CSV column names we understand (lower-case, spaces/punctuation removed) -> our field
_COLS = {
    "invoice_no": ["invoice", "invoice#", "inv#", "order#", "so#", "invoiceno", "invoicenumber", "inv", "invno", "order", "orderno", "ordernumber",
                   "salesorder", "so", "sono", "document", "docno", "ticket"],
    "invoice_date": ["date", "invoicedate", "orderdate", "invdate", "shipdate", "posted", "postdate"],
    "customer_name": ["customer", "customername", "property", "propertyname", "account", "accountname",
                      "billto", "shipto", "name", "soldto"],
    "acct": ["acct", "acctno", "account#", "accountno", "accountnumber", "customerno", "customernumber",
             "custno", "customerid", "custid"],
    "po": ["po", "po#", "pono", "ponumber", "purchaseorder", "customerpo"],
    "rep": ["rep", "salesrep", "salesperson", "slsp", "salesman"],
    "category": ["category", "productcategory", "productline", "line", "class", "productclass", "group",
                 "productgroup", "dept", "department"],
    "item_no": ["item", "itemno", "item#", "sku#", "itemnumber", "sku", "partno", "part", "product", "productcode",
                "code"],
    "description": ["description", "desc", "itemdescription", "productdescription", "productname", "itemname"],
    "qty": ["qty", "quantity", "qtyshipped", "shipped", "qtyordered", "units"],
    "unit_price": ["price", "unitprice", "each", "sellprice", "rate"],
    "amount": ["amount", "ext", "total", "extended", "extprice", "extendedprice", "lineamount", "linetotal", "sales",
               "net", "netamount", "extamount", "revenue"],
    "cost": ["cost", "extcost", "extendedcost", "totalcost", "linecost", "cogs"],
}
FIELD_LABELS = {"invoice_no": "Invoice / order #", "invoice_date": "Date", "customer_name": "Customer / property",
                "acct": "Account #", "po": "PO #", "rep": "Sales rep", "category": "Product category",
                "item_no": "Item #", "description": "Description", "qty": "Qty", "unit_price": "Unit price",
                "amount": "Line total ($)", "cost": "Cost ($)"}


def init(con):
    con.executescript(SALES_SCHEMA)


def _key(h):
    return re.sub(r"[^a-z0-9#]", "", (h or "").lower())


def guess_mapping(headers):
    """{field: header} - best guess from the column names."""
    keys = {_key(h): h for h in headers}
    out = {}
    for field, names in _COLS.items():
        for n in names:
            if n in keys and keys[n] not in out.values():
                out[field] = keys[n]
                break
    return out


def category_for(*texts):
    t = " ".join(str(s or "") for s in texts)
    for c in CATEGORIES:                       # a column that already says the category
        if t.strip().lower() == c.lower():
            return c
    for c, rx in _CAT_RE:
        if rx.search(t):
            return c
    return "Other"


def _num(v):
    s = str(v or "").strip().replace("$", "").replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


def _date(v):
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%m-%d-%Y", "%d-%b-%Y", "%b %d, %Y",
                "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def read_csv(text):
    text = text.lstrip("﻿")
    rows = list(csv.DictReader(io.StringIO(text)))
    headers = list(rows[0].keys()) if rows else next(csv.reader(io.StringIO(text)), [])
    return headers, rows


def import_rows(rows, mapping, batch=None):
    """One CSV row = one line item. Rows sharing an invoice # become one invoice.
    Re-importing the same invoice replaces it (no duplicates). Returns a summary dict."""
    batch = batch or now()
    g = lambda r, f: (r.get(mapping[f]) if mapping.get(f) else "") or ""   # noqa: E731
    invoices, skipped = {}, 0
    for i, r in enumerate(rows):
        d = _date(g(r, "invoice_date"))
        amount = _num(g(r, "amount"))
        qty, price = _num(g(r, "qty")), _num(g(r, "unit_price"))
        if amount is None and qty is not None and price is not None:
            amount = round(qty * price, 2)
        if not d or amount is None:
            skipped += 1
            continue
        inv = g(r, "invoice_no").strip() or f"row{i + 1}-{d}"
        name = g(r, "customer_name").strip()
        key = (inv, name.lower())
        h = invoices.setdefault(key, {"invoice_no": inv, "invoice_date": d, "customer_name": name,
                                      "acct": g(r, "acct").strip(), "po": g(r, "po").strip(),
                                      "rep": g(r, "rep").strip(), "lines": []})
        desc = g(r, "description").strip()
        item = g(r, "item_no").strip()
        cat = g(r, "category").strip()
        h["lines"].append({"category": category_for(cat) if cat and category_for(cat) != "Other" else
                           category_for(desc, item, cat),
                           "item_no": item, "description": desc, "qty": qty, "unit_price": price,
                           "amount": amount, "cost": _num(g(r, "cost"))})
    added = replaced = 0
    matched, unmatched = 0, set()
    for h in invoices.values():
        c = find_customer(name=h["customer_name"], acct=h["acct"])
        cid = c["id"] if c else None
        if cid:
            matched += 1
        else:
            unmatched.add(h["customer_name"] or "(blank)")
        old = q("SELECT id FROM sales WHERE invoice_no=? AND lower(coalesce(customer_name,''))=?",
                (h["invoice_no"], h["customer_name"].lower()), one=True)
        if old:
            x("DELETE FROM sales WHERE id=?", (old["id"],))
            replaced += 1
        else:
            added += 1
        total = round(sum(ln["amount"] for ln in h["lines"]), 2)
        costs = [ln["cost"] for ln in h["lines"] if ln["cost"] is not None]
        sid = x("""INSERT INTO sales(invoice_no, invoice_date, customer_id, customer_name, acct, po, rep, total,
                   cost, batch, created) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (h["invoice_no"], h["invoice_date"], cid, h["customer_name"] or (c["name"] if c else ""),
                 h["acct"], h["po"], h["rep"], total, round(sum(costs), 2) if costs else None, batch, now()))
        for ln in h["lines"]:
            x("""INSERT INTO sales_lines(sale_id, category, item_no, description, qty, unit_price, amount, cost)
                 VALUES (?,?,?,?,?,?,?,?)""",
              (sid, ln["category"], ln["item_no"], ln["description"], ln["qty"], ln["unit_price"], ln["amount"],
               ln["cost"]))
    return {"invoices": len(invoices), "added": added, "replaced": replaced, "skipped_rows": skipped,
            "matched": matched, "unmatched": sorted(unmatched)}


def relink_customers():
    """Link imported invoices to customers added later (by account # or name)."""
    n = 0
    for s in q("SELECT id, customer_name, acct FROM sales WHERE customer_id IS NULL"):
        c = find_customer(name=s["customer_name"], acct=s["acct"])
        if c:
            x("UPDATE sales SET customer_id=? WHERE id=?", (c["id"], s["id"]))
            n += 1
    return n


# ------------------------------------------------------------------ reports
def _where(year=None, customer_id=None, category=None, alias="s"):
    w, a = ["1=1"], []
    if year:
        w.append(f"substr({alias}.invoice_date,1,4)=?")
        a.append(str(year))
    if customer_id:
        w.append(f"{alias}.customer_id=?")
        a.append(customer_id)
    if category:
        w.append(f"{alias}.id IN (SELECT sale_id FROM sales_lines WHERE category=?)")
        a.append(category)
    return " AND ".join(w), a


def years():
    ys = [r["y"] for r in q("SELECT DISTINCT substr(invoice_date,1,4) y FROM sales ORDER BY y DESC")]
    return ys or [str(date.today().year)]


def has_data():
    return bool(q("SELECT 1 FROM sales LIMIT 1", one=True))


def monthly(year, customer_id=None, category=None):
    """[12 totals] for the year (line amounts, so a category filter is exact)."""
    w, a = _where(year, customer_id, None)
    cat_sql = " AND l.category=?" if category else ""
    rows = q(f"""SELECT substr(s.invoice_date,6,2) m, SUM(l.amount) t FROM sales s JOIN sales_lines l
                 ON l.sale_id=s.id WHERE {w}{cat_sql} GROUP BY m""", a + ([category] if category else []))
    out = [0.0] * 12
    for r in rows:
        out[int(r["m"]) - 1] = round(r["t"] or 0, 2)
    return out


def by_category(year, customer_id=None, upto=None):
    w, a = _where(year, customer_id)
    if upto:
        w += " AND s.invoice_date<=?"
        a.append(upto)
    return [dict(r) for r in q(f"""SELECT l.category, SUM(l.amount) total, COUNT(DISTINCT s.id) invoices
                                   FROM sales s JOIN sales_lines l ON l.sale_id=s.id WHERE {w}
                                   GROUP BY l.category ORDER BY total DESC""", a)]


def by_customer(year, category=None, upto=None, limit=200):
    w, a = _where(year, None, None)
    if upto:
        w += " AND s.invoice_date<=?"
        a.append(upto)
    cat_sql = " AND l.category=?" if category else ""
    return [dict(r) for r in q(f"""SELECT s.customer_id, coalesce(c.name, s.customer_name) name,
            SUM(l.amount) total, COUNT(DISTINCT s.id) invoices, MAX(s.invoice_date) last
            FROM sales s JOIN sales_lines l ON l.sale_id=s.id LEFT JOIN customers c ON c.id=s.customer_id
            WHERE {w}{cat_sql} GROUP BY coalesce(s.customer_id, 'n:' || lower(s.customer_name))
            ORDER BY total DESC LIMIT ?""", a + ([category] if category else []) + [limit])]


def total(year, customer_id=None, category=None, upto=None, frm=None):
    w, a = _where(year, customer_id)
    if upto:
        w += " AND s.invoice_date<=?"
        a.append(upto)
    if frm:
        w += " AND s.invoice_date>=?"
        a.append(frm)
    cat_sql = " AND l.category=?" if category else ""
    r = q(f"SELECT SUM(l.amount) t FROM sales s JOIN sales_lines l ON l.sale_id=s.id WHERE {w}{cat_sql}",
          a + ([category] if category else []), one=True)
    return round(r["t"] or 0, 2)


def goals(year):
    """[{period, goal, rate, sales, pct, commission}] for each month of the year."""
    default_rate = float(setting("commission_rate") or 0)
    default_goal = float(setting("monthly_goal") or 0)
    saved = {r["period"]: r for r in q("SELECT * FROM sales_goals WHERE period LIKE ?", (f"{year}-%",))}
    sales = monthly(year)
    basis = setting("commission_basis") or "sales"
    margin = {}
    if basis == "margin":
        for r in q("""SELECT substr(invoice_date,6,2) m, SUM(total - coalesce(cost, total)) gm FROM sales
                      WHERE substr(invoice_date,1,4)=? GROUP BY m""", (str(year),)):
            margin[int(r["m"])] = r["gm"] or 0
    out = []
    for m in range(1, 13):
        p = f"{year}-{m:02d}"
        s = saved.get(p)
        goal = s["goal"] if s and s["goal"] is not None else default_goal
        rate = s["rate"] if s and s["rate"] is not None else default_rate
        amt = sales[m - 1]
        base = margin.get(m, 0) if basis == "margin" else amt
        out.append({"period": p, "month": m, "goal": goal, "rate": rate, "sales": amt,
                    "pct": round(amt / goal * 100) if goal else None,
                    "commission": round(base * rate / 100, 2), "custom": bool(s)})
    return out


def customer_summary(customer_id):
    if not customer_id:
        return None
    y = date.today().year
    today = date.today().isoformat()
    ly_same_day = date.today().replace(year=y - 1).isoformat() if not (date.today().month == 2 and
                                                                       date.today().day == 29) else f"{y - 1}-02-28"
    last = q("SELECT MAX(invoice_date) d, COUNT(*) n FROM sales WHERE customer_id=?", (customer_id,), one=True)
    if not last or not last["n"]:
        return None
    ytd = total(y, customer_id, upto=today)
    lytd = total(y - 1, customer_id, upto=ly_same_day)
    return {"ytd": ytd, "lytd": lytd, "last_year": total(y - 1, customer_id), "last": last["d"],
            "invoices": last["n"], "cats": by_category(y, customer_id) or by_category(y - 1, customer_id),
            "change": round((ytd - lytd) / lytd * 100) if lytd else None}


def gaps(year, limit=40):
    """Customers who bought last year but not yet this year, and categories each customer dropped."""
    gone = [dict(r) for r in q("""SELECT coalesce(c.name, s.customer_name) name, s.customer_id,
            SUM(s.total) ly, MAX(s.invoice_date) last FROM sales s LEFT JOIN customers c ON c.id=s.customer_id
            WHERE substr(s.invoice_date,1,4)=? GROUP BY coalesce(s.customer_id, lower(s.customer_name))""",
                                 (str(int(year) - 1),))]
    this = {(r["customer_id"] or r["n"]) for r in q("""SELECT customer_id, lower(customer_name) n FROM sales
            WHERE substr(invoice_date,1,4)=?""", (str(year),))}
    out = [g for g in gone if (g["customer_id"] or (g["name"] or "").lower()) not in this]
    return sorted(out, key=lambda g: -g["ly"])[:limit]


def invoices(search="", year=None, customer_id=None, category=None, limit=500):
    w, a = _where(year, customer_id, category)
    if search:
        w += """ AND (s.invoice_no LIKE ? OR s.customer_name LIKE ? OR s.po LIKE ? OR s.id IN
               (SELECT sale_id FROM sales_lines WHERE description LIKE ? OR item_no LIKE ?))"""
        a += [f"%{search}%"] * 5
    return q(f"""SELECT s.*, coalesce(c.name, s.customer_name) cname,
                 (SELECT group_concat(DISTINCT category) FROM sales_lines WHERE sale_id=s.id) cats
                 FROM sales s LEFT JOIN customers c ON c.id=s.customer_id WHERE {w}
                 ORDER BY s.invoice_date DESC, s.id DESC LIMIT ?""", a + [limit])


TEMPLATE_HEADERS = ["Invoice #", "Date", "Customer", "Acct #", "PO #", "Sales Rep", "Item #", "Description",
                    "Category", "Qty", "Unit Price", "Amount", "Cost"]
