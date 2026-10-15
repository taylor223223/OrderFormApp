"""Rolling contact log: every property / person you were in touch with, built automatically from orders.

Each order becomes a contact entry (unless you already logged one for that property that day - then the
order is linked to it). Every entry has how you were in touch (in person, call, text, email) and whether it
was a warm or cold contact. The log is the "last visit" source for route suggestions.
"""
from datetime import date, datetime, timedelta

from .db import now, q, setting, x

HOW = ["In person", "Call", "Text", "Email"]
TEMPS = ["Warm", "Cold"]
IN_PERSON = ("In person", "Site visit", "Drop-in", "Meeting", "Trade show")


def _iso(d):
    """'10/09/2026' or '2026-10-09...' -> '2026-10-09' (None if unreadable)."""
    d = (d or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(d[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _had_business_before(cid, day):
    """True when the property ordered / bought before this day -> a warm contact."""
    if q("SELECT 1 FROM sales WHERE customer_id=? AND invoice_date < ? LIMIT 1", (cid, day), one=True):
        return True
    for o in q("SELECT order_date, created FROM orders WHERE customer_id=?", (cid,)):
        d = _iso(o["order_date"]) or (o["created"] or "")[:10]
        if d and d < day:
            return True
    try:
        from .company_db import company_key
        c = q("SELECT name FROM customers WHERE id=?", (cid,), one=True)
        k = company_key(c["name"]) if c else None
        if k and q("SELECT 1 FROM co_docs WHERE cust_key=? AND doc_date < ? LIMIT 1", (k, day), one=True):
            return True
    except Exception:  # noqa: BLE001 - company data is optional
        pass
    return False


def sync_orders():
    """Add a contact entry for every order that doesn't have one yet. Returns how many were added/linked."""
    have = {r["order_id"] for r in q("SELECT order_id FROM activities WHERE order_id IS NOT NULL")}
    n = 0
    how = setting("contact_default_how") or "In person"
    for o in q("""SELECT o.id, o.customer_id, o.title, o.order_date, o.created, o.status, o.po
                  FROM orders o WHERE o.customer_id IS NOT NULL ORDER BY o.id"""):
        if o["id"] in have or o["status"] == "Cancelled":
            continue
        day = _iso(o["order_date"]) or (o["created"] or now())[:10]
        # already logged something with this property that day? link the order to it instead of a 2nd entry
        same = q("""SELECT id FROM activities WHERE customer_id=? AND substr(occurred_at,1,10)=?
                    AND order_id IS NULL ORDER BY id LIMIT 1""", (o["customer_id"], day), one=True)
        if same:
            x("UPDATE activities SET order_id=? WHERE id=?", (o["id"], same["id"]))
            n += 1
            continue
        tm = (o["created"] or "")[11:16] if (o["created"] or "")[:10] == day else "12:00"
        temp = "Warm" if _had_business_before(o["customer_id"], day) else "Cold"
        x("""INSERT INTO activities(customer_id, contact_name, kind, occurred_at, subject, notes, outcome, owner,
             created, order_id, temp) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
          (o["customer_id"], "", how, f"{day} {tm or '12:00'}", f"Order #{o['id']}: {o['title'] or 'order'}",
           f"PO {o['po']}" if o["po"] else "", "Order", "auto", now(), o["id"], temp))
        n += 1
    return n


def entries(frm=None, to=None, term="", how="", temp="", limit=1000):
    sql = """SELECT a.*, c.name AS cname, c.city AS ccity FROM activities a LEFT JOIN customers c ON c.id=a.customer_id
             WHERE substr(a.occurred_at,1,10) BETWEEN ? AND ?"""
    args = [frm or "0000", to or "9999"]
    if term:
        sql += " AND (c.name LIKE ? OR a.contact_name LIKE ? OR a.subject LIKE ? OR a.notes LIKE ?)"
        args += [f"%{term}%"] * 4
    if how == "In person":
        sql += f" AND a.kind IN ({','.join('?' * len(IN_PERSON))})"
        args += list(IN_PERSON)
    elif how:
        sql += " AND a.kind=?"
        args.append(how)
    if temp:
        sql += " AND coalesce(a.temp,'')=?"
        args.append(temp)
    sql += " ORDER BY a.occurred_at DESC, a.id DESC LIMIT ?"
    args.append(limit)
    return q(sql, args)


def last_contacts(customer_ids):
    """{customer_id: row(occurred_at, kind, temp, contact_name)} - the latest contact with each property."""
    out = {}
    ids = [i for i in customer_ids if i]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in q(f"""SELECT a.customer_id, a.occurred_at, a.kind, a.temp, a.contact_name FROM activities a
                       JOIN (SELECT customer_id, MAX(occurred_at) m FROM activities
                             WHERE customer_id IN ({','.join('?' * len(chunk))}) GROUP BY customer_id) z
                       ON z.customer_id=a.customer_id AND z.m=a.occurred_at""", chunk):
            out.setdefault(r["customer_id"], r)
    return out


def week_counts(day=None):
    d = day or date.today()
    mon = d - timedelta(days=d.weekday())
    rows = q("""SELECT kind, coalesce(temp,'') temp, COUNT(*) n FROM activities
                WHERE substr(occurred_at,1,10) BETWEEN ? AND ? GROUP BY kind, temp""",
             (mon.isoformat(), (mon + timedelta(days=6)).isoformat()))
    res = {"In person": 0, "Call": 0, "Text": 0, "Email": 0, "Warm": 0, "Cold": 0, "total": 0}
    for r in rows:
        k = "In person" if r["kind"] in IN_PERSON else r["kind"]
        res[k] = res.get(k, 0) + r["n"]
        if r["temp"]:
            res[r["temp"]] += r["n"]
        res["total"] += r["n"]
    return res
