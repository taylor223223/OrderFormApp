"""Account holds: properties that fell behind on payments. No purchases until they're brought current.

The list comes from the office's AR report - paste it in (or upload the CSV) on the Account Holds page.
"""
import re

from .db import find_customer, norm_name, now, q, x

HEADER_WORDS = ("customer", "balance")
_LINE = re.compile(r"^\s*(?P<name>.+?)[\s,]+\"?\$?\s*(?P<bal>\(?-?[\d,]*\d(?:\.\d{1,2})?\)?)\"?"
                   r"(?:[\s,]+(?P<rep>[A-Za-z]{1,4}))?(?:[\s,]+(?P<terms>[A-Za-z][A-Za-z .\-/]*))?\s*$")


def _money(s):
    s = (s or "").replace(",", "").replace("$", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    try:
        v = float(s.strip("()"))
    except ValueError:
        return None
    return -v if neg else v


def parse_report(text):
    """Rows like 'Edge on Seventeen 4,518.66 TA ACCOUNT HOLD' (spaces, tabs or CSV) -> list of dicts."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.replace("\t", "  ").strip().strip(",")
        if not line:
            continue
        low = line.lower()
        if all(w in low for w in HEADER_WORDS) or low.startswith(("total", "grand total")):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        name = m.group("name").strip().strip('"').strip(",").strip()
        bal = _money(m.group("bal"))
        if not name or bal is None:
            continue
        out.append({"name": name, "balance": bal, "rep": (m.group("rep") or "").upper(),
                    "terms": (m.group("terms") or "").strip().upper() or "ACCOUNT HOLD"})
    return out


def _link(name):
    c = find_customer(name=name)
    return c["id"] if c else None


def import_rows(rows, clear_missing=False):
    """Add / update holds. Returns (added, updated, cleared)."""
    added = updated = cleared = 0
    seen = set()
    for r in rows:
        key = norm_name(r["name"])
        seen.add(key)
        old = q("SELECT * FROM account_holds WHERE name_key=? AND status='hold'", (key,), one=True)
        cid = _link(r["name"])
        if old:
            x("""UPDATE account_holds SET name=?, balance=?, rep=?, terms=?, customer_id=coalesce(?, customer_id),
                 updated=? WHERE id=?""", (r["name"], r["balance"], r["rep"], r["terms"], cid, now(), old["id"]))
            updated += 1
        else:
            x("""INSERT INTO account_holds(name, name_key, customer_id, balance, rep, terms, status, since, updated)
                 VALUES (?,?,?,?,?,?, 'hold', ?, ?)""",
              (r["name"], key, cid, r["balance"], r["rep"], r["terms"], now()[:10], now()))
            added += 1
    if clear_missing and rows:
        for h in q("SELECT id, name_key FROM account_holds WHERE status='hold'"):
            if h["name_key"] not in seen:
                clear(h["id"], "Not on the latest report")
                cleared += 1
    return added, updated, cleared


def clear(hid, note=""):
    h = q("SELECT notes FROM account_holds WHERE id=?", (hid,), one=True)
    notes = (h["notes"] or "") if h else ""
    if note:
        notes = (notes + "\n" if notes else "") + f"{now()[:10]}: {note}"
    x("UPDATE account_holds SET status='cleared', cleared=?, updated=?, notes=? WHERE id=?",
      (now()[:10], now(), notes, hid))


def active():
    return q("""SELECT h.*, c.name AS cname FROM account_holds h LEFT JOIN customers c ON c.id=h.customer_id
                WHERE h.status='hold' ORDER BY h.balance DESC""")


def count():
    try:
        return q("SELECT COUNT(*) n FROM account_holds WHERE status='hold'", one=True)["n"]
    except Exception:   # noqa: BLE001 - table not there yet on a very old database
        return 0


def hold_for(customer_id=None, name=None):
    """The active hold for a customer (by link, or by matching name), or None."""
    try:
        if customer_id:
            h = q("SELECT * FROM account_holds WHERE status='hold' AND customer_id=?", (customer_id,), one=True)
            if h:
                return h
            c = q("SELECT name FROM customers WHERE id=?", (customer_id,), one=True)
            name = name or (c["name"] if c else None)
        if name:
            h = q("SELECT * FROM account_holds WHERE status='hold' AND name_key=?", (norm_name(name),), one=True)
            if h and customer_id and not h["customer_id"]:
                x("UPDATE account_holds SET customer_id=? WHERE id=?", (customer_id, h["id"]))
            return h
    except Exception:   # noqa: BLE001
        return None
    return None


def hold_message(h):
    return (f"ACCOUNT HOLD - {h['name']} owes ${h['balance']:,.2f}. "
            "The account must be brought current before any purchase.")
