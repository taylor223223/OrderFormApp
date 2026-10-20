"""Deleting orders safely: a deleted order goes to "Recently deleted" for 30 days and can be put back
(Undo right after deleting, or Restore from the list on Order Tracking). After 30 days it's gone for good.

The order row and everything hanging off it (status history, photo records) are saved as JSON in
order_trash, then removed from the live tables - so every other screen simply stops seeing it.
Photo files stay on disk until the order is purged, so a restore gets its photos back too.
"""
import json
import shutil
from datetime import datetime, timedelta

from .db import get_db, now, q, x

TRASH_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_trash (
    id INTEGER PRIMARY KEY, order_id INTEGER, label TEXT, deleted_at TEXT, data TEXT NOT NULL
);
"""
KEEP_DAYS = 30
CHILD_TABLES = ("order_status_history", "order_photos")


def _rows(table, where, args):
    return [dict(r) for r in q(f"SELECT * FROM {table} WHERE {where}", args)]


def delete(oid):
    """Move an order to Recently deleted. Returns the trash id (None if the order doesn't exist)."""
    o = q("""SELECT o.*, c.name cname FROM orders o LEFT JOIN customers c ON c.id=o.customer_id
             WHERE o.id=?""", (oid,), one=True)
    if not o:
        return None
    order = {k: o[k] for k in o.keys() if k != "cname"}
    snap = {"order": order, "children": {t: _rows(t, "order_id=?", (oid,)) for t in CHILD_TABLES}}
    # the contact-log entry the app made for this order goes with it; entries you logged yourself stay
    auto = "order_id=? AND owner='auto' AND subject LIKE ?"
    snap["children"]["activities"] = _rows("activities", auto, (oid, f"Order #{oid}:%"))
    label = " - ".join(p for p in [o["cname"], o["title"], (f"PO {o['po']}" if o["po"] else "")] if p)
    tid = x("INSERT INTO order_trash(order_id, label, deleted_at, data) VALUES (?,?,?,?)",
            (oid, label, now(), json.dumps(snap, default=str)))
    x(f"DELETE FROM activities WHERE {auto}", (oid, f"Order #{oid}:%"))
    x("UPDATE activities SET order_id=NULL WHERE order_id=?", (oid,))
    x("DELETE FROM orders WHERE id=?", (oid,))      # status history + photo rows cascade
    return tid


def _insert(table, row):
    cols = list(row.keys())
    have = {r[1] for r in get_db().execute(f"PRAGMA table_info({table})")}
    cols = [c for c in cols if c in have]
    return x(f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
             [row[c] for c in cols])


def restore(tid):
    """Put a deleted order back (same order # when it's still free). Returns the order id or None."""
    t = q("SELECT * FROM order_trash WHERE id=?", (tid,), one=True)
    if not t:
        return None
    snap = json.loads(t["data"])
    order = dict(snap["order"])
    if order.get("customer_id") and not q("SELECT 1 FROM customers WHERE id=?", (order["customer_id"],), one=True):
        order["customer_id"] = None
    if q("SELECT 1 FROM orders WHERE id=?", (order["id"],), one=True):
        order.pop("id")                             # that # was reused by a newer order - give it a new one
    new_id = _insert("orders", order)
    for table, rows in snap.get("children", {}).items():
        for r in rows:
            r = dict(r)
            r.pop("id", None)
            r["order_id"] = new_id
            if table == "activities" and new_id != t["order_id"]:
                r["subject"] = (r.get("subject") or "").replace(f"Order #{t['order_id']}:", f"Order #{new_id}:", 1)
            _insert(table, r)
    if new_id != t["order_id"]:
        _move_photos(t["order_id"], new_id)
    x("DELETE FROM order_trash WHERE id=?", (tid,))
    return new_id


def _move_photos(old, new):
    import os
    from .paths import data_dir
    src = os.path.join(data_dir(), "photos", str(int(old)))
    dst = os.path.join(data_dir(), "photos", str(int(new)))
    if os.path.isdir(src) and not os.path.exists(dst):
        shutil.move(src, dst)
        for p in q("SELECT id, path FROM order_photos WHERE order_id=?", (new,)):
            x("UPDATE order_photos SET path=? WHERE id=?", (p["path"].replace(src, dst), p["id"]))


def recent():
    return q("SELECT id, order_id, label, deleted_at FROM order_trash ORDER BY deleted_at DESC")


def purge(days=KEEP_DAYS):
    """Remove deleted orders older than `days` (and their photo files) for good."""
    import os
    from .paths import data_dir
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    old = q("SELECT id, order_id FROM order_trash WHERE deleted_at < ?", (cutoff,))
    for t in old:
        if not q("SELECT 1 FROM orders WHERE id=?", (t["order_id"],), one=True):
            shutil.rmtree(os.path.join(data_dir(), "photos", str(int(t["order_id"]))), ignore_errors=True)
        x("DELETE FROM order_trash WHERE id=?", (t["id"],))
    return len(old)
