"""Reorder form: pick items a property bought before (from its invoice history), set quantities, and get a
one-page-per-~24-lines "Reorder Request" PDF for the office. Saved as a normal order (form_key 'reorder')
so it shows in Order Tracking, gets the status taps and can be emailed from the Send page."""
import fitz

from .db import dumps, now, q, setting, x

FORM_KEY = "reorder"
TITLE = "Reorder Request"

PAGE_W, PAGE_H = 612, 792
L, R = 36, 576
NAVY = (0.12, 0.23, 0.41)
GREY = (0.93, 0.94, 0.96)
DARK = (0.25, 0.25, 0.25)
COLS = [("", 18), ("Qty", 32), ("Item #", 74), ("Description", 186), ("Size", 54), ("For (unit / room)", 64),
        ("Last invoice", 74), ("Last $", 38)]


def _money(v):
    try:
        return f"${float(v):,.2f}" if v not in (None, "") else ""
    except (TypeError, ValueError):
        return str(v)


def _qty(v):
    try:
        f = float(v)
        return f"{f:g}"
    except (TypeError, ValueError):
        return str(v or "")


def clean_lines(lines):
    out = []
    for ln in lines:
        if not (str(ln.get("desc") or "").strip() or str(ln.get("item_no") or "").strip()):
            continue
        q_ = str(ln.get("qty") or "").strip()
        if q_ in ("", "0"):
            continue
        out.append({k: str(ln.get(k) or "").strip() for k in
                    ("qty", "item_no", "desc", "size", "for", "invoice", "last_date", "last_price", "notes")})
    return out


def header_for(cust, po="", day=""):
    from datetime import date
    return {"name": cust["name"], "acct": cust["acct"] or "", "address": cust["address"] or "",
            "city": cust["city"] or "", "state": cust["state"] or "", "zip": cust["zip"] or "",
            "mgmt": cust["mgmt"] or "", "phone": cust["phone"] or "", "po": po,
            "date": day or date.today().isoformat(), "sales_rep": setting("sales_rep") or ""}


def create(cid, lines, po="", comments="", contact=""):
    from .db import add_status
    cust = q("SELECT * FROM customers WHERE id=?", (cid,), one=True)
    data = {"header": header_for(cust, po), "lines": clean_lines(lines), "comments": comments,
            "contact": contact, "reorder": True}
    oid = x("""INSERT INTO orders(customer_id, form_key, title, po, order_date, status, data, source, notes, created,
               updated, kind) VALUES (?,?,?,?,?,'Draft',?,?,?,?,?,'order')""",
            (cid, FORM_KEY, TITLE, po, data["header"]["date"], dumps(data), "reorder",
             f"{len(data['lines'])} item(s) picked from purchase history", now(), now()))
    add_status(oid, "Draft", "Reorder picked from purchase history")
    return oid


def _put(page, x0, y0, x1, y1, text, size=8, bold=False, align=0, color=(0, 0, 0)):
    """Write text in a box, shrinking (then wrapping) so it always fits - never silently dropped."""
    font = "hebo" if bold else "helv"
    text = str(text or "")
    r = fitz.Rect(x0 + 2, y0 + 2, x1 - 2, y1 + 4)
    s = size
    while s >= 5.5:
        tmp = fitz.open()
        pg = tmp.new_page(width=PAGE_W, height=PAGE_H)
        ok = pg.insert_textbox(r, text, fontsize=s, fontname=font, align=align) >= 0
        tmp.close()
        if ok:
            break
        s -= 0.5
    page.insert_textbox(r, text, fontsize=max(s, 5.5), fontname=font, align=align, color=color)


def render(o, data, cust_name=None):
    """PDF bytes for a reorder order row + its data."""
    h = data.get("header") or {}
    lines = data.get("lines") or []
    per_page = 24
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    doc = fitz.open()
    name = cust_name or h.get("name") or ""
    for pn, chunk in enumerate(pages):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        # title band
        page.draw_rect(fitz.Rect(L, 30, R, 62), color=None, fill=NAVY)
        page.insert_text((L + 10, 51), "APARTMENT INTERIOR SUPPLY", fontsize=13, fontname="hebo", color=(1, 1, 1))
        page.insert_text((R - 170, 51), "REORDER REQUEST", fontsize=15, fontname="hebo", color=(1, 1, 1))
        # header boxes
        y = 70
        box = [("Property", name, 300), ("Acct #", h.get("acct"), 80), ("PO #", h.get("po"), 80), ("Date", h.get("date"), 80)]
        x0 = L
        for lab, val, w in box:
            page.draw_rect(fitz.Rect(x0, y, x0 + w, y + 30), color=(0.6, 0.6, 0.6), width=0.6)
            page.insert_text((x0 + 4, y + 9), lab.upper(), fontsize=6.5, fontname="hebo", color=DARK)
            _put(page, x0, y + 10, x0 + w, y + 30, val, size=10, bold=True)
            x0 += w
        y += 30
        addr = ", ".join(p for p in [h.get("address"), " ".join(p for p in [h.get("city"), h.get("state"), h.get("zip")] if p)] if p)
        box = [("Ship to / address", addr, 300), ("Mgmt Co.", h.get("mgmt"), 160), ("Sales Rep", h.get("sales_rep"), 80)]
        x0 = L
        for lab, val, w in box:
            page.draw_rect(fitz.Rect(x0, y, x0 + w, y + 30), color=(0.6, 0.6, 0.6), width=0.6)
            page.insert_text((x0 + 4, y + 9), lab.upper(), fontsize=6.5, fontname="hebo", color=DARK)
            _put(page, x0, y + 10, x0 + w, y + 30, val, size=9)
            x0 += w
        y += 30
        if data.get("contact"):
            page.insert_text((L, y + 12), f"Requested by: {data['contact']}", fontsize=8.5, fontname="helv")
            y += 14
        y += 8
        page.insert_text((L, y + 8), "Same items as before - item #, description, size and invoice # are from this property's "
                         "past invoices. Last $ is the last invoiced price; please confirm current pricing.",
                         fontsize=7, fontname="helv", color=DARK)
        y += 14
        # table header
        page.draw_rect(fitz.Rect(L, y, R, y + 16), color=None, fill=NAVY)
        x0 = L
        for lab, w in COLS:
            page.insert_text((x0 + 3, y + 11), lab, fontsize=7.5, fontname="hebo", color=(1, 1, 1))
            x0 += w
        y += 16
        row_h = 22
        for i, ln in enumerate(chunk):
            if i % 2:
                page.draw_rect(fitz.Rect(L, y, R, y + row_h), color=None, fill=GREY)
            vals = ["", _qty(ln.get("qty")), ln.get("item_no"), ln.get("desc") + (f"  ({ln['notes']})" if ln.get("notes") else ""),
                    ln.get("size"), ln.get("for"),
                    " ".join(p for p in [("#" + ln["invoice"]) if ln.get("invoice") else "", ln.get("last_date")] if p),
                    _money(ln.get("last_price"))]
            x0 = L
            for (lab, w), v in zip(COLS, vals):
                if lab == "":
                    page.draw_rect(fitz.Rect(x0 + 4, y + 6, x0 + 13, y + 15), color=(0.3, 0.3, 0.3), width=0.7)
                else:
                    _put(page, x0, y + 1, x0 + w, y + row_h - 1, v, size=9 if lab == "Qty" else 8,
                         bold=(lab == "Qty"), align=1 if lab in ("Qty",) else 0)
                x0 += w
            page.draw_line((L, y + row_h), (R, y + row_h), color=(0.8, 0.8, 0.8), width=0.4)
            y += row_h
        if pn == len(pages) - 1:
            n_items = len(lines)
            tq = 0.0
            for ln in lines:
                try:
                    tq += float(ln.get("qty") or 0)
                except ValueError:
                    pass
            y += 6
            page.insert_text((L, y + 10), f"{n_items} line(s) · {tq:g} piece(s) total", fontsize=9, fontname="hebo")
            y += 18
            page.draw_rect(fitz.Rect(L, y, R, y + 58), color=(0.6, 0.6, 0.6), width=0.6)
            page.insert_text((L + 4, y + 9), "COMMENTS", fontsize=6.5, fontname="hebo", color=DARK)
            _put(page, L, y + 11, R, y + 58, data.get("comments") or "", size=9)
        page.insert_text((L, PAGE_H - 28), "Office: check the box on each line when it's entered.", fontsize=7,
                         fontname="helv", color=DARK)
        page.insert_text((R - 60, PAGE_H - 28), f"Page {pn + 1} of {len(pages)}", fontsize=7, fontname="helv",
                         color=DARK)
    doc.set_metadata({"title": f"{TITLE} - {name}", "creator": "Apartment Interior Supply"})
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def picked_from_form(form):
    """The products-tab form -> line dicts for the rows that were ticked."""
    lines = []
    for i in form.getlist("sel"):
        g = lambda k: form.get(f"{k}_{i}", "")   # noqa: E731
        lines.append({"qty": g("qty") or "1", "item_no": g("item"), "desc": g("desc"), "size": g("size"),
                      "for": g("for"), "invoice": g("inv"), "last_date": g("last"), "last_price": g("price"),
                      "notes": ""})
    return lines
