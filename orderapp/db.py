"""SQLite storage. One local file in the user's app-data folder."""
import json
import os
import sqlite3
from datetime import datetime

from flask import g

from .paths import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    failed_attempts INTEGER DEFAULT 0, locked_until TEXT, created TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, acct TEXT, address TEXT, city TEXT, state TEXT,
    zip TEXT, mgmt TEXT, phone TEXT, email TEXT, notes TEXT, created TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    name TEXT, role TEXT, email TEXT, phone TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS floorplans (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    name TEXT NOT NULL, beds TEXT, baths TEXT, sqft TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    unit_number TEXT NOT NULL, building TEXT,
    floorplan_id INTEGER REFERENCES floorplans(id) ON DELETE SET NULL, notes TEXT
);
CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    floorplan_id INTEGER REFERENCES floorplans(id) ON DELETE CASCADE,
    unit_id INTEGER REFERENCES units(id) ON DELETE CASCADE,
    product TEXT NOT NULL, room TEXT, data TEXT NOT NULL DEFAULT '{}', verified INTEGER DEFAULT 0,
    notes TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    form_key TEXT, title TEXT, po TEXT, order_date TEXT, status TEXT DEFAULT 'Draft',
    data TEXT NOT NULL DEFAULT '{}', pdf_path TEXT, source TEXT DEFAULT 'manual',
    vendor_ref TEXT, due_date TEXT, notes TEXT, sent_at TEXT, created TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS order_status_history (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    status TEXT, note TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS change_batches (
    id INTEGER PRIMARY KEY, source TEXT, label TEXT, created TEXT, status TEXT DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES change_batches(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, customer_id INTEGER, customer_ref TEXT, field TEXT, old_value TEXT,
    new_value TEXT, payload TEXT, default_on INTEGER DEFAULT 1, status TEXT DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS email_messages (
    id INTEGER PRIMARY KEY, provider TEXT, msg_id TEXT UNIQUE, subject TEXT, sender TEXT,
    sender_name TEXT, received TEXT, body TEXT, status TEXT DEFAULT 'new', order_id INTEGER,
    parsed TEXT
);
CREATE TABLE IF NOT EXISTS order_photos (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    filename TEXT, path TEXT NOT NULL, caption TEXT, created TEXT
);
CREATE INDEX IF NOT EXISTS ix_units_cust ON units(customer_id);
CREATE INDEX IF NOT EXISTS ix_meas_cust ON measurements(customer_id);
CREATE INDEX IF NOT EXISTS ix_orders_cust ON orders(customer_id);
"""

CUSTOMER_FIELDS = ["name", "acct", "address", "city", "state", "zip", "mgmt", "phone", "email", "notes", "acct_type",
                   "services"]

DEFAULT_SETTINGS = {
    "email_order_word": "Order",      # only emails with this in the subject are read automatically
    "email_auto_minutes": "15",       # 0 = off
    "email_lookback_days": "3",
    "email_last_check": "",
    "sales_rep": "Taylor M. Anderson",
    "order_to": "orders@apartmentinterior.net",
    "order_cc": "",
    "email_provider": "outlook_desktop",
    "send_mode": "review",
    "graph_client_id": "",
    "graph_tenant": "common",
    "subject_template": "{title} - {customer} - PO {po}",
    "body_template": "Hello,\n\nPlease see the attached {title} for {customer} (PO {po}).\n\nThank you,\n{sales_rep}",
    "output_dir": "",
    "date_format": "%m/%d/%Y",
    "session_minutes": "240",
    # CRM + routes
    "report_to": "",
    "report_cc": "",
    "ors_api_key": "",
    "route_start_address": "5325 S. Kyrene Rd, Suite 103, Tempe, AZ 85283",
    "route_start_time": "08:00",
    "route_visit_minutes": "20",
    "route_return_to_start": "1",
    # sales reports
    "commission_rate": "",        # % of sales (or of margin)
    "commission_basis": "sales",  # sales | margin
    "monthly_goal": "",
    "demo": "",
    # quotes
    "quote_valid_days": "30",
    "quote_terms": "Prices are good until the date above. Sales tax and delivery charges, if any, are added on the "
                   "invoice. Custom-sized items are made to order. Thank you for the opportunity!",
    # morning delivery list email
    "notice_subject_words": "Order Received, Order Confirmation, Order Acknowledgement, Acknowledgment, Order Entered",
    "notice_auto": "1",
    "contact_default_how": "In person",
    "along_count": "5",
    "reply_confirm": "1",
    "reply_confirm_words": "got it, got this, received, recieved, will do, on it, entered, placed, confirmed, done, thanks, thank you, ok, okay, 10-4, sounds good",
    "delivery_subject_word": "Deliver",
    "delivery_auto": "1",
}

STATUSES = ["Draft", "Ready", "Sent", "Confirmed", "Backordered", "Shipped", "Delivered",
            "Installed", "Completed", "On Hold", "Cancelled"]
ACTIVITY_KINDS = ["In person", "Call", "Text", "Email", "Site visit", "Drop-in", "Meeting", "Trade show", "Voicemail", "Other"]
CUSTOMER_STATUSES = ["Lead", "Prospect", "Active", "Inactive"]
DEAL_STAGES = ["Lead", "Contacted", "Quote sent", "Negotiating", "Won", "Lost"]
TOPICS = ["Doors", "Pre-hung doors", "Bi-pass doors", "Screen doors", "Window screens", "Vertical blinds",
          "Horizontal blinds", "Baseboards", "Cabinets", "Pricing", "Delivery", "Vendor setup", "Turn schedule",
          "Rehab / renovation", "Complaint", "Samples"]
OPEN_STATUSES = ["Draft", "Ready", "Sent", "Confirmed", "Backordered", "Shipped", "On Hold"]


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def db_path():
    return os.path.join(data_dir(), "orderapp.db")


def connect(path=None):
    con = sqlite3.connect(path or db_path(), detect_types=0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


CRM_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_links (
    id INTEGER PRIMARY KEY, a_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    b_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE, note TEXT, created TEXT
);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL, contact_name TEXT,
    kind TEXT NOT NULL, occurred_at TEXT NOT NULL, location TEXT, subject TEXT, topics TEXT,
    notes TEXT, outcome TEXT, owner TEXT, created TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    activity_id INTEGER REFERENCES activities(id) ON DELETE SET NULL,
    title TEXT NOT NULL, due_date TEXT, done INTEGER DEFAULT 0, done_at TEXT, owner TEXT, created TEXT
);
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    name TEXT NOT NULL, product TEXT, value REAL, stage TEXT DEFAULT 'Lead', close_date TEXT,
    notes TEXT, owner TEXT, created TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS route_days (
    day TEXT PRIMARY KEY, start_address TEXT, start_time TEXT, total_drive_s REAL, total_m REAL,
    legs TEXT, geometry TEXT, estimated INTEGER DEFAULT 0, optimized_at TEXT
);
CREATE TABLE IF NOT EXISTS route_stops (
    id INTEGER PRIMARY KEY, day TEXT NOT NULL, position INTEGER DEFAULT 0,
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL, label TEXT, address TEXT,
    lat REAL, lon REAL, purpose TEXT, visit_min INTEGER, status TEXT DEFAULT 'Planned', source TEXT,
    notes TEXT, activity_id INTEGER, created TEXT, products TEXT
);
CREATE TABLE IF NOT EXISTS trip_log (
    day TEXT PRIMARY KEY, stops INTEGER, visited INTEGER, skipped INTEGER, miles REAL, drive_s REAL,
    estimated INTEGER DEFAULT 0, start_address TEXT, places TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS account_holds (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_key TEXT,
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    balance REAL, rep TEXT, terms TEXT, status TEXT DEFAULT 'hold', notes TEXT,
    since TEXT, updated TEXT, cleared TEXT
);
CREATE TABLE IF NOT EXISTS geocache (address TEXT PRIMARY KEY, lat REAL, lon REAL, provider TEXT, updated TEXT);
CREATE INDEX IF NOT EXISTS ix_act_cust ON activities(customer_id);
CREATE INDEX IF NOT EXISTS ix_act_when ON activities(occurred_at);
CREATE INDEX IF NOT EXISTS ix_stops_day ON route_stops(day);
"""

# columns added to existing tables after v1.1 (added in place, data kept)
MIGRATIONS = {
    "customers": {"status": "TEXT", "tags": "TEXT", "lat": "REAL", "lon": "REAL", "geo_address": "TEXT",
                  "owner": "TEXT", "acct_type": "TEXT", "services": "TEXT"},
    "route_stops": {"products": "TEXT", "done_at": "TEXT", "expected": "REAL", "pin": "TEXT"},
    "floorplans": {"plan": "TEXT"},
    "email_messages": {"order_ids": "TEXT", "auto": "INTEGER"},
    "account_holds": {"days_late": "INTEGER", "aging": "TEXT", "last_payment": "TEXT"},
    "orders": {"est_value": "REAL", "est_complete": "INTEGER", "sent_subject": "TEXT", "sent_to": "TEXT", "kind": "TEXT"},
    "delivery_checks": {"kind": "TEXT"},
    "activities": {"order_id": "INTEGER", "temp": "TEXT"},
}

# quick picks for "what am I estimating" on a route stop
ESTIMATE_ITEMS = ["Doors", "Pre-hung", "Bi-pass", "Blinds", "Verticals", "Window screens", "Screen doors",
                  "Baseboards", "Cabinets", "Other"]


def init_db(path=None):
    con = connect(path)
    con.executescript(SCHEMA)
    con.executescript(CRM_SCHEMA)
    from .sales import SALES_SCHEMA
    con.executescript(SALES_SCHEMA)
    from .pricing import PRICE_SCHEMA
    con.executescript(PRICE_SCHEMA)
    from .quotes import QUOTE_SCHEMA
    con.executescript(QUOTE_SCHEMA)
    from .deliveries import DELIVERY_SCHEMA
    con.executescript(DELIVERY_SCHEMA)
    from .company_reports import ITEMS_SCHEMA
    con.executescript(ITEMS_SCHEMA)
    from .company_db import COMPANY_SCHEMA
    con.executescript(COMPANY_SCHEMA)
    from .kitchen import KITCHEN_SCHEMA
    con.executescript(KITCHEN_SCHEMA)
    from .plan_focus import FOCUS_SCHEMA
    con.executescript(FOCUS_SCHEMA)
    from .order_trash import TRASH_SCHEMA
    con.executescript(TRASH_SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    _data_migrations(con)
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    con.commit()
    con.close()


def get_db():
    if "db" not in g:
        from flask import current_app
        g.db = connect(current_app.config.get("DB_PATH"))
    return g.db


def close_db(_e=None):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def q(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def x(sql, args=()):
    con = get_db()
    cur = con.execute(sql, args)
    con.commit()
    return cur.lastrowid


def setting(key, default=None):
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    if r is None or r["value"] is None:
        return DEFAULT_SETTINGS.get(key, default)
    return r["value"]


def set_setting(key, value):
    x("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, value))


def loads(s, default=None):
    try:
        return json.loads(s) if s else (default if default is not None else {})
    except (ValueError, TypeError):
        return default if default is not None else {}


def dumps(o):
    return json.dumps(o, ensure_ascii=False)


def norm_name(s):
    s = (s or "").lower()
    for w in [" apartments", " apartment", " apts", " apt", " the ", " at "]:
        s = s.replace(w, " ")
    return "".join(ch for ch in s if ch.isalnum())


def find_customer(name=None, acct=None, email=None):
    """Best-effort match on account #, then email, then normalised name."""
    if acct:
        r = q("SELECT * FROM customers WHERE acct=? AND acct<>''", (str(acct).strip(),), one=True)
        if r:
            return r
    if email:
        e = email.strip().lower()
        r = q("SELECT c.* FROM customers c WHERE lower(c.email)=?", (e,), one=True)
        if r:
            return r
        r = q("SELECT c.* FROM customers c JOIN contacts k ON k.customer_id=c.id WHERE lower(k.email)=?",
              (e,), one=True)
        if r:
            return r
    if name:
        n = norm_name(name)
        if n:
            rows = q("SELECT * FROM customers")
            for r in rows:
                if norm_name(r["name"]) == n:
                    return r
            # "Tomscot Scottsdale" vs "Tomscot": accept only if exactly one customer is a clear prefix match
            if len(n) >= 6:
                hits = [r for r in rows if len(norm_name(r["name"])) >= 6 and
                        (n.startswith(norm_name(r["name"])) or norm_name(r["name"]).startswith(n))]
                if len(hits) == 1:
                    return hits[0]
    return None


def add_status(order_id, status, note=""):
    x("INSERT INTO order_status_history(order_id, status, note, ts) VALUES (?,?,?,?)",
      (order_id, status, note, now()))


def _data_migrations(con):
    """One-time fixes to saved data when a field changes shape. Each runs once (flag in settings)."""
    import json
    done = {r[0] for r in con.execute("SELECT key FROM settings WHERE key LIKE 'dm_%'")}
    if "dm_blind_slat" not in done:
        from .catalog import OLD_BLIND_STYLES, split_blind_style
        olds = tuple(OLD_BLIND_STYLES)

        def fix(raw, many):
            try:
                d = json.loads(raw or "{}")
            except ValueError:
                return None
            blocks = d.get("blocks") if many else [d]
            if not isinstance(blocks, list) or not any(isinstance(b, dict) and b.get("style") in olds
                                                       for b in blocks):
                return None
            new = [split_blind_style(b) for b in blocks]
            if many:
                d["blocks"] = new
                return json.dumps(d)
            return json.dumps(new[0])
        for oid, raw in con.execute("SELECT id, data FROM orders WHERE data LIKE '%Basswood%' OR "
                                    "data LIKE '%Metal Plus%'").fetchall():
            n = fix(raw, True)
            if n:
                con.execute("UPDATE orders SET data=? WHERE id=?", (n, oid))
        for mid, raw in con.execute("SELECT id, data FROM measurements WHERE data LIKE '%Basswood%' OR "
                                    "data LIKE '%Metal Plus%'").fetchall():
            n = fix(raw, False)
            if n:
                con.execute("UPDATE measurements SET data=? WHERE id=?", (n, mid))
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('dm_blind_slat', '1')")
    if "dm_door_split" not in done:
        from .catalog import OLD_DOOR_STYLES, split_door_style
        olds = set(OLD_DOOR_STYLES)

        def fixd(raw, many):
            try:
                d = json.loads(raw or "{}")
            except ValueError:
                return None
            blocks = d.get("blocks") if many else [d]
            if not isinstance(blocks, list) or not any(isinstance(b, dict) and b.get("style") in olds
                                                       for b in blocks):
                return None
            new = [split_door_style(b) if isinstance(b, dict) else b for b in blocks]
            if many:
                d["blocks"] = new
                return json.dumps(d)
            return json.dumps(new[0])
        for oid, fk, raw in con.execute("SELECT id, form_key, data FROM orders WHERE form_key IN ('door','prehung')"
                                        ).fetchall():
            n = fixd(raw, True)
            if n:
                con.execute("UPDATE orders SET data=? WHERE id=?", (n, oid))
        for mid, raw in con.execute("SELECT id, data FROM measurements WHERE product IN ('interior_door',"
                                    "'entry_door','prehung_door','storage_door')").fetchall():
            n = fixd(raw, False)
            if n:
                con.execute("UPDATE measurements SET data=? WHERE id=?", (n, mid))
        for fid, raw in con.execute("SELECT id, plan FROM floorplans WHERE plan LIKE '%Panel%' OR plan LIKE '%Primecoat%'").fetchall():
            try:
                plan = json.loads(raw or "{}")
            except ValueError:
                continue
            changed = False
            for it in plan.get("items", []) if isinstance(plan, dict) else []:
                tgt = it.get("p") if isinstance(it, dict) else None
                if isinstance(tgt, dict) and it.get("t") == "door" and tgt.get("style") in olds:
                    st, pn, _tp = OLD_DOOR_STYLES[tgt["style"]]
                    tgt["style"], tgt["panels"] = st, tgt.get("panels") or pn
                    changed = True
            if changed:
                con.execute("UPDATE floorplans SET plan=? WHERE id=?", (json.dumps(plan), fid))
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('dm_door_split', '1')")
    if "dm_acct_types" not in done:    # v2.19: contractors / mgmt cos. / vendors live in the CRM too
        from .accounts import guess_type
        for cid, name in con.execute("SELECT id, name FROM customers WHERE coalesce(acct_type,'')=''").fetchall():
            con.execute("UPDATE customers SET acct_type=? WHERE id=?", (guess_type(name), cid))
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('dm_acct_types', '1')")
    if "dm_closet_leaves" not in done:
        from .catalog import split_closet
        for oid, raw in con.execute("SELECT id, data FROM orders WHERE form_key='closet_bifold'").fetchall():
            try:
                d = json.loads(raw or "{}")
            except ValueError:
                continue
            if d.get("_v212"):
                continue
            d["blocks"] = [split_closet(b) for b in d.get("blocks") or []]
            d["_v212"] = True
            con.execute("UPDATE orders SET data=? WHERE id=?", (json.dumps(d), oid))
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('dm_closet_leaves', '1')")
    if "dm_categories_v2" not in done:   # v2.23: QuickBooks item group first ("Shaker" doors were filed as Cabinets)
        recategorize(con)
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('dm_categories_v2', '1')")
    con.commit()


def recategorize(con):
    """Re-file every imported line and item under the current category rules. Returns {table: changed}."""
    from .sales import category_for
    out = {}
    jobs = (("co_lines", "id", ("memo", "item_desc", "item_no")),
            ("sales_lines", "id", ("description", "item_no")),
            ("items", "item_no", ("description", "item_no")))
    for table, key, cols in jobs:
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if not have or "category" not in have or not set(cols) <= have:
            continue
        changed, seen = [], {}
        for r in con.execute(f"SELECT {key}, category, {', '.join(cols)} FROM {table}").fetchall():
            k = r[2:]
            new = seen.get(k)
            if new is None:
                new = seen[k] = category_for(*k)
            if new != r[1]:
                changed.append((new, r[0]))
        con.executemany(f"UPDATE {table} SET category=? WHERE {key}=?", changed)
        out[table] = len(changed)
    return out
