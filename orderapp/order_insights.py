"""What a customer has bought before, and what things cost.

- purchases(customer_id): every invoice line for the account (my sales + Company Data, no double counting)
- condensed(customer_id): the same lines rolled up per product + size, with total quantity (Measurements tab)
- previous_for(customer_id, form_key, blocks): earlier orders/invoices of the products on an order form
- estimate_block(customer_id, form_key, block): rough price range from similar past items
- tracking_totals(): conservative dollar estimate of orders put in today / this week / month / year
"""
import re
from datetime import date, timedelta

from .catalog import FORMS, PRODUCTS, products_for_form
from .db import loads, q

# ------------------------------------------------------------------ sizes
_FRAC = r"\d+(?:\s*[- ]\s*\d+/\d+|/\d+)?"
_DIM = rf"(?:(\d+)\s*'\s*-?\s*({_FRAC})?\s*\"?|({_FRAC})\s*(?:\"|''|in\b)?)"
_PAIR = re.compile(rf"{_DIM}\s*[xX×]\s*{_DIM}")


def _num(s):
    s = (s or "").strip()
    if not s:
        return 0.0
    m = re.match(r"^(\d+)\s*[- ]\s*(\d+)/(\d+)$", s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.match(r"^(\d+)/(\d+)$", s)
    if m:
        return int(m.group(1)) / int(m.group(2))
    try:
        return float(s)
    except ValueError:
        return 0.0


def _dim(ft, ftin, inch):
    if ft:
        return int(ft) * 12 + _num(ftin)
    return _num(inch)


def parse_size(text):
    """'2'-6" x 6'-8" Bi-Fold' -> (30.0, 80.0); '59-1/2" x 72" blind' -> (59.5, 72.0); None if no size."""
    m = _PAIR.search(text or "")
    if not m:
        return None
    a = _dim(m.group(1), m.group(2), m.group(3))
    b = _dim(m.group(4), m.group(5), m.group(6))
    if not a or not b:
        return None
    return round(a, 3), round(b, 3)


def fmt_in(v):
    if v is None:
        return ""
    whole = int(v)
    frac = round((v - whole) * 16)
    if frac == 16:
        whole, frac = whole + 1, 0
    if not frac:
        return f'{whole}"'
    from math import gcd
    g = gcd(frac, 16)
    return f'{whole}-{frac // g}/{16 // g}"'


def _clean(desc):
    """Line description without the '*unit / location' note at the end."""
    d = re.split(r"\s\*", desc or "", maxsplit=1)[0]
    return re.sub(r"\s+", " ", d).strip()


def _note(desc):
    m = re.search(r"\s\*(.+)$", desc or "")
    return m.group(1).strip() if m else ""


# ------------------------------------------------------------------ an account's invoice lines
def _company_key(customer_id):
    c = q("SELECT name FROM customers WHERE id=?", (customer_id,), one=True)
    if not c:
        return None
    try:
        from .company_db import company_key
        return company_key(c["name"])
    except Exception:   # noqa: BLE001 - company tables missing on a very old database
        return None


def purchases(customer_id, since=None):
    """[{date, invoice, item_no, desc, qty, price, amount, category, rep, source}] newest first."""
    out, seen = [], set()
    key = _company_key(customer_id)
    if key:
        for r in q("""SELECT d.doc_date, d.num, d.doc_type, d.rep, l.item_no, l.memo, l.item_desc, l.qty, l.price,
                      l.amount, l.category FROM co_lines l JOIN co_docs d ON d.id=l.doc_id
                      WHERE d.cust_key=? AND (? IS NULL OR d.doc_date>=?)
                      ORDER BY d.doc_date DESC, d.id DESC, l.id""", (key, since, since)):
            k = (r["num"], (r["memo"] or "").strip(), r["qty"], round(r["amount"] or 0, 2))
            if k in seen:
                continue
            seen.add(k)
            out.append({"date": r["doc_date"], "invoice": r["num"], "type": r["doc_type"], "rep": r["rep"] or "",
                        "item_no": r["item_no"] or "", "desc": r["memo"] or r["item_desc"] or "",
                        "stock_desc": r["item_desc"] or "", "qty": r["qty"], "price": r["price"],
                        "amount": r["amount"] or 0, "category": r["category"] or "Other", "source": "company"})
    for r in q("""SELECT s.invoice_date, s.invoice_no, s.rep, l.item_no, l.description, l.qty, l.unit_price, l.amount,
                  l.category FROM sales_lines l JOIN sales s ON s.id=l.sale_id
                  WHERE s.customer_id=? AND (? IS NULL OR s.invoice_date>=?)""", (customer_id, since, since)):
        k = (r["invoice_no"], (r["description"] or "").strip(), r["qty"], round(r["amount"] or 0, 2))
        if k in seen:
            continue
        seen.add(k)
        out.append({"date": r["invoice_date"], "invoice": r["invoice_no"], "type": "Invoice", "rep": r["rep"] or "",
                    "item_no": r["item_no"] or "", "desc": r["description"] or "", "stock_desc": "",
                    "qty": r["qty"], "price": r["unit_price"], "amount": r["amount"] or 0,
                    "category": r["category"] or "Other", "source": "sales"})
    out.sort(key=lambda r: (r["date"] or "", r["invoice"] or ""), reverse=True)
    return out


_SKIP = re.compile(r"fuel surcharge|freight|delivery charge|credit card|tariff|discount|retention|labor", re.I)


def condensed(customer_id, category=""):
    """One line per product + ordered size: total quantity, how many orders, units/locations, last price."""
    groups = {}
    for r in purchases(customer_id):
        if _SKIP.search(r["desc"]) or not (r["qty"] or 0):
            continue
        if category and r["category"] != category:
            continue
        base = _clean(r["desc"])
        key = (r["category"], base.lower(), r["item_no"])
        g = groups.get(key)
        if not g:
            size = parse_size(base)
            g = groups[key] = {"category": r["category"], "desc": base, "item_no": r["item_no"],
                               "stock_desc": r["stock_desc"], "width": size[0] if size else None,
                               "height": size[1] if size else None, "qty": 0, "orders": set(), "notes": [],
                               "first": r["date"], "last": r["date"], "last_price": r["price"], "invoices": []}
        g["qty"] += r["qty"] or 0
        g["orders"].add(r["invoice"])
        n = _note(r["desc"])
        if n and n not in g["notes"]:
            g["notes"].append(n)
        if r["date"] and r["date"] < (g["first"] or "9"):
            g["first"] = r["date"]
        if r["invoice"] not in g["invoices"]:
            g["invoices"].append(r["invoice"])
    rows = []
    for g in groups.values():
        if g["qty"] <= 0:
            continue
        g["orders"] = len(g["orders"])
        rows.append(g)
    rows.sort(key=lambda g: (g["category"], -(g["qty"] or 0), g["desc"]))
    return rows


def products_summary(customer_id, limit=8):
    """Top products by quantity for the customer page header."""
    rows = condensed(customer_id)
    return sorted(rows, key=lambda g: (-g["orders"], -g["qty"]))[:limit]


# ------------------------------------------------------------------ order-form helpers
def _product_of(form_key):
    ps = products_for_form(form_key) if form_key else []
    return ps[0] if ps else None


def category_of(form_key):
    from .pricing import PRODUCT_CATEGORY
    return PRODUCT_CATEGORY.get(_product_of(form_key) or "", "")


def describe_block(form_key, blk):
    from .quotes import describe_measurement
    prod = blk.get("product") or _product_of(form_key) or "other"
    d = dict(blk)
    if not d.get("width") and d.get("om_headrail"):
        d["width"], d["height"] = d["om_headrail"], d.get("om_slat") or d.get("height")
    if prod in ("interior_door", "entry_door", "prehung_door", "storage_door", "closet_door", "bypass_door") \
            and blk.get("width") and blk.get("height"):
        d["width"], d["height"] = blk["width"], blk["height"]
    return describe_measurement(prod, "", d)


def _block_size(blk):
    w, h = blk.get("width") or blk.get("om_headrail"), blk.get("height") or blk.get("om_slat") or blk.get("length")
    try:
        sw = _num(str(w).replace('"', "")) if w else None
        sh = _num(str(h).replace('"', "")) if h else None
    except Exception:   # noqa: BLE001
        return None
    if sw and sh:
        return sw, sh
    return None


def _close(a, b, tol=1.0):
    return a and b and abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def previous_for(customer_id, form_key, blocks=None, limit=60):
    """Earlier purchases of this form's product type for the account, newest first, with a 'match' flag
    when the item # or size matches a line being filled in."""
    if not customer_id:
        return []
    cat = category_of(form_key)
    wants = [(str(b.get("item_no") or "").strip(), _block_size(b)) for b in (blocks or []) if isinstance(b, dict)]
    out = []
    prod = _product_of(form_key)
    _, fkinds = _kind_words(PRODUCTS.get(prod, {}).get("label", "") if prod else "")
    for r in purchases(customer_id):
        if _SKIP.search(r["desc"]) or (cat and r["category"] != cat):
            continue
        if fkinds:
            _, rk = _kind_words(r["desc"] + " " + (r.get("stock_desc") or ""))
            rk = rk - _CORE_KINDS
            if rk and not (rk & fkinds):
                continue                       # e.g. faux wood blinds when ordering verticals
        size = parse_size(_clean(r["desc"]))
        match = any((it and it == r["item_no"]) or (sz and size and _close(sz, size)) for it, sz in wants)
        out.append({**r, "match": bool(match), "size": size})
    # orders made in this app (not invoiced yet, or no reports loaded)
    for o in q("""SELECT id, po, order_date, created, status, form_key, data FROM orders WHERE customer_id=?
                  AND status NOT IN ('Cancelled','Draft') ORDER BY coalesce(order_date, created) DESC LIMIT 100""",
               (customer_id,)):
        if form_key and o["form_key"] != form_key and category_of(o["form_key"]) != cat:
            continue
        for b in (loads(o["data"]) or {}).get("blocks") or []:
            if not isinstance(b, dict) or not any(b.values()):
                continue
            size = _block_size(b)
            match = any((it and it == str(b.get("item_no") or "")) or (sz and size and _close(sz, size))
                        for it, sz in wants)
            out.append({"date": o["order_date"] or (o["created"] or "")[:10], "invoice": f"Order #{o['id']}",
                        "order_id": o["id"], "type": o["status"], "item_no": b.get("item_no") or "",
                        "desc": describe_block(o["form_key"], b) + (f" *{b['unit']}" if b.get("unit") else ""),
                        "qty": _num(str(b.get("qty") or "1")) or 1, "price": None, "amount": None,
                        "category": cat, "source": "app", "match": bool(match), "size": size, "rep": ""})
    out.sort(key=lambda r: (not r["match"], -(int((r["date"] or "0").replace("-", "")[:8] or 0))))
    return out[:limit]


_CORE = [(re.compile(r"\bH/?C\b|hollow[\s-]*core", re.I), " hollowcore "),
         (re.compile(r"\bS/?C\b|solid[\s-]*core", re.I), " solidcore ")]
_CORE_KINDS = {"hollowcore", "solidcore"}


def _kind_words(text):
    from .pricing import _kinds, _words
    t = _clean(text)
    for rx, rep in _CORE:
        t = rx.sub(rep, t)
    w = _words(re.sub(r"[\d/'\"-]+", " ", t))
    k = _kinds(w) | (w & _CORE_KINDS)
    return w, k


_CACHE = {}


def _cat_rows(cat):
    """Distinct priced lines for a product category, pre-parsed (cached until new data is imported)."""
    if not cat:
        return []
    stamp = (q("SELECT coalesce(MAX(id),0) m FROM co_lines", one=True)["m"],
             q("SELECT coalesce(MAX(id),0) m FROM sales_lines", one=True)["m"])
    hit = _CACHE.get(cat)
    if hit and hit[0] == stamp:
        return hit[1]
    raw = list(q("""SELECT l.item_no, l.memo, l.price, d.doc_date FROM co_lines l JOIN co_docs d ON d.id=l.doc_id
                    WHERE l.category=? AND l.price>0 AND d.doc_type='Invoice' ORDER BY d.doc_date DESC LIMIT 20000""",
                 (cat,)))
    raw += list(q("""SELECT l.item_no, l.description memo, l.unit_price price, s.invoice_date doc_date
                     FROM sales_lines l JOIN sales s ON s.id=l.sale_id WHERE l.category=? AND l.unit_price>0
                     ORDER BY s.invoice_date DESC LIMIT 10000""", (cat,)))
    out, seen = [], set()
    for r in raw:
        clean = _clean(r["memo"] or "")
        k = (r["item_no"], clean.lower(), r["price"])
        if k in seen:
            continue
        seen.add(k)
        w, kinds = _kind_words(clean)
        out.append({"item_no": r["item_no"], "clean": clean, "price": r["price"], "doc_date": r["doc_date"],
                    "words": w, "kinds": kinds, "size": parse_size(clean)})
    _CACHE[cat] = (stamp, out)
    return out


def estimate_block(customer_id, form_key, blk, limit=400):
    """Rough price range for one order line from similar items sold before (same product kind, nearest sizes).
    Returns {low, high, likely, basis, n, desc} or None."""
    from .pricing import price_for
    desc = describe_block(form_key, blk)
    cat = category_of(form_key)
    item_no = str(blk.get("item_no") or "").strip() or None
    size = _block_size(blk)
    exact = price_for(customer_id, item_no, desc if not item_no else None, cat) if (item_no or customer_id) else None
    words, kinds = _kind_words(" ".join([desc] + [str(blk.get(k) or "") for k in ("core", "material", "type")]))
    cands = []
    rows = _cat_rows(cat)
    for r in rows:
        w2, k2 = r["words"], r["kinds"]
        if kinds and k2 and not (kinds & k2):
            continue
        c1, c2 = kinds & _CORE_KINDS, k2 & _CORE_KINDS
        if c1 and c2 and not (c1 & c2):
            continue
        overlap = len(words & w2) / max(len(words), 1) if words else 0
        if words and overlap < 0.34:
            continue
        s2 = r["size"]
        dist = 999.0
        if size and s2:
            dist = abs(size[0] - s2[0]) + abs(size[1] - s2[1])
        elif size or s2:
            dist = 500.0
        cands.append((dist, -overlap, r["doc_date"] or "", r["price"], r["clean"]))
    if item_no:
        same = [(0.0, -1.0, r["doc_date"] or "", r["price"], r["clean"]) for r in rows if r["item_no"] == item_no]
        if same:
            cands = same
    cands.sort(key=lambda c: (c[0], c[1], tuple(-ord(ch) for ch in c[2])))
    near = [c for c in cands if c[0] <= (cands[0][0] + 8 if cands else 0)][:12] if cands else []
    prices = sorted(c[3] for c in near)
    if not prices and not exact:
        return None
    res = {"desc": desc, "n": len(prices), "basis": [{"desc": c[4], "price": c[3], "date": c[2],
                                                      "off": None if c[0] >= 500 else round(c[0], 2)}
                                                     for c in near[:5]]}
    if prices:
        lo = prices[max(0, int(len(prices) * 0.2) - (1 if len(prices) > 4 else 0))]
        hi = prices[min(len(prices) - 1, int(len(prices) * 0.8))]
        res.update(low=round(lo, 2), high=round(hi, 2), likely=round(prices[len(prices) // 2], 2))
    if exact:
        res["exact"] = {"price": exact["price"], "source": exact["source"], "date": exact.get("date")}
        if "low" not in res:
            res.update(low=exact["price"], high=exact["price"], likely=exact["price"])
        else:
            res["low"] = min(res["low"], exact["price"])
            res["high"] = max(res["high"], exact["price"])
            res["likely"] = exact["price"]
    return res


def estimate_order(customer_id, form_key, blocks):
    lines, lo, hi, likely, unpriced = [], 0.0, 0.0, 0.0, 0
    for b in blocks or []:
        if not isinstance(b, dict) or not any(str(v).strip() for v in b.values() if v is not None):
            continue
        qty = _num(str(b.get("qty") or "1")) or 1
        own = _num(str(b.get("price") or "").replace("$", "").replace(",", ""))
        e = {"low": own, "high": own, "likely": own, "desc": describe_block(form_key, b), "n": 0,
             "basis": [], "exact": {"price": own, "source": "Price on the form"}} if own else \
            estimate_block(customer_id, form_key, b)
        if e and e.get("low") is not None:
            lo += e["low"] * qty
            hi += e["high"] * qty
            likely += e["likely"] * qty
        else:
            unpriced += 1
        lines.append({"qty": qty, "est": e, "desc": (e or {}).get("desc") or describe_block(form_key, b)})
    return {"lines": lines, "low": round(lo, 2), "high": round(hi, 2), "likely": round(likely, 2),
            "unpriced": unpriced}


# ------------------------------------------------------------------ order tracking totals
def order_value(o):
    """Conservative (low-end) dollar estimate for one order, and whether every line had a price."""
    data = loads(o["data"]) if isinstance(o["data"], str) else (o["data"] or {})
    blocks = data.get("blocks") or []
    if not o["form_key"] or not blocks:
        return 0.0, False
    est = estimate_order(o["customer_id"], o["form_key"], blocks)
    return est["low"], est["unpriced"] == 0 and bool(est["lines"])


def tracking_totals(today=None, compute_limit=40):
    """Orders put in (not Draft / Cancelled) today, this week, this month and this year, with a conservative
    (low-end) dollar estimate. Estimates are cached on the order; missing ones are worked out a few at a time."""
    from .db import x
    today = today or date.today()
    week0 = today - timedelta(days=today.weekday())
    periods = [("Today", today, today), ("This week", week0, today),
               ("This month", today.replace(day=1), today), ("Year to date", today.replace(month=1, day=1), today)]
    first = min(p[1] for p in periods).isoformat()
    rows = q("""SELECT o.*, substr(coalesce(nullif(o.sent_at,''), nullif(o.order_date,''), o.created),1,10) d
                FROM orders o WHERE o.status NOT IN ('Draft','Cancelled')
                AND substr(coalesce(nullif(o.sent_at,''), nullif(o.order_date,''), o.created),1,10)>=?""", (first,))
    done, pending = 0, 0
    vals = []
    for r in rows:
        v, ok = r["est_value"], r["est_complete"]
        if v is None:
            if done < compute_limit:
                try:
                    v, ok = order_value(r)
                except Exception:   # noqa: BLE001 - never break the tracking page over an estimate
                    v, ok = 0.0, False
                x("UPDATE orders SET est_value=?, est_complete=? WHERE id=?", (v, 1 if ok else 0, r["id"]))
                done += 1
            else:
                v, ok = 0.0, False
                pending += 1
        vals.append((r["d"], v or 0.0, bool(ok)))
    out = []
    for label, a, b in periods:
        inside = [v for v in vals if a.isoformat() <= (v[0] or "") <= b.isoformat()]
        out.append({"label": label, "orders": len(inside), "total": round(sum(v[1] for v in inside), 2),
                    "partial": sum(1 for v in inside if not v[2])})
    return {"periods": out, "pending": pending}
