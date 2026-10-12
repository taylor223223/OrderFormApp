"""Company Data: everything in the company system's reports, for every rep and every account.

Kept separate from "my sales" (the sales / sales_lines tables that drive goals and commission), so loading the
whole company never changes your own numbers. Filled by the same report import (Sales > Import company reports).
"""
import hashlib

from . import sales as S
from .db import find_customer, get_db, norm_name, now, q

COMPANY_SCHEMA = """
CREATE TABLE IF NOT EXISTS co_docs (
    id INTEGER PRIMARY KEY, doc_type TEXT, num TEXT, doc_date TEXT, customer TEXT, cust_key TEXT, rep TEXT,
    total REAL DEFAULT 0, report TEXT, batch TEXT
);
CREATE TABLE IF NOT EXISTS co_lines (
    id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL REFERENCES co_docs(id) ON DELETE CASCADE,
    item_no TEXT, item_desc TEXT, memo TEXT, category TEXT, qty REAL, price REAL, amount REAL
);
CREATE TABLE IF NOT EXISTS co_accounts (
    cust_key TEXT PRIMARY KEY, name TEXT, address TEXT, city TEXT, state TEXT, zip TEXT,
    summary_total REAL, summary_period TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS co_reps (name TEXT PRIMARY KEY, summary_total REAL, summary_period TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS co_imports (
    id INTEGER PRIMARY KEY, kind TEXT, filename TEXT, sha TEXT, rows INTEGER, lines INTEGER, first TEXT, last TEXT,
    complete INTEGER, total REAL, imported TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_codoc ON co_docs(doc_type, num, cust_key);
CREATE INDEX IF NOT EXISTS ix_codoc_date ON co_docs(doc_date);
CREATE INDEX IF NOT EXISTS ix_codoc_cust ON co_docs(cust_key);
CREATE INDEX IF NOT EXISTS ix_codoc_rep ON co_docs(rep);
CREATE INDEX IF NOT EXISTS ix_coline_doc ON co_lines(doc_id);
CREATE INDEX IF NOT EXISTS ix_coline_item ON co_lines(item_no);
"""


def sha(text):
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def already_loaded(text):
    return q("SELECT imported, kind FROM co_imports WHERE sha=? ORDER BY id DESC LIMIT 1", (sha(text),), one=True)


# ------------------------------------------------------------------ loading
def store(files, batch=None):
    """files = [(kind, text, filename)]. Saves every report line for every rep. A document (invoice / credit memo)
    found in several reports is taken from the first report only; loading it again later replaces it."""
    from . import company_reports as CR
    batch = batch or now()
    con = get_db()
    out = {"docs": 0, "lines": 0, "total": 0.0, "accounts": 0, "reps": 0}
    sales_files = [(i, k, CR.usable_records(t, k)[0]) for i, (k, t, _) in enumerate(files) if k in CR.SALES_KINDS]
    rep_of = {}
    for _, k, recs in sales_files:
        if k == "sales_rep":
            for r in recs:
                rep_of.setdefault((r["type"], r["invoice_no"]), r["rep"])
    docs = {}
    for src, k, recs in sales_files:
        for r in recs:
            d = S._date(r["date"])
            if not d:
                continue
            amt = r["amount"]
            if amt is None:
                amt = round(r["qty"] * r["unit_price"], 2) if r["qty"] is not None and r["unit_price"] is not None else 0.0
            key = (r["type"], r["invoice_no"], norm_name(r["customer_name"]))
            h = docs.get(key)
            if h is None:
                h = docs[key] = {"type": r["type"], "num": r["invoice_no"], "date": d, "customer": r["customer_name"],
                                 "rep": r["rep"] or rep_of.get((r["type"], r["invoice_no"]), ""), "report": k,
                                 "src": src, "lines": []}
            elif h["src"] != src:
                continue
            h["lines"].append((r["item_no"], r["item_desc"], r["description"],
                               S.category_for(r["description"], r["item_desc"], r["item_no"]), r["qty"],
                               r["unit_price"], amt))
    try:
        old = {(r["doc_type"], r["num"], r["cust_key"]): (r["id"], r["rep"]) for r in
               q("SELECT id, doc_type, num, cust_key, rep FROM co_docs")}
        for key, h in docs.items():
            prev = old.get(key)
            rep = h["rep"]
            if prev:
                rep = rep or prev[1] or ""            # a customer/item report doesn't say the rep - keep the known one
                con.execute("DELETE FROM co_docs WHERE id=?", (prev[0],))
            total = round(sum(ln[6] for ln in h["lines"]), 2)
            did = con.execute("""INSERT INTO co_docs(doc_type, num, doc_date, customer, cust_key, rep, total, report, batch)
                                 VALUES (?,?,?,?,?,?,?,?,?)""",
                              (h["type"], h["num"], h["date"], h["customer"], key[2], rep, total, h["report"],
                               batch)).lastrowid
            con.executemany("""INSERT INTO co_lines(doc_id, item_no, item_desc, memo, category, qty, price, amount)
                               VALUES (?,?,?,?,?,?,?,?)""", [(did,) + ln for ln in h["lines"]])
            out["docs"] += 1
            out["lines"] += len(h["lines"])
            out["total"] += total
            con.execute("INSERT OR IGNORE INTO co_accounts(cust_key, name, updated) VALUES (?,?,?)",
                        (key[2], h["customer"], now()))
        # accounts: addresses + lifetime totals; reps: lifetime totals
        for k, t, _ in files:
            if k == "ship_to":
                for key, a in CR.ship_to_addresses(t).items():
                    con.execute("""INSERT INTO co_accounts(cust_key, name, address, city, state, zip, updated)
                                   VALUES (?,?,?,?,?,?,?) ON CONFLICT(cust_key) DO UPDATE SET
                                   address=excluded.address, city=excluded.city, state=excluded.state,
                                   zip=excluded.zip, updated=excluded.updated""",
                                (key, a["name"], a["address"], a["city"], a["state"], a["zip"], now()))
                    out["accounts"] += 1
            elif k in ("summary_customer", "summary_rep"):
                rows = CR._rows(t)
                period = (rows[0][-1] if rows and rows[0] else "").strip()
                for r in rows[1:]:
                    if len(r) < 2 or not r[0].strip() or r[0].strip() == "TOTAL" or S._num(r[1]) is None:
                        continue
                    if k == "summary_rep":
                        con.execute("""INSERT INTO co_reps(name, summary_total, summary_period, updated) VALUES (?,?,?,?)
                                       ON CONFLICT(name) DO UPDATE SET summary_total=excluded.summary_total,
                                       summary_period=excluded.summary_period, updated=excluded.updated""",
                                    (r[0].strip(), S._num(r[1]), period, now()))
                        out["reps"] += 1
                    else:
                        con.execute("""INSERT INTO co_accounts(cust_key, name, summary_total, summary_period, updated)
                                       VALUES (?,?,?,?,?) ON CONFLICT(cust_key) DO UPDATE SET
                                       summary_total=excluded.summary_total, summary_period=excluded.summary_period,
                                       updated=excluded.updated""",
                                    (norm_name(r[0]), r[0].strip(), S._num(r[1]), period, now()))
                        out["accounts"] += 1
        for k, t, fn in files:
            info = CR.describe(t, k) if k in CR.SALES_KINDS else {"rows": len(CR._rows(t)) - 1, "complete": True}
            con.execute("""INSERT INTO co_imports(kind, filename, sha, rows, lines, first, last, complete, total, imported)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (k, fn, sha(t), info.get("rows"), info.get("lines"), info.get("first"), info.get("last"),
                         1 if info.get("complete", True) else 0, info.get("total"), batch))
        con.commit()
    except Exception:
        con.rollback()
        raise
    saved = q("SELECT COUNT(*) n, coalesce(SUM(total),0) t FROM co_docs WHERE batch=?", (batch,), one=True)
    out["total"] = round(out["total"], 2)
    out["verified"] = saved["n"] == out["docs"] and abs(saved["t"] - out["total"]) < 0.01
    return out


# ------------------------------------------------------------------ reading
def has_data():
    return bool(q("SELECT 1 FROM co_docs LIMIT 1", one=True) or q("SELECT 1 FROM co_accounts LIMIT 1", one=True))


def overview():
    o = dict(q("""SELECT COUNT(*) docs, coalesce(SUM(total),0) total, MIN(doc_date) first, MAX(doc_date) last,
                  COUNT(DISTINCT cust_key) accounts FROM co_docs""", one=True))
    o["lines"] = q("SELECT COUNT(*) n FROM co_lines", one=True)["n"]
    o["all_accounts"] = q("SELECT COUNT(*) n FROM co_accounts", one=True)["n"]
    years = [r["y"] for r in q("SELECT DISTINCT substr(doc_date,1,4) y FROM co_docs ORDER BY y")]
    grid = {}
    for r in q("SELECT coalesce(nullif(rep,''),'(rep not on report)') rep, substr(doc_date,1,4) y, SUM(total) t FROM co_docs GROUP BY 1,2"):
        grid.setdefault(r["rep"], {})[r["y"]] = r["t"]
    reps = {r["name"]: dict(r) for r in q("SELECT * FROM co_reps")}
    rows = []
    for rep in sorted(set(grid) | set(reps)):
        loaded = sum(grid.get(rep, {}).values())
        s = reps.get(rep, {}).get("summary_total")
        rows.append({"rep": rep, "years": grid.get(rep, {}), "loaded": loaded, "summary": s,
                     "pct": round(loaded / s * 100) if s else None})
    o.update(years=years, reps=rows, period=next((r["summary_period"] for r in reps.values() if r["summary_period"]), ""))
    o["imports"] = q("SELECT * FROM co_imports ORDER BY id DESC LIMIT 30")
    return o


def top_accounts(year=None, rep=None, limit=15):
    w, a = _w(year=year, rep=rep)
    return q(f"""SELECT d.cust_key, MAX(d.customer) name, SUM(d.total) total, COUNT(*) docs, MAX(d.doc_date) last
                 FROM co_docs d WHERE {w} GROUP BY d.cust_key ORDER BY total DESC LIMIT ?""", a + [limit])


def top_items(year=None, rep=None, limit=15):
    w, a = _w(year=year, rep=rep)
    return q(f"""SELECT l.item_no, MAX(coalesce(nullif(l.item_desc,''), l.memo)) descr, MAX(l.category) category,
                 SUM(l.qty) qty, SUM(l.amount) total FROM co_lines l JOIN co_docs d ON d.id=l.doc_id WHERE {w}
                 GROUP BY l.item_no ORDER BY total DESC LIMIT ?""", a + [limit])


def _w(year=None, rep=None, cust_key=None, frm=None, to=None, doc_type=None):
    w, a = ["1=1"], []
    if year:
        w.append("substr(d.doc_date,1,4)=?")
        a.append(str(year))
    if rep:
        w.append("coalesce(nullif(d.rep,''),'(rep not on report)')=?")
        a.append(rep)
    if cust_key:
        w.append("d.cust_key=?")
        a.append(cust_key)
    if frm:
        w.append("d.doc_date>=?")
        a.append(frm)
    if to:
        w.append("d.doc_date<=?")
        a.append(to)
    if doc_type:
        w.append("d.doc_type=?")
        a.append(doc_type)
    return " AND ".join(w), a


def accounts(term="", rep="", city="", sort="total", limit=500):
    w, a = ["1=1"], []
    if term:
        w.append("(ac.name LIKE ? OR ac.address LIKE ? OR ac.zip LIKE ?)")
        a += [f"%{term}%"] * 3
    if city:
        w.append("ac.city=?")
        a.append(city)
    order = {"name": "ac.name COLLATE NOCASE", "last": "last DESC", "loaded": "loaded DESC"}.get(
        sort, "coalesce(ac.summary_total, loaded) DESC")
    rows = [dict(r) for r in q(f"""SELECT ac.*, s.loaded, s.last, s.docs, s.rep FROM co_accounts ac LEFT JOIN
               (SELECT cust_key, SUM(total) loaded, MAX(doc_date) last, COUNT(*) docs,
                (SELECT rep FROM co_docs d2 WHERE d2.cust_key=d.cust_key AND rep<>'' GROUP BY rep
                 ORDER BY SUM(total) DESC LIMIT 1) rep FROM co_docs d GROUP BY cust_key) s ON s.cust_key=ac.cust_key
               WHERE {' AND '.join(w)} ORDER BY {order} LIMIT ?""", a + [limit])]
    if rep:
        rows = [r for r in rows if (r["rep"] or "") == rep]
    return rows


def cities():
    return [r["city"] for r in q("SELECT DISTINCT city FROM co_accounts WHERE city<>'' ORDER BY city")]


def rep_names():
    return [r["rep"] for r in q("""SELECT DISTINCT coalesce(nullif(rep,''),'(rep not on report)') rep FROM co_docs
                                   UNION SELECT name FROM co_reps ORDER BY 1""")]


def account(cust_key):
    ac = q("SELECT * FROM co_accounts WHERE cust_key=?", (cust_key,), one=True)
    if not ac:
        return None
    ac = dict(ac)
    ac["by_year"] = q("""SELECT substr(doc_date,1,4) y, SUM(total) t, COUNT(*) n FROM co_docs WHERE cust_key=?
                         GROUP BY y ORDER BY y DESC""", (cust_key,))
    ac["reps"] = q("""SELECT coalesce(nullif(rep,''),'(rep not on report)') rep, SUM(total) t, MAX(doc_date) last
                      FROM co_docs WHERE cust_key=? GROUP BY 1 ORDER BY t DESC""", (cust_key,))
    ac["cats"] = q("""SELECT l.category, SUM(l.amount) t FROM co_lines l JOIN co_docs d ON d.id=l.doc_id
                      WHERE d.cust_key=? GROUP BY l.category ORDER BY t DESC""", (cust_key,))
    ac["items"] = q("""SELECT l.item_no, MAX(coalesce(nullif(l.item_desc,''), l.memo)) descr, SUM(l.qty) qty,
                       SUM(l.amount) t, MAX(d.doc_date) last,
                       (SELECT l2.price FROM co_lines l2 JOIN co_docs d2 ON d2.id=l2.doc_id WHERE d2.cust_key=d.cust_key
                        AND l2.item_no=l.item_no AND l2.price IS NOT NULL ORDER BY d2.doc_date DESC LIMIT 1) last_price
                       FROM co_lines l JOIN co_docs d ON d.id=l.doc_id WHERE d.cust_key=?
                       GROUP BY l.item_no ORDER BY t DESC LIMIT 200""", (cust_key,))
    ac["docs"] = q("SELECT * FROM co_docs WHERE cust_key=? ORDER BY doc_date DESC, id DESC LIMIT 200", (cust_key,))
    c = find_customer(name=ac["name"])
    ac["customer_id"] = c["id"] if c else None
    return ac


def lines(term="", rep="", cust="", item="", frm="", to="", doc_type="", limit=1000):
    w, a = _w(rep=rep or None, frm=frm or None, to=to or None, doc_type=doc_type or None)
    if cust:
        w += " AND d.customer LIKE ?"
        a.append(f"%{cust}%")
    if item:
        w += " AND (l.item_no LIKE ? OR l.item_desc LIKE ?)"
        a += [f"%{item}%"] * 2
    if term:
        w += " AND (l.memo LIKE ? OR l.item_desc LIKE ? OR l.item_no LIKE ? OR d.num LIKE ? OR d.customer LIKE ?)"
        a += [f"%{term}%"] * 5
    rows = q(f"""SELECT l.*, d.doc_type, d.num, d.doc_date, d.customer, d.cust_key, d.rep, d.id did
                 FROM co_lines l JOIN co_docs d ON d.id=l.doc_id WHERE {w}
                 ORDER BY d.doc_date DESC, d.id DESC, l.id LIMIT ?""", a + [limit])
    tot = q(f"""SELECT COUNT(*) n, coalesce(SUM(l.amount),0) t, coalesce(SUM(l.qty),0) qty FROM co_lines l
                JOIN co_docs d ON d.id=l.doc_id WHERE {w}""", a, one=True)
    return rows, dict(tot)


def doc(did):
    d = q("SELECT * FROM co_docs WHERE id=?", (did,), one=True)
    return (dict(d), q("SELECT * FROM co_lines WHERE doc_id=? ORDER BY id", (did,))) if d else (None, [])


def add_to_customers(cust_key):
    """Copy a company account into my Customers (name + address). Returns the customer id."""
    ac = q("SELECT * FROM co_accounts WHERE cust_key=?", (cust_key,), one=True)
    if not ac:
        return None
    c = find_customer(name=ac["name"])
    if c:
        return c["id"]
    con = get_db()
    cid = con.execute("""INSERT INTO customers(name, address, city, state, zip, notes, created, updated)
                         VALUES (?,?,?,?,?,?,?,?)""",
                      (ac["name"], ac["address"] or "", ac["city"] or "", ac["state"] or "", ac["zip"] or "",
                       "Added from Company Data", now(), now())).lastrowid
    con.commit()
    return cid


def company_key(name):
    """The Company Data account for one of my customers (same name, or one clear prefix match), or None."""
    n = norm_name(name)
    if not n:
        return None
    if q("SELECT 1 FROM co_accounts WHERE cust_key=?", (n,), one=True):
        return n
    if len(n) >= 6:
        hits = [r["cust_key"] for r in q("SELECT cust_key FROM co_accounts WHERE cust_key LIKE ? OR ? LIKE cust_key || '%'",
                                         (n + "%", n)) if len(r["cust_key"]) >= 6]
        if len(hits) == 1:
            return hits[0]
    return None


def latest_price(item_no):
    """Most recent price any account paid company-wide (used when my own history has none)."""
    if not item_no:
        return None
    r = q("""SELECT l.price, l.memo, d.doc_date FROM co_lines l JOIN co_docs d ON d.id=l.doc_id
             WHERE l.item_no=? AND l.price IS NOT NULL AND l.price>0 AND d.doc_type='Invoice'
             ORDER BY d.doc_date DESC LIMIT 1""", (str(item_no).strip(),), one=True)
    return dict(r) if r else None


# ------------------------------------------------------------------ analysis
DIMS = {
    "year": ("Year", "substr(d.doc_date,1,4)"),
    "month": ("Month", "substr(d.doc_date,1,7)"),
    "moy": ("Month of year (all years)", "substr(d.doc_date,6,2)"),
    "quarter": ("Quarter", "substr(d.doc_date,1,4) || '-Q' || ((cast(substr(d.doc_date,6,2) as integer)+2)/3)"),
    "rep": ("Sales rep", "coalesce(nullif(d.rep,''),'(rep not on report)')"),
    "category": ("Product category", "coalesce(l.category,'Other')"),
    "account": ("Account", "d.customer"),
    "item": ("Item", "l.item_no"),
    "city": ("City", "coalesce(nullif(ac.city,''),'(unknown)')"),
    "type": ("Document type", "d.doc_type"),
}
MEASURES = {
    "sales": ("Sales $", "SUM(l.amount)", "money"),
    "qty": ("Units sold", "SUM(l.qty)", "num"),
    "docs": ("Invoices", "COUNT(DISTINCT d.id)", "num"),
    "accounts": ("Accounts buying", "COUNT(DISTINCT d.cust_key)", "num"),
    "avg": ("Average invoice $", "SUM(l.amount) * 1.0 / COUNT(DISTINCT d.id)", "money"),
}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _filters(f):
    w, a = ["1=1"], []
    for key, sql in (("year", "substr(d.doc_date,1,4)=?"), ("rep", DIMS["rep"][1] + "=?"),
                     ("category", "coalesce(l.category,'Other')=?"), ("cust_key", "d.cust_key=?"),
                     ("city", "ac.city=?"), ("from", "d.doc_date>=?"), ("to", "d.doc_date<=?"),
                     ("type", "d.doc_type=?")):
        if f.get(key):
            w.append(sql)
            a.append(f[key])
    if f.get("item"):
        w.append("(l.item_no LIKE ? OR l.item_desc LIKE ? OR l.memo LIKE ?)")
        a += [f"%{f['item']}%"] * 3
    if f.get("account"):
        w.append("d.customer LIKE ?")
        a.append(f"%{f['account']}%")
    return " AND ".join(w), a


def pivot(dim="month", measure="sales", split="", filters=None, limit=25):
    """Group company sales by one dimension (optionally split into series by a second) -> chart + table data."""
    filters = filters or {}
    dim = dim if dim in DIMS else "month"
    measure = measure if measure in MEASURES else "sales"
    split = split if split in DIMS and split != dim else ""
    w, a = _filters(filters)
    dx, mx = DIMS[dim][1], MEASURES[measure][1]
    base = f"FROM co_lines l JOIN co_docs d ON d.id=l.doc_id LEFT JOIN co_accounts ac ON ac.cust_key=d.cust_key WHERE {w}"
    time_dim = dim in ("year", "month", "moy", "quarter")
    order = "k" if time_dim else "v DESC"
    rows = q(f"SELECT {dx} k, {mx} v {base} GROUP BY k ORDER BY {order}" + ("" if time_dim else " LIMIT ?"),
             a + ([] if time_dim else [limit]))
    keys = [r["k"] for r in rows]
    out = {"dim": dim, "measure": measure, "split": split, "fmt": MEASURES[measure][2],
           "dim_label": DIMS[dim][0], "measure_label": MEASURES[measure][0]}
    labels = [MONTHS[int(k) - 1] if dim == "moy" and k else (k if k not in (None, "") else "(blank)") for k in keys]
    if not split:
        out.update(labels=labels, series=[{"name": MEASURES[measure][0], "values": [round(r["v"] or 0, 2) for r in rows]}])
    else:
        sx = DIMS[split][1]
        tops = [r["s"] for r in q(f"SELECT {sx} s, {mx} v {base} GROUP BY s ORDER BY v DESC LIMIT 8", a)]
        series = []
        if keys:
            ph = ",".join("?" * len(keys))
            data = {}
            for r in q(f"SELECT {dx} k, {sx} s, {mx} v {base} AND {dx} IN ({ph}) GROUP BY k, s", a + keys):
                data[(r["k"], r["s"] if r["s"] in tops else "Everything else")] = \
                    data.get((r["k"], r["s"] if r["s"] in tops else "Everything else"), 0) + (r["v"] or 0)
            names = tops + (["Everything else"] if any(s == "Everything else" for _, s in data) else [])
            series = [{"name": str(s or "(blank)"), "values": [round(data.get((k, s), 0), 2) for k in keys]} for s in names]
        out.update(labels=labels, series=series)
    tot = q(f"SELECT {mx} v, SUM(l.amount) s, COUNT(DISTINCT d.id) n {base}", a, one=True)
    out["total"] = round(tot["v"] or 0, 2)
    out["keys"] = keys
    return out


def monthly_series(years, filters=None):
    """{year: [12 monthly sales]} for a year-over-year line chart."""
    filters = dict(filters or {})
    filters.pop("year", None)
    w, a = _filters(filters)
    out = {str(y): [0.0] * 12 for y in years}
    for r in q(f"""SELECT substr(d.doc_date,1,4) y, substr(d.doc_date,6,2) m, SUM(l.amount) t FROM co_lines l
                   JOIN co_docs d ON d.id=l.doc_id LEFT JOIN co_accounts ac ON ac.cust_key=d.cust_key
                   WHERE {w} GROUP BY y, m""", a):
        if r["y"] in out:
            out[r["y"]][int(r["m"]) - 1] = round(r["t"] or 0, 2)
    return out


def years():
    return [r["y"] for r in q("SELECT DISTINCT substr(doc_date,1,4) y FROM co_docs ORDER BY y DESC")]


def _days(a, b):
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def profile(cust_key):
    """Everything about one account: totals, trend, mix, ordering rhythm, items with last price, gaps."""
    from datetime import date, timedelta
    ac = account(cust_key)
    if not ac:
        return None
    today = date.today()
    last_doc = q("SELECT MAX(doc_date) d FROM co_docs", one=True)["d"] or today.isoformat()
    asof = min(today.isoformat(), last_doc)          # the reports' newest date - "today" for the data
    y = asof[:4]
    md = asof[5:]
    ly = str(int(y) - 1)
    t = lambda sql, args: (q(sql, args, one=True)["t"] or 0)   # noqa: E731
    ac["asof"] = asof
    ac["lifetime"] = t("SELECT SUM(total) t FROM co_docs WHERE cust_key=?", (cust_key,))
    ac["ytd"] = t("SELECT SUM(total) t FROM co_docs WHERE cust_key=? AND doc_date BETWEEN ? AND ?",
                  (cust_key, f"{y}-01-01", asof))
    ac["lytd"] = t("SELECT SUM(total) t FROM co_docs WHERE cust_key=? AND doc_date BETWEEN ? AND ?",
                   (cust_key, f"{ly}-01-01", f"{ly}-{md if md != '02-29' else '02-28'}"))
    ac["change"] = round((ac["ytd"] - ac["lytd"]) / ac["lytd"] * 100) if ac["lytd"] else None
    a12 = (date.fromisoformat(asof) - timedelta(days=365)).isoformat()
    a24 = (date.fromisoformat(asof) - timedelta(days=730)).isoformat()
    ac["t12"] = t("SELECT SUM(total) t FROM co_docs WHERE cust_key=? AND doc_date>?", (cust_key, a12))
    ac["p12"] = t("SELECT SUM(total) t FROM co_docs WHERE cust_key=? AND doc_date>? AND doc_date<=?", (cust_key, a24, a12))
    inv = [r["doc_date"] for r in q("""SELECT DISTINCT doc_date FROM co_docs WHERE cust_key=? AND doc_type='Invoice'
                                       ORDER BY doc_date""", (cust_key,))]
    n_inv = q("SELECT COUNT(*) n FROM co_docs WHERE cust_key=? AND doc_type='Invoice'", (cust_key,), one=True)["n"]
    ac["orders"] = n_inv
    ac["avg_order"] = ac["lifetime"] / n_inv if n_inv else None
    ac["first"] = inv[0] if inv else None
    ac["last"] = inv[-1] if inv else None
    gaps = [_days(a, b) for a, b in zip(inv, inv[1:])]
    gaps.sort()
    ac["typical_gap"] = gaps[len(gaps) // 2] if gaps else None
    ac["days_since"] = _days(inv[-1], asof) if inv else None
    ac["overdue"] = bool(ac["typical_gap"] and ac["days_since"] and len(gaps) >= 3 and
                         ac["days_since"] > max(45, ac["typical_gap"] * 2.5))
    # last 24 months of spend
    months = []
    d0 = date.fromisoformat(asof).replace(day=1)
    for i in range(23, -1, -1):
        yy, mm = d0.year, d0.month - i
        while mm <= 0:
            yy, mm = yy - 1, mm + 12
        months.append(f"{yy}-{mm:02d}")
    by_m = {r["m"]: r["t"] for r in q("""SELECT substr(doc_date,1,7) m, SUM(total) t FROM co_docs WHERE cust_key=?
                                         AND doc_date>=? GROUP BY m""", (cust_key, months[0] + "-01"))}
    ac["months"] = months
    ac["month_vals"] = [round(by_m.get(m, 0) or 0, 2) for m in months]
    # product mix this 12 months vs before -> categories they stopped buying
    recent = {r["category"] for r in q("""SELECT DISTINCT l.category FROM co_lines l JOIN co_docs d ON d.id=l.doc_id
                                          WHERE d.cust_key=? AND d.doc_date>? AND l.amount>0""", (cust_key, a12))}
    ac["dropped"] = [dict(r) for r in q("""SELECT l.category, SUM(l.amount) t, MAX(d.doc_date) last FROM co_lines l
                     JOIN co_docs d ON d.id=l.doc_id WHERE d.cust_key=? AND d.doc_date<=? AND l.amount>0
                     AND l.category NOT IN ('Other') GROUP BY l.category ORDER BY t DESC""", (cust_key, a12))
                     if r["category"] not in recent and r["t"] > 100]
    bought = {r["category"] for r in ac["cats"]}
    ac["never"] = [c for c in S.CATEGORIES if c not in bought and c != "Other"]
    return ac


def insights(rep="", min_t12=1000):
    """Decision lists across all accounts (optionally one rep's): who's slipping, who's growing, who's overdue,
    how concentrated sales are, and which products each big account isn't buying."""
    from datetime import date, timedelta
    last_doc = q("SELECT MAX(doc_date) d FROM co_docs", one=True)["d"]
    if not last_doc:
        return None
    asof = min(date.today().isoformat(), last_doc)
    a12 = (date.fromisoformat(asof) - timedelta(days=365)).isoformat()
    a24 = (date.fromisoformat(asof) - timedelta(days=730)).isoformat()
    rep_sql, ra = ("", [])
    if rep:
        rep_sql, ra = " AND " + DIMS["rep"][1] + "=?", [rep]
    rows = [dict(r) for r in q(f"""SELECT d.cust_key, MAX(d.customer) name,
            SUM(CASE WHEN d.doc_date>? THEN d.total ELSE 0 END) t12,
            SUM(CASE WHEN d.doc_date>? AND d.doc_date<=? THEN d.total ELSE 0 END) p12,
            MAX(d.doc_date) last, COUNT(CASE WHEN d.doc_type='Invoice' THEN 1 END) n, MIN(d.doc_date) first
            FROM co_docs d WHERE 1=1{rep_sql} GROUP BY d.cust_key""", [a12, a24, a12] + ra)]
    for r in rows:
        r["change"] = round((r["t12"] - r["p12"]) / r["p12"] * 100) if r["p12"] else None
        r["days"] = _days(r["last"], asof) if r["last"] else None
    first = q("SELECT MIN(doc_date) d FROM co_docs", one=True)["d"]
    have_p12 = bool(first and first < a12)
    slipping = sorted([r for r in rows if r["p12"] >= min_t12 and r["t12"] < r["p12"] * 0.7],
                      key=lambda r: r["t12"] - r["p12"])[:25]
    growing = sorted([r for r in rows if r["t12"] >= min_t12 and r["p12"] and r["t12"] > r["p12"] * 1.2],
                     key=lambda r: r["p12"] - r["t12"])[:25]
    new = sorted([r for r in rows if r["first"] and r["first"] > a12 and r["t12"] > 0],
                 key=lambda r: -r["t12"])[:25] if have_p12 else []
    # overdue: usually order often, but quiet for much longer than usual
    overdue = []
    for r in sorted(rows, key=lambda r: -(r["t12"] + r["p12"]))[:400]:
        if r["n"] < 4 or not r["days"] or r["days"] < 45:
            continue
        ds = [x["doc_date"] for x in q("""SELECT DISTINCT doc_date FROM co_docs WHERE cust_key=? AND doc_type='Invoice'
                                          ORDER BY doc_date""", (r["cust_key"],))]
        gaps = sorted(_days(a, b) for a, b in zip(ds, ds[1:]))
        if gaps:
            r["gap"] = gaps[len(gaps) // 2]
            if r["days"] > max(45, r["gap"] * 2.5):
                overdue.append(r)
    overdue = sorted(overdue, key=lambda r: -(r["t12"] + r["p12"]))[:25]
    # concentration (Pareto) on the last 12 months
    vals = sorted((r["t12"] for r in rows if r["t12"] > 0), reverse=True)
    tot = sum(vals) or 1
    k = max(1, round(len(vals) * 0.2))
    pareto = {"accounts": len(vals), "top20_share": round(sum(vals[:k]) / tot * 100), "top10": round(sum(vals[:10]) / tot * 100),
              "total": tot, "top_n": k}
    # cross-sell: biggest 30 accounts (12 months) and the main categories they don't buy
    main_cats = ["Blinds & Shades", "Doors", "Closet & Wardrobe Doors", "Screens", "Door Hardware",
                 "Baseboard & Casing", "Cabinets", "Countertops"]
    cross = []
    for r in sorted(rows, key=lambda r: -r["t12"])[:30]:
        have = {x["category"] for x in q("""SELECT DISTINCT l.category FROM co_lines l JOIN co_docs d ON d.id=l.doc_id
                                           WHERE d.cust_key=? AND d.doc_date>? AND l.amount>0""", (r["cust_key"], a12))}
        miss = [c for c in main_cats if c not in have]
        if miss:
            cross.append({**r, "missing": miss, "has": [c for c in main_cats if c in have]})
    # category trend
    cats = q(f"""SELECT coalesce(l.category,'Other') c, SUM(CASE WHEN d.doc_date>? THEN l.amount ELSE 0 END) t12,
                 SUM(CASE WHEN d.doc_date>? AND d.doc_date<=? THEN l.amount ELSE 0 END) p12
                 FROM co_lines l JOIN co_docs d ON d.id=l.doc_id WHERE 1=1{rep_sql} GROUP BY c ORDER BY t12 DESC""",
             [a12, a24, a12] + ra)
    return {"asof": asof, "a12": a12, "slipping": slipping, "growing": growing, "new": new, "overdue": overdue,
            "pareto": pareto, "cross": cross, "cats": [dict(c) for c in cats], "have_p12": have_p12,
            "t12": sum(r["t12"] for r in rows), "p12": sum(r["p12"] for r in rows)}
