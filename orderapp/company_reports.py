"""Reads the company system's (QuickBooks-style) report exports as-is - no column matching needed.

Sales reports (one line item per row, grouped under a customer / rep / item heading):
  Sales by Customer Detail, Sales by Rep Detail, Sales by Item Detail
Reference reports (stored in the items table, used by quotes and the Items page):
  Item Price List, Inventory Stock Status, Item Turnover, Sales by Item Summary (avg price / COGS / margin)
Addresses: Sales by Ship To Address (fills blank customer addresses)
Summaries (Sales by Customer / Rep Summary) are only used to check the import's totals.
"""
import csv
import io
import re

from .db import find_customer, get_db, norm_name, now, q
from . import sales as S

ITEMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    item_no TEXT PRIMARY KEY, description TEXT, vendor TEXT, list_price REAL, category TEXT,
    on_hand REAL, on_so REAL, available REAL, on_po REAL, reorder_pt REAL, next_deliv TEXT, sales_week REAL,
    qty_sold REAL, turnover_days REAL, last_sold TEXT, avg_price REAL, avg_cost REAL, margin_pct REAL,
    price_updated TEXT, stock_updated TEXT, updated TEXT
);
"""

KINDS = {
    "sales_customer": "Sales by Customer Detail", "sales_rep": "Sales by Rep Detail",
    "sales_item": "Sales by Item Detail", "ship_to": "Sales by Ship To Address", "price_list": "Item Price List",
    "inventory": "Inventory Stock Status", "turnover": "Item Turnover", "item_summary": "Sales by Item Summary",
    "summary_rep": "Sales by Rep Summary", "summary_customer": "Sales by Customer Summary",
}
SALES_KINDS = ("sales_customer", "sales_rep", "sales_item")


def init(con):
    con.executescript(ITEMS_SCHEMA)


def decode(raw):
    if isinstance(raw, str):
        return raw.lstrip("﻿")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


def _k(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def detect(text, filename=""):
    """Which report this is, from its header row(s). None = not a company report (use the column matcher)."""
    rows = _rows(text[:20000])
    if not rows:
        return None
    h = [_k(c) for c in rows[0]]
    h2 = [_k(c) for c in rows[1]] if len(rows) > 1 else []
    hs = set(h)
    if {"type", "date", "num"} <= hs and "amount" in hs:
        if "shiptoaddress1" in hs:
            return "ship_to"
        if "item" not in hs:
            return "sales_item"
        return _customer_or_rep(rows)
    if {"item", "description", "price"} <= hs and "qtysold" not in hs:
        return "price_list"
    if "onhand" in hs and "available" in hs:
        return "inventory"
    if "turnoverdays" in hs or "turnoverratio" in hs:
        return "turnover"
    if {"qty", "amount", "avgprice"} <= set(h2) or {"qty", "amount", "avgprice"} <= hs:
        return "item_summary"
    if len([c for c in rows[0] if c.strip()]) == 1 and re.search(r"\d", rows[0][-1] or ""):
        f = filename.lower()
        if "rep" in f:
            return "summary_rep"
        if "cust" in f:
            return "summary_customer"
        return "summary_customer" if len(rows) > 30 else "summary_rep"
    return None


def _customer_or_rep(rows):
    """Detail grouped by customer (heading = the Name on its lines) or by sales rep (heading never is)."""
    hdr = [_k(c) for c in rows[0]]
    ni = hdr.index("name") if "name" in hdr else None
    group, same, diff = None, 0, 0
    for r in rows[1:400]:
        if r and r[0].strip() and not r[0].startswith("Total"):
            group = r[0].strip()
        elif r and not r[0].strip() and ni is not None and len(r) > ni and group:
            if r[ni].strip().lower() == group.lower() or r[ni].strip().lower().startswith(group.lower() + ":"):
                same += 1
            else:
                diff += 1
    return "sales_customer" if same >= diff else "sales_rep"


def split_item(s):
    """'130015 (3'-0" X 6'-8" Bi-Fold Door Set)' -> ('130015', '3'-0" X 6'-8" Bi-Fold Door Set')."""
    s = (s or "").strip()
    i = s.find(" (")
    if i > 0 and s.endswith(")"):
        return s[:i].strip(), s[i + 2:-1].strip()
    return s, ""


def _n(v):
    return S._num(v)


def _grouped(rows):
    """Yield (groups, row-dict) for each data line of a grouped report; groups = headings, outermost first."""
    hdr = rows[0]
    stack = []
    for r in rows[1:]:
        if not r or not any(c.strip() for c in r):
            continue
        first = r[0].strip()
        if first:
            if first == "TOTAL":
                stack = []
            elif first.startswith("Total "):
                name = first[6:]
                while stack:
                    if stack.pop() == name:
                        break
            else:
                stack.append(first)
            continue
        yield list(stack), {hdr[i].strip(): (r[i] if i < len(r) else "") for i in range(1, len(hdr))}


def _complete(rows):
    return any(r and r[0].strip() == "TOTAL" for r in rows[-5:])


# ------------------------------------------------------------------ sales detail reports
def sales_records(text, kind):
    """Flat line items from a Sales by Customer / Rep / Item Detail report."""
    out = []
    for groups, d in _grouped(_rows(text)):
        if not d.get("Type") or not d.get("Date"):
            continue
        g = groups[-1] if groups else ""
        if kind == "sales_item":
            item_no, item_desc = split_item(g)
        else:
            item_no, item_desc = split_item(d.get("Item", ""))
        rep = g if kind == "sales_rep" else ""
        cust = (d.get("Name") or "").strip() or (g if kind == "sales_customer" else "")
        memo = (d.get("Memo") or "").strip()
        out.append({"type": d.get("Type", ""), "invoice_no": (d.get("Num") or "").strip(), "date": d.get("Date"),
                    "customer_name": cust, "rep": rep, "item_no": item_no, "description": memo or item_desc,
                    "item_desc": item_desc, "qty": _n(d.get("Qty")), "unit_price": _n(d.get("Sales Price")),
                    "amount": _n(d.get("Amount"))})
    return out


def usable_records(text, kind):
    """Sales lines safe to load. A cut-off export ends part-way through an invoice, so its last invoice is left out
    (the next export brings it in whole). A cut-off ITEM report only holds part of every invoice, so it isn't used
    for invoices at all. Returns (records, note)."""
    recs = sales_records(text, kind)
    if not recs or _complete(_rows(text)):
        return recs, ""
    if kind == "sales_item":
        return [], "not used for invoices - a cut-off item report only has some of each invoice's lines"
    last = recs[-1]
    if kind == "sales_customer":              # that account's history is incomplete - leave the whole account out
        keep = [r for r in recs if r["customer_name"] != last["customer_name"]]
        return keep, (f"it stops part-way through {last['customer_name']}, so that account is left out until a "
                      f"fuller export")
    key = (last["type"], last["invoice_no"], last["customer_name"])
    keep = [r for r in recs if (r["type"], r["invoice_no"], r["customer_name"]) != key]
    return keep, f"its last invoice ({last['invoice_no']}) was cut part-way, so it's left out until a fuller export"


def tie_out(rows):
    """Add up every line under each heading and compare with the report's own "Total ..." row."""
    hdr = [c.strip() for c in rows[0]]
    if "Amount" not in hdr:
        return {}
    ai = hdr.index("Amount")
    stack, sums, ok, bad, grand = [], {}, 0, [], 0.0
    for r in rows[1:]:
        if not r or not any(c.strip() for c in r):
            continue
        first = r[0].strip()
        if first and first.startswith("Total "):
            g = first[6:]
            want = _n(r[ai]) if len(r) > ai else None
            # innermost heading with that name
            idx = max(i for i, n in enumerate(stack) if n == g) if g in stack else None
            if idx is not None:
                got = round(sums.get(idx, 0.0), 2)
                if want is not None:
                    if abs(want - got) < 0.01:
                        ok += 1
                    else:
                        bad.append(g)
                for i in range(idx, len(stack)):
                    sums.pop(i, None)
                del stack[idx:]
        elif first == "TOTAL":
            want = _n(r[ai]) if len(r) > ai else None
            if want is not None:
                if abs(want - round(grand, 2)) < 0.01:
                    ok += 1
                else:
                    bad.append("TOTAL")
        elif first:
            stack.append(first)
            sums[len(stack) - 1] = 0.0
        elif not first:
            a = _n(r[ai]) if len(r) > ai else None
            grand += a or 0
            for i in range(len(stack)):
                sums[i] = sums.get(i, 0.0) + (a or 0)
    return {"tied": ok, "untied": bad}


def describe(text, kind):
    """Preview facts for one uploaded report."""
    rows = _rows(text)
    info = {"kind": kind, "label": KINDS.get(kind, "Unknown"), "rows": max(len(rows) - 1, 0),
            "complete": _complete(rows)}
    if kind in SALES_KINDS:
        recs = sales_records(text, kind)
        dates = sorted(d for d in (S._date(r["date"]) for r in recs) if d)
        info.update(tie_out(rows))
        info["note"] = usable_records(text, kind)[1]
        info.update(lines=len(recs), first=dates[0] if dates else "", last=dates[-1] if dates else "",
                    invoices=len({(r["invoice_no"], r["customer_name"].lower()) for r in recs}),
                    total=round(sum(r["amount"] or 0 for r in recs), 2))
        if kind == "sales_rep":
            reps = {}
            for r in recs:
                t = reps.setdefault(r["rep"], {"lines": 0, "total": 0.0, "first": None, "last": None})
                d = S._date(r["date"])
                t["lines"] += 1
                t["total"] += r["amount"] or 0
                t["first"] = min(filter(None, [t["first"], d])) if d else t["first"]
                t["last"] = max(filter(None, [t["last"], d])) if d else t["last"]
            info["reps"] = reps
            # the last rep in a cut-off file is the one that got cut short
            info["cut_rep"] = recs[-1]["rep"] if recs and not info["complete"] else ""
    elif kind in ("summary_rep", "summary_customer"):
        info["totals"] = {r[0]: _n(r[1]) for r in rows[1:] if len(r) > 1 and r[0] and _n(r[1]) is not None}
        info["complete"] = True
    elif kind == "ship_to":
        info["addresses"] = len(ship_to_addresses(text))
    else:
        info["items"] = len(_item_rows(text, kind))
        info["complete"] = True if kind in ("price_list", "turnover") else info["complete"]
    return info


def import_sales(files, reps=None, add_customers=False, addresses=None):
    """files = [(kind, text)]. reps = set of rep names to keep (None = all).
    An invoice that shows up in several reports is taken from the first one only (no double counting).
    When a rep filter is on, invoices from reports that don't say the rep (customer / item detail) are kept
    only if a rep report in the same batch puts them under a chosen rep."""
    recs_by_file = [(i, kind, usable_records(text, kind)[0]) for i, (kind, text) in enumerate(files)
                    if kind in SALES_KINDS]
    rep_of = {}
    for _, kind, recs in recs_by_file:
        if kind == "sales_rep":
            for r in recs:
                rep_of.setdefault(r["invoice_no"], r["rep"])
    invoices, skipped, filtered = {}, 0, set()
    for src, kind, recs in recs_by_file:
        for r in recs:
            d = S._date(r["date"])
            amount = r["amount"]
            if amount is None and r["qty"] is not None and r["unit_price"] is not None:
                amount = round(r["qty"] * r["unit_price"], 2)
            if amount is None and r["type"]:
                amount = 0.0                             # e.g. a discount line with no amount
            if not d or amount is None:
                skipped += 1
                continue
            rep = r["rep"] or rep_of.get(r["invoice_no"], "")
            if reps is not None and rep not in reps:
                filtered.add(r["invoice_no"])
                continue
            key = (r["invoice_no"] or f"{d}-{r['customer_name']}", r["customer_name"].lower())
            h = invoices.get(key)
            if h is None:
                h = invoices[key] = {"invoice_no": key[0], "invoice_date": d, "customer_name": r["customer_name"],
                                     "acct": "", "po": "", "rep": rep, "src": src, "lines": [],
                                     "credit": r["type"].lower().startswith("credit")}
            elif h["src"] != src:
                continue
            h["lines"].append({"category": S.category_for(r["description"], r["item_desc"], r["item_no"]),
                               "item_no": r["item_no"], "description": r["description"], "qty": r["qty"],
                               "unit_price": r["unit_price"], "amount": amount, "cost": None})
    res = save_invoices(list(invoices.values()), add_customers=add_customers, addresses=addresses)
    res.update(skipped_rows=skipped, filtered_invoices=len(filtered))
    return res


class _Customers:
    """Name -> customer lookup cached for a big import (one pass over the customers table per new name)."""

    def __init__(self):
        self.cache = {}

    def get(self, name, acct=""):
        k = (norm_name(name), acct or "")
        if k not in self.cache:
            c = find_customer(name=name, acct=acct)
            self.cache[k] = c["id"] if c else None
        return self.cache[k]


def save_invoices(invoices, add_customers=False, addresses=None, batch=None):
    """Write invoices (each {invoice_no, invoice_date, customer_name, acct, po, rep, lines}) in one transaction.
    The same invoice imported again replaces the old copy."""
    batch = batch or now()
    con = get_db()
    custs = _Customers()
    existing = {(r["invoice_no"], (r["n"] or "")): r["id"] for r in
                q("SELECT id, invoice_no, lower(coalesce(customer_name,'')) n FROM sales")}
    added = replaced = matched = created = 0
    unmatched = set()
    addresses = addresses or {}
    try:
        for h in invoices:
            name = h["customer_name"]
            cid = custs.get(name, h.get("acct", ""))
            if not cid and add_customers and name:
                a = addresses.get(norm_name(name), {})
                cid = con.execute("""INSERT INTO customers(name, address, city, state, zip, notes, created, updated)
                                     VALUES (?,?,?,?,?,?,?,?)""",
                                  (name, a.get("address", ""), a.get("city", ""), a.get("state", ""),
                                   a.get("zip", ""), "Added from company sales report", now(), now())).lastrowid
                custs.cache[(norm_name(name), h.get("acct", "") or "")] = cid
                created += 1
            if cid:
                matched += 1
            else:
                unmatched.add(name or "(blank)")
            old = existing.pop((h["invoice_no"], name.lower()), None)
            if old:
                con.execute("DELETE FROM sales WHERE id=?", (old,))
                replaced += 1
            else:
                added += 1
            total = round(sum(ln["amount"] for ln in h["lines"]), 2)
            costs = [ln["cost"] for ln in h["lines"] if ln.get("cost") is not None]
            sid = con.execute("""INSERT INTO sales(invoice_no, invoice_date, customer_id, customer_name, acct, po, rep,
                                 total, cost, batch, created) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                              (h["invoice_no"], h["invoice_date"], cid, name, h.get("acct", ""), h.get("po", ""),
                               h.get("rep", ""), total, round(sum(costs), 2) if costs else None, batch,
                               now())).lastrowid
            con.executemany("""INSERT INTO sales_lines(sale_id, category, item_no, description, qty, unit_price, amount,
                               cost) VALUES (?,?,?,?,?,?,?,?)""",
                            [(sid, ln["category"], ln["item_no"], ln["description"], ln["qty"], ln["unit_price"],
                              ln["amount"], ln.get("cost")) for ln in h["lines"]])
        con.commit()
    except Exception:
        con.rollback()
        raise
    saved = q("SELECT COUNT(*) n, coalesce(SUM(total), 0) t FROM sales WHERE batch=?", (batch,), one=True)
    lines = q("SELECT COUNT(*) n FROM sales_lines WHERE sale_id IN (SELECT id FROM sales WHERE batch=?)", (batch,),
              one=True)["n"]
    want_total = round(sum(ln["amount"] for h in invoices for ln in h["lines"]), 2)
    verified = (saved["n"] == len(invoices) and abs(saved["t"] - want_total) < 0.01 and
                lines == sum(len(h["lines"]) for h in invoices))
    return {"verified": verified, "lines": lines, "invoices": len(invoices), "added": added, "replaced": replaced, "matched": matched,
            "created": created, "unmatched": sorted(unmatched),
            "total": round(sum(ln["amount"] for h in invoices for ln in h["lines"]), 2)}


# ------------------------------------------------------------------ ship-to addresses
def ship_to_addresses(text):
    """{normalised property name: {name, address, city, state, zip}} - the latest address on file wins."""
    out = {}
    for groups, d in _grouped(_rows(text)):
        name = (d.get("Ship To Address 1") or "").strip() or (groups[-1] if groups else "")
        if not name:
            continue
        city, state = "", ""
        for g in groups:
            m = re.match(r"^(.*?)\s*,\s*([A-Z]{2})$", g.strip())
            if m:
                city, state = m.group(1).strip(), m.group(2)
        out[norm_name(name)] = {"name": name, "address": (d.get("Ship To Address 2") or "").strip(),
                                "city": city, "state": state, "zip": (d.get("Ship Zip") or "").strip()}
    return out


def fill_addresses(addrs):
    """Fill blank address / city / state / zip on customers that match. Never overwrites what you typed."""
    n = 0
    con = get_db()
    custs = _Customers()
    for a in addrs.values():
        cid = custs.get(a["name"])
        if not cid:
            continue
        c = q("SELECT address, city, state, zip FROM customers WHERE id=?", (cid,), one=True)
        upd = {k: a[k] for k in ("address", "city", "state", "zip") if a.get(k) and not (c[k] or "").strip()}
        if upd:
            con.execute(f"UPDATE customers SET {', '.join(k + '=?' for k in upd)}, updated=? WHERE id=?",
                        list(upd.values()) + [now(), cid])
            n += 1
    con.commit()
    return n


# ------------------------------------------------------------------ items (price list, stock, turnover, margins)
def _item_rows(text, kind):
    rows = _rows(text)
    out = []
    if kind == "price_list":
        hdr = [_k(c) for c in rows[0]]
        ix = {k: hdr.index(k) for k in ("item", "description", "preferredvendor", "price") if k in hdr}
        for r in rows[1:]:
            g = lambda k: (r[ix[k]].strip() if k in ix and ix[k] < len(r) else "")   # noqa: E731
            if g("item"):
                out.append({"item_no": g("item"), "description": g("description"), "vendor": g("preferredvendor"),
                            "list_price": _n(g("price")), "price_updated": now()})
    elif kind == "inventory":
        hdr = [c.strip() for c in rows[0]]
        col = {_k(c): i for i, c in enumerate(hdr)}
        for r in rows[1:]:
            if not r or not r[0].strip() or r[0].startswith("Total") or r[0] == "TOTAL":
                continue
            g = lambda k: (r[col[k]].strip() if k in col and col[k] < len(r) else "")   # noqa: E731
            if _n(g("onhand")) is None:
                continue                                 # a heading row (category / item type)
            item_no, desc = split_item(r[0])
            out.append({"item_no": item_no, "description": desc, "vendor": g("prefvendor"),
                        "on_hand": _n(g("onhand")), "on_so": _n(g("onsalesorder")), "available": _n(g("available")),
                        "on_po": _n(g("onpo")), "reorder_pt": _n(g("reorderptmin")),
                        "next_deliv": S._date(g("nextdeliv")) or g("nextdeliv"), "sales_week": _n(g("salesweek")),
                        "stock_updated": now()})
    elif kind == "turnover":
        hdr = [_k(c) for c in rows[0]]
        col = {k: i for i, k in enumerate(hdr)}
        for r in rows[1:]:
            g = lambda k: (r[col[k]].strip() if k in col and col[k] < len(r) else "")   # noqa: E731
            if not g("item") or not any(g(k) for k in col if k not in ("item",)):
                continue                                 # a heading row (item type)
            out.append({"item_no": g("item"), "description": g("description"), "qty_sold": _n(g("qtysold")),
                        "turnover_days": _n(g("turnoverdays")), "last_sold": S._date(g("lastselldate"))})
    elif kind == "item_summary":
        hi = 1 if len(rows) > 1 and "avgprice" in [_k(c) for c in rows[1]] else 0
        hdr = [_k(c) for c in rows[hi]]
        col = {k: i for i, k in enumerate(hdr) if k}
        for r in rows[hi + 1:]:
            if not r or not r[0].strip() or r[0].startswith("Total") or r[0] == "TOTAL":
                continue
            g = lambda k: (r[col[k]].strip() if k in col and col[k] < len(r) else "")   # noqa: E731
            if _n(g("qty")) is None:
                continue
            item_no, desc = split_item(r[0])
            out.append({"item_no": item_no, "description": desc, "avg_price": _n(g("avgprice")),
                        "avg_cost": _n(g("avgcogs")), "margin_pct": _n(g("grossmargin").rstrip("%")),
                        "_qty": _n(g("qty"))})
        best = {}
        for r in out:                                    # same item listed twice: keep the bigger seller
            if r["item_no"] not in best or (r["_qty"] or 0) > (best[r["item_no"]]["_qty"] or 0):
                best[r["item_no"]] = r
        out = [{k: v for k, v in r.items() if k != "_qty"} for r in best.values()]
    return out


def import_items(text, kind):
    """Upsert one reference report into the items table; only that report's columns change."""
    rows = _item_rows(text, kind)
    con = get_db()
    for r in rows:
        r = dict(r)
        r["category"] = S.category_for(r.get("description"), r["item_no"])
        if not r.get("description"):
            r.pop("description")
            r.pop("category")
        cols = [k for k in r if k != "item_no"]
        con.execute(f"""INSERT INTO items(item_no, {', '.join(cols)}, updated) VALUES (?, {', '.join('?' * len(cols))}, ?)
                        ON CONFLICT(item_no) DO UPDATE SET {', '.join(f'{k}=excluded.{k}' for k in cols)},
                        updated=excluded.updated""",
                    [r["item_no"]] + [r[k] for k in cols] + [now()])
    con.commit()
    return len(rows)


def item(item_no):
    if not item_no:
        return None
    r = q("SELECT * FROM items WHERE item_no=?", (str(item_no).strip(),), one=True)
    return dict(r) if r else None


def stock_text(it):
    """'12 available · 30 on PO (due 2026-10-20)' for the quote editor."""
    if not it or it.get("on_hand") is None:
        return ""
    parts = [f"{it['available']:g} available" if it.get("available") is not None else f"{it['on_hand']:g} on hand"]
    if it.get("on_po"):
        parts.append(f"{it['on_po']:g} on PO" + (f" (due {it['next_deliv']})" if it.get("next_deliv") else ""))
    return " · ".join(parts)


def search(term="", category="", stock="", limit=300):
    w, a = ["1=1"], []
    if term:
        w.append("(item_no LIKE ? OR description LIKE ? OR vendor LIKE ?)")
        a += [f"%{term}%"] * 3
    if category:
        w.append("category=?")
        a.append(category)
    if stock == "in":
        w.append("available > 0")
    elif stock == "short":
        w.append("(available < 0 OR (reorder_pt IS NOT NULL AND available <= reorder_pt AND sales_week > 0))")
    elif stock == "po":
        w.append("on_po > 0")
    return q(f"""SELECT * FROM items WHERE {' AND '.join(w)}
                 ORDER BY (last_sold IS NULL), last_sold DESC, item_no LIMIT ?""", a + [limit])


def counts():
    r = q("""SELECT COUNT(*) n, SUM(list_price IS NOT NULL) priced, SUM(on_hand IS NOT NULL) stocked,
             MAX(price_updated) pu, MAX(stock_updated) su FROM items""", one=True)
    return dict(r) if r else {}
