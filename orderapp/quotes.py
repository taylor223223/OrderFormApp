"""Quotes / estimates: priced from the account's own price history, built by hand, from a past invoice,
or from a floorplan's measurements x number of units (a "turn kit"). PDF + email draft; approved quotes
become draft orders."""
import os
from datetime import date, timedelta

import fitz

from . import pricing as P
from .catalog import FORMS, PRODUCTS
from .db import add_status, dumps, loads, now, q, setting, x

QUOTE_SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY, number TEXT, customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    title TEXT, status TEXT DEFAULT 'Draft', valid_until TEXT, notes TEXT, terms TEXT, source TEXT,
    floorplan_id INTEGER, units INTEGER, pdf_path TEXT, sent_at TEXT, decided_at TEXT, order_ids TEXT,
    created TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS quote_lines (
    id INTEGER PRIMARY KEY, quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE, position INTEGER,
    item_no TEXT, description TEXT, category TEXT, qty REAL, unit TEXT, unit_price REAL, price_source TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS ix_qlines ON quote_lines(quote_id);
"""
QUOTE_STATUSES = ["Draft", "Sent", "Approved", "Ordered", "Lost"]


def _fmt_size(d):
    w, h, ln = d.get("width"), d.get("height"), d.get("length")
    if w and h:
        return f'{w}" x {h}"'
    if w:
        return f'{w}"'
    if ln:
        return f"{ln}"
    return ""


def describe_measurement(product, room, d):
    label = PRODUCTS.get(product, {}).get("label", product.replace("_", " ").title())
    if d.get("closet_type"):
        label = f"Closet door {d['closet_type']}"
    bits = [label]
    size = _fmt_size(d) if product not in ("baseboard", "casing") else ""
    if size:
        bits.append(size)
    for k in ("style", "cab_kind", "material", "color", "finish", "frame_finish", "ct_material", "slat_size"):
        if d.get(k) and str(d[k]) not in bits:
            bits.append(str(d[k]))
    s = " ".join(bits)
    return f"{s} - {room}" if room else s


def number_for(qid):
    return f"Q-{date.today().year}-{qid:04d}"


def create(customer_id, title="", source="manual", floorplan_id=None, units=None):
    days = int(setting("quote_valid_days") or 30)
    qid = x("""INSERT INTO quotes(customer_id, title, status, valid_until, terms, source, floorplan_id, units, created,
               updated) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (customer_id, title, "Draft", (date.today() + timedelta(days=days)).isoformat(),
             setting("quote_terms") or "", source, floorplan_id, units, now(), now()))
    x("UPDATE quotes SET number=? WHERE id=?", (number_for(qid), qid))
    return qid


def add_line(qid, description, qty=1, unit="ea", item_no="", category="", unit_price=None, source="", meta=None):
    pos = (q("SELECT coalesce(MAX(position),0)+1 n FROM quote_lines WHERE quote_id=?", (qid,), one=True) or {"n": 1})["n"]
    return x("""INSERT INTO quote_lines(quote_id, position, item_no, description, category, qty, unit, unit_price,
                price_source, meta) VALUES (?,?,?,?,?,?,?,?,?,?)""",
             (qid, pos, item_no, description, category, qty, unit, unit_price, source, dumps(meta) if meta else None))


def priced(customer_id, description, category, item_no=None):
    p = P.price_for(customer_id, item_no, description, category)
    if not p:
        return None, "", item_no or ""
    return p["price"], f"{p['source']} {p['date'] or ''}".strip(), item_no or p.get("item_no") or ""


def add_floorplan(qid, customer_id, floorplan_id, units=1):
    """Turn kit: every measurement saved for the floorplan x the number of units."""
    units = max(1, int(units or 1))
    rows = q("""SELECT * FROM measurements WHERE floorplan_id=? AND unit_id IS NULL ORDER BY product, room""",
             (floorplan_id,))
    n = 0
    for m in rows:
        d = loads(m["data"]) or {}
        prod = m["product"]
        cat = P.PRODUCT_CATEGORY.get(prod, "Other")
        desc = describe_measurement(prod, m["room"], d)
        if prod in ("baseboard", "casing"):
            try:
                feet = float(d.get("length") or 0)
            except ValueError:
                feet = 0
            qty, unit = round(feet * units, 1), "ft"
            price, src, item = priced(customer_id, f"{PRODUCTS[prod]['label']} per ft", cat)
        else:
            try:
                each = float(d.get("qty") or 1)
            except ValueError:
                each = 1
            qty, unit = each * units, "ea"
            price, src, item = priced(customer_id, describe_measurement(prod, None, d), cat)
        add_line(qid, desc, qty, unit, item, cat, price, src,
                 {"product": prod, "room": m["room"], "data": d, "per_unit": qty / units})
        n += 1
    return n


def add_invoice(qid, customer_id, sale_id):
    """Re-quote a past invoice at today's prices for the account."""
    n = 0
    for ln in q("SELECT * FROM sales_lines WHERE sale_id=? ORDER BY id", (sale_id,)):
        price, src, item = priced(customer_id, ln["description"], ln["category"], ln["item_no"])
        add_line(qid, ln["description"] or ln["item_no"], ln["qty"] or 1, "ea", item, ln["category"],
                 price if price is not None else ln["unit_price"], src or "Invoice price")
        n += 1
    return n


def lines(qid):
    return [dict(r) for r in q("SELECT * FROM quote_lines WHERE quote_id=? ORDER BY position, id", (qid,))]


def totals(qid):
    ls = lines(qid)
    sub = round(sum((ln["qty"] or 0) * (ln["unit_price"] or 0) for ln in ls), 2)
    return {"subtotal": sub, "count": len(ls), "missing": sum(1 for ln in ls if ln["unit_price"] is None)}


def get(qid):
    return q("""SELECT qu.*, c.name cname, c.address, c.city, c.state, c.zip, c.mgmt, c.phone, c.email, c.acct
                FROM quotes qu LEFT JOIN customers c ON c.id=qu.customer_id WHERE qu.id=?""", (qid,), one=True)


def set_status(qid, status):
    if status not in QUOTE_STATUSES:
        return
    extra = ", decided_at=?" if status in ("Approved", "Lost") else ""
    args = [status, now()] + ([now()] if extra else []) + [qid]
    x(f"UPDATE quotes SET status=?, updated=?{extra} WHERE id=?", args)


# ------------------------------------------------------------------ approved quote -> draft orders
def to_orders(qid):
    """Lines built from measurements become blocks on the right company form (one draft order per form).
    Returns [order ids]."""
    from .routes.orders import customer_header
    from .suggest import to_form_block
    qu = get(qid)
    c = q("SELECT * FROM customers WHERE id=?", (qu["customer_id"],), one=True) if qu["customer_id"] else None
    by_form = {}
    other = []
    for ln in lines(qid):
        meta = loads(ln["meta"]) if ln["meta"] else None
        prod = (meta or {}).get("product")
        forms = [f for f in PRODUCTS.get(prod, {}).get("forms", []) if f in FORMS] if prod else []
        if not forms:
            other.append(ln)
            continue
        fk = forms[0]
        blk = to_form_block(fk, meta.get("data") or {}, meta.get("room"))
        if "qty" in {f["key"] for f in FORMS[fk]["blocks"][0]} and prod not in ("baseboard", "casing"):
            blk["qty"] = f"{ln['qty']:g}"
        if prod in ("baseboard", "casing"):
            blk["length"] = f"{ln['qty']:g}"
        by_form.setdefault(fk, []).append(blk)
    ids = []
    hdr = customer_header(c) if c else {}
    hdr["date"] = date.today().strftime(setting("date_format") or "%m/%d/%Y")
    for fk, blocks in by_form.items():
        data = {"header": dict(hdr), "blocks": blocks, "sales_rep": setting("sales_rep")}
        oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, notes,
                   created, updated) VALUES (?,?,?,?,?,'Draft',?,?,?,?,?)""",
                (qu["customer_id"], fk, FORMS[fk]["title"], "", date.today().isoformat(), dumps(data), "quote",
                 f"From quote {qu['number']}", now(), now()))
        add_status(oid, "Draft", f"From quote {qu['number']}")
        ids.append(oid)
    x("UPDATE quotes SET order_ids=?, status='Ordered', updated=? WHERE id=?",
      (",".join(map(str, ids)), now(), qid))
    return ids, other


# ------------------------------------------------------------------ PDF
def make_pdf(qid, out_dir):
    from .manager_forms import COMPANY, DARK, NAVY, Page, logo
    qu = get(qid)
    ls = lines(qid)
    t = totals(qid)
    doc = fitz.open()
    src = fitz.open(COMPANY)
    pages = []
    L, R = 36, 576

    def new_page(first):
        pg = Page(doc.new_page(width=612, height=792))
        if first:
            pg.text(R - fitz.get_text_length("QUOTE", "hebo", 24), 52, "QUOTE", 24, bold=True, color=NAVY)
            y = 72
            for lab, val in (("Quote #", qu["number"]), ("Date", (qu["created"] or "")[:10]),
                             ("Valid until", qu["valid_until"] or ""), ("Sales rep", setting("sales_rep") or "")):
                pg.text(400, y, lab, 8.5, bold=True, color=DARK)
                pg.text(470, y, str(val), 9)
                y += 13
            pg.rect((L, 132, 300, 210), color=(0.8, 0.8, 0.8))
            pg.text(L + 8, 146, "PREPARED FOR", 7.5, bold=True, color=DARK)
            yy = 160
            for s in (qu["cname"] or "", qu["mgmt"] or "", qu["address"] or "",
                      " ".join(v for v in (qu["city"], qu["state"], qu["zip"]) if v),
                      f"Acct # {qu['acct']}" if qu["acct"] else ""):
                if s.strip():
                    pg.text(L + 8, yy, s, 9.5 if yy == 160 else 8.5, bold=yy == 160)
                    yy += 12
            if qu["title"]:
                pg.text(320, 160, qu["title"], pg.fit(qu["title"], R - 320, 11, True), bold=True)
            return pg, 228
        return pg, 50

    def table_head(pg, y):
        pg.rect((L, y - 11, R, y + 4), color=None, fill=NAVY)
        for x0, lab in ((L + 6, "ITEM #"), (L + 86, "DESCRIPTION"), (406, "QTY"), (452, "PRICE"), (520, "AMOUNT")):
            pg.text(x0, y, lab, 7.5, bold=True, color=(1, 1, 1))
        return y + 16

    pg, y = new_page(True)
    pages.append(pg)
    y = table_head(pg, y)
    for i, ln in enumerate(ls):
        if y > 700:
            pg, y = new_page(False)
            pages.append(pg)
            y = table_head(pg, y)
        if i % 2:
            pg.rect((L, y - 10, R, y + 4), color=None, fill=(0.96, 0.97, 0.99))
        pg.text(L + 6, y, (ln["item_no"] or "")[:16], 7.5)
        desc = ln["description"] or ""
        pg.text(L + 86, y, desc, pg.fit(desc, 310, 8.5))
        qty = f"{ln['qty']:g}" + (f" {ln['unit']}" if ln["unit"] and ln["unit"] != "ea" else "")
        pg.text(406, y, qty, 8.5)
        if ln["unit_price"] is not None:
            pg.text(452, y, f"${ln['unit_price']:,.2f}", 8.5)
            amt = (ln["qty"] or 0) * ln["unit_price"]
            s = f"${amt:,.2f}"
            pg.text(R - 6 - fitz.get_text_length(s, "helv", 8.5), y, s, 8.5)
        else:
            pg.text(452, y, "TBD", 8.5, color=DARK)
        y += 15
    pg.line((L, y - 6), (R, y - 6), color=(0.6, 0.6, 0.6))
    s = f"${t['subtotal']:,.2f}"
    pg.text(452, y + 8, "TOTAL", 10, bold=True)
    pg.text(R - 6 - fitz.get_text_length(s, "hebo", 11), y + 8, s, 11, bold=True)
    y += 20
    if qu["units"] and qu["floorplan_id"]:
        per = t["subtotal"] / qu["units"] if qu["units"] else 0
        pg.text(L, y + 8, f"Priced for {qu['units']} unit(s) - about ${per:,.2f} per unit, plus tax and delivery "
                          f"where applicable.", 8.5, color=DARK)
        y += 16
    if t["missing"]:
        pg.text(L, y + 8, f"{t['missing']} line(s) marked TBD will be priced on confirmation.", 8, color=DARK)
        y += 14
    for block in (qu["notes"], qu["terms"]):
        if block:
            y = pg.wrap(L, y + 14, block.replace("\n", " "), R - L, 8, 10)
    total = len(pages)
    for n, p in enumerate(pages):
        p.p = doc[n]
        p.text(L, 772, "Apartment Interior Supply  ·  5325 S. Kyrene Rd., Suite 103, Tempe, AZ 85283  ·  "
                       "(480) 964-7600", 7.5, color=(0.35, 0.35, 0.35))
        p.text(R - 50, 772, f"Page {n + 1} of {total}", 7.5, color=(0.35, 0.35, 0.35))
        if n == 0:
            logo(p, src)
        p.finish()
    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(ch if ch not in '\\/:*?"<>|' else "-" for ch in (qu["cname"] or "Quote"))
    path = os.path.join(out_dir, f"{qu['number']} - {safe}.pdf")
    doc.set_metadata({"title": f"Quote {qu['number']}", "creator": "Apartment Interior Supply"})
    doc.save(path, garbage=3, deflate=True)
    doc.close()
    src.close()
    x("UPDATE quotes SET pdf_path=?, updated=? WHERE id=?", (path, now(), qid))
    return path
