"""Account pricing: what each account pays per item.

Price lookup order (most specific first):
  1. Agreed account price  - a price you set for this account (contract / negotiated)
  2. This account's last price paid - from the sales history
  3. Company list price - from the Item Price List report (exact item #)
  4. Latest price anyone paid - from the sales history
"""
import re

from .db import now, q, x

PRICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_prices (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    item_no TEXT, description TEXT, category TEXT, price REAL NOT NULL, notes TEXT, updated TEXT
);
CREATE INDEX IF NOT EXISTS ix_aprice_cust ON account_prices(customer_id);
"""

# app product (measurement) -> sales category
PRODUCT_CATEGORY = {
    "horizontal_blind": "Blinds & Shades", "vertical_blind": "Blinds & Shades", "roller_shade": "Blinds & Shades",
    "interior_door": "Doors", "entry_door": "Doors", "prehung_door": "Doors", "storage_door": "Doors",
    "bypass_door": "Closet & Wardrobe Doors", "closet_door": "Closet & Wardrobe Doors",
    "wardrobe_door": "Closet & Wardrobe Doors", "sliding_patio_door": "Patio Doors",
    "screen_door": "Screens", "window_screen": "Screens", "baseboard": "Baseboard & Casing",
    "casing": "Baseboard & Casing", "door_hardware": "Door Hardware", "bath_hardware": "Bath Hardware",
    "cabinet": "Cabinets", "cabinet_door": "Cabinets", "countertop": "Countertops", "shower_door": "Shower Doors",
    "mirror_frame": "Mirror Frames",
}


_STOP = {"the", "and", "with", "for", "per", "ea", "each", "set", "pc", "pcs", "standard", "custom", "kitchen", "vanity"}


def _words(s):
    t = (s or "").lower().replace("bi-pass", "bypass").replace("bi pass", "bypass")
    t = re.sub(r"(\d)\s*x\s*(\d)", r"\1 \2", t)          # 30x80 -> 30 80
    out = set()
    for w in re.findall(r"[a-z0-9/]+", t):
        if w in _STOP or (len(w) < 2 and not w.isdigit()):
            continue
        out.add(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)    # blinds -> blind
    return out


# product "kinds" that must agree: a vertical blind is never priced like a faux wood blind, an entry door
# never like a hollow-core interior door, a bi-fold never like a bypass
_KINDS = {
    "vertical": {"vertical"}, "horizontal": {"horizontal", "faux", "mini", "basswood", "aluminum", "vinyl", "metal"},
    "roller": {"roller", "shade"}, "entry": {"entry", "exterior", "steel", "pre", "hung", "prehung"},
    "interior": {"interior", "hollow", "slab"}, "bypass": {"bypass", "mirrored"}, "bifold": {"fold", "bifold"},
    "casing": {"casing"}, "patio": {"patio", "slider", "sliding"},
    "laminate": {"laminate"}, "thinscape": {"thinscape"}, "synthetic": {"synthetic", "quartz", "solid"},
}


def _kinds(words):
    return {k for k, ws in _KINDS.items() if words & ws}


def _match_score(a, b):
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0
    ka, kb = _kinds(wa), _kinds(wb)
    if ka and kb and not (ka & kb):
        return 0
    return len(wa & wb) / max(len(wa), 1)


def last_paid(customer_id, item_no=None, description=None, category=None):
    """Most recent line this account bought that matches (item #, else best description match)."""
    if not customer_id:
        return None
    if item_no:
        r = q("""SELECT l.unit_price, l.item_no, l.description, l.category, s.invoice_date FROM sales_lines l
                 JOIN sales s ON s.id=l.sale_id WHERE s.customer_id=? AND l.item_no=? AND l.unit_price IS NOT NULL
                 ORDER BY s.invoice_date DESC LIMIT 1""", (customer_id, item_no), one=True)
        if r:
            return dict(r)
    if description:
        return _best_desc(description, category, "AND s.customer_id=?", [customer_id])
    return None


def latest_any(item_no=None, description=None, category=None):
    if item_no:
        r = q("""SELECT l.unit_price, l.item_no, l.description, l.category, s.invoice_date FROM sales_lines l
                 JOIN sales s ON s.id=l.sale_id WHERE l.item_no=? AND l.unit_price IS NOT NULL
                 ORDER BY s.invoice_date DESC LIMIT 1""", (item_no,), one=True)
        if r:
            return dict(r)
    if description:
        return _best_desc(description, category, "", [])
    return None


def _best_desc(description, category, extra, args):
    cat_sql = " AND l.category=?" if category else ""
    rows = q(f"""SELECT l.unit_price, l.item_no, l.description, l.category, MAX(s.invoice_date) invoice_date
                 FROM sales_lines l JOIN sales s ON s.id=l.sale_id
                 WHERE l.unit_price IS NOT NULL {extra}{cat_sql}
                 GROUP BY l.item_no, l.description ORDER BY invoice_date DESC LIMIT 400""",
             args + ([category] if category else []))
    best, score = None, 0.0
    for r in rows:
        s = _match_score(description, (r["description"] or "") + " " + (r["item_no"] or ""))
        if s > score:
            best, score = r, s
    need = 0.25 if category else 0.34          # same product category already narrows it down
    if best and score >= need:
        d = dict(best)
        d["score"] = score
        return d
    return None


def agreed(customer_id, item_no=None, description=None):
    if not customer_id:
        return None
    if item_no:
        r = q("SELECT * FROM account_prices WHERE customer_id=? AND item_no=? ORDER BY id DESC LIMIT 1",
              (customer_id, item_no), one=True)
        if r:
            return dict(r)
    if description:
        best, score = None, 0.0
        for r in q("SELECT * FROM account_prices WHERE customer_id=?", (customer_id,)):
            s = _match_score(description, (r["description"] or "") + " " + (r["item_no"] or ""))
            if s > score:
                best, score = r, s
        if best and score >= 0.6:
            return dict(best)
    return None


def price_for(customer_id, item_no=None, description=None, category=None):
    """{"price", "source", "date", "item_no", "description"} or None."""
    a = agreed(customer_id, item_no, description)
    if a:
        return {"price": a["price"], "source": "Account price", "date": (a["updated"] or "")[:10],
                "item_no": a["item_no"], "description": a["description"]}
    lp = last_paid(customer_id, item_no, description, category)
    la = latest_any(item_no, description, category)
    if lp and la and "score" in lp and ("score" not in la or la["score"] > lp["score"] + 0.15):
        lp = None            # this account only bought something loosely similar; another account bought the match
    if lp:
        return {"price": lp["unit_price"], "source": "Last paid", "date": lp["invoice_date"],
                "item_no": lp["item_no"], "description": lp["description"]}
    co = _company_last_paid(customer_id, item_no)
    if co:
        return {"price": co["price"], "source": "Last paid (company data)", "date": co["doc_date"],
                "item_no": item_no, "description": co["memo"]}
    it = _list_item(item_no) or (_list_item(la["item_no"]) if la and la.get("item_no") else None)
    if it and it.get("list_price"):
        return {"price": it["list_price"], "source": "List price", "date": (it.get("price_updated") or "")[:10],
                "item_no": it["item_no"], "description": (la or {}).get("description") or it["description"]}
    if not la and item_no:
        from .company_db import latest_price
        cl = latest_price(item_no)
        if cl:
            return {"price": cl["price"], "source": "Latest price (company data)", "date": cl["doc_date"],
                    "item_no": item_no, "description": cl["memo"]}
    if la:
        return {"price": la["unit_price"], "source": "Latest price", "date": la["invoice_date"],
                "item_no": la["item_no"], "description": la["description"]}
    return None


def _company_last_paid(customer_id, item_no):
    """This account's last price for the item in Company Data (all reps' invoices)."""
    if not customer_id or not item_no:
        return None
    from .company_db import company_key
    c = q("SELECT name FROM customers WHERE id=?", (customer_id,), one=True)
    key = company_key(c["name"]) if c else None
    if not key:
        return None
    r = q("""SELECT l.price, l.memo, d.doc_date FROM co_lines l JOIN co_docs d ON d.id=l.doc_id
             WHERE d.cust_key=? AND l.item_no=? AND l.price IS NOT NULL AND l.price>0 AND d.doc_type='Invoice'
             ORDER BY d.doc_date DESC LIMIT 1""", (key, str(item_no).strip()), one=True)
    return dict(r) if r else None


def _list_item(item_no):
    if not item_no:
        return None
    r = q("SELECT * FROM items WHERE item_no=?", (str(item_no).strip(),), one=True)
    return dict(r) if r else None


def search_items(term, customer_id=None, limit=15):
    """Items for the quote picker - sales history first, then the company price list - with this account's
    price and stock on hand."""
    from .company_reports import stock_text
    like = f"%{term}%"
    rows = [dict(r) for r in q("""SELECT l.item_no, l.description, l.category, MAX(s.invoice_date) last
                FROM sales_lines l JOIN sales s ON s.id=l.sale_id WHERE l.item_no LIKE ? OR l.description LIKE ?
                GROUP BY l.item_no, l.description ORDER BY last DESC LIMIT ?""", (like, like, limit))]
    have = {r["item_no"] for r in rows if r["item_no"]}
    if len(rows) < limit:
        rows += [dict(r) for r in q("""SELECT item_no, description, category FROM items
                 WHERE (item_no LIKE ? OR description LIKE ?) AND description <> ''
                 ORDER BY (last_sold IS NULL), last_sold DESC LIMIT ?""", (like, like, limit))
                 if r["item_no"] not in have][:limit - len(rows)]
    out = []
    for r in rows:
        p = price_for(customer_id, r["item_no"], r["description"], r["category"])
        out.append({"item_no": r["item_no"] or "", "description": r["description"] or "",
                    "category": r["category"] or "", "price": p["price"] if p else None,
                    "source": p["source"] if p else "", "date": p["date"] if p else "",
                    "stock": stock_text(_list_item(r["item_no"]))})
    return out


def price_book(customer_id):
    """Everything this account has bought: last price, when, how often, low-high, plus agreed prices."""
    rows = q("""SELECT l.item_no, l.description, l.category, COUNT(*) times, SUM(l.qty) qty,
                MIN(l.unit_price) low, MAX(l.unit_price) high, MAX(s.invoice_date) last
                FROM sales_lines l JOIN sales s ON s.id=l.sale_id WHERE s.customer_id=? AND l.unit_price IS NOT NULL
                GROUP BY l.item_no, l.description ORDER BY l.category, l.description""", (customer_id,))
    out = []
    for r in rows:
        d = dict(r)
        lp = last_paid(customer_id, r["item_no"]) if r["item_no"] else last_paid(customer_id, None, r["description"])
        d["last_price"] = lp["unit_price"] if lp else None
        anyp = latest_any(r["item_no"]) if r["item_no"] else None
        d["latest_any"] = anyp["unit_price"] if anyp else None
        a = agreed(customer_id, r["item_no"]) if r["item_no"] else None
        d["agreed"] = a["price"] if a else None
        d["agreed_id"] = a["id"] if a else None
        out.append(d)
    have = {(d["item_no"] or "") for d in out}
    extra = [dict(r) for r in q("SELECT * FROM account_prices WHERE customer_id=? ORDER BY description", (customer_id,))
             if (r["item_no"] or "") not in have or not r["item_no"]]
    return out, extra


def set_agreed(customer_id, item_no, description, category, price, notes=""):
    old = q("SELECT id FROM account_prices WHERE customer_id=? AND coalesce(item_no,'')=? AND "
            "coalesce(description,'')=?", (customer_id, item_no or "", description or ""), one=True)
    if price is None:
        if old:
            x("DELETE FROM account_prices WHERE id=?", (old["id"],))
        return
    if old:
        x("UPDATE account_prices SET price=?, category=?, notes=?, updated=? WHERE id=?",
          (price, category, notes, now(), old["id"]))
    else:
        x("""INSERT INTO account_prices(customer_id, item_no, description, category, price, notes, updated)
             VALUES (?,?,?,?,?,?,?)""", (customer_id, item_no, description, category, price, notes, now()))
