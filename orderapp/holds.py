"""Account holds: properties that fell behind on payments. No purchases until they're brought current.

The list comes from the office's AR report - paste it in (or upload the CSV) on the Account Holds page.
"""
import csv
import io
import json
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


_COLS = {
    "name": ["customer", "customername", "account", "accountname", "name", "property", "propertyname", "billto"],
    "balance": ["balance", "totalbalance", "total", "amountdue", "totaldue", "balancedue", "pastdue", "totalpastdue",
                "amount", "owed"],
    "rep": ["rep", "salesrep", "slsp", "salesperson"],
    "terms": ["terms", "status", "holdstatus", "creditstatus", "hold"],
    "days_late": ["days", "dayspastdue", "dayslate", "oldest", "oldestdays", "age", "daysold"],
    "last_payment": ["lastpayment", "lastpaymentdate", "lastpmt", "lastpaid", "lastpaiddate"],
}
_AGING = {"current": "Current", "130": "1-30", "030": "0-30", "3160": "31-60", "6190": "61-90", "91120": "91-120",
          "over90": "Over 90", "90": "90+", "91": "91+", "over120": "Over 120", "120": "120+"}


def _hkey(h):
    return re.sub(r"[^a-z0-9]", "", (h or "").lower())


def parse_csv(text):
    """A CSV with a header row (the office's hold / aging export) -> rows with aging buckets kept."""
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows:
        return []
    heads = {_hkey(h): h for h in rows[0].keys() if h}
    col = {}
    for f, names in _COLS.items():
        for n in names:
            if n in heads:
                col[f] = heads[n]
                break
    if "name" not in col:
        return []
    aging_cols = [(h, _AGING[k]) for k, h in heads.items() if k in _AGING or k.replace("days", "") in _AGING]
    out = []
    for r in rows:
        name = (r.get(col["name"]) or "").strip()
        if not name or name.lower().startswith(("total", "grand total")):
            continue
        aging = {}
        for h, lab in aging_cols:
            v = _money(r.get(h))
            if v:
                aging[lab] = v
        bal = _money(r.get(col["balance"])) if "balance" in col else None
        if bal is None and aging:
            bal = round(sum(aging.values()), 2)
        if bal is None:
            continue
        days = None
        if "days_late" in col:
            try:
                days = int(float(_money(r.get(col["days_late"])) or 0)) or None
            except (TypeError, ValueError):
                days = None
        if days is None and aging:     # oldest bucket with money in it
            for lab, d in (("Over 120", 121), ("120+", 120), ("91-120", 91), ("Over 90", 91), ("91+", 91), ("90+", 90),
                           ("61-90", 61), ("31-60", 31)):
                if aging.get(lab):
                    days = d
                    break
        out.append({"name": name, "balance": bal, "rep": (r.get(col.get("rep", ""), "") or "").strip().upper(),
                    "terms": (r.get(col.get("terms", ""), "") or "").strip().upper() or "ACCOUNT HOLD",
                    "days_late": days, "aging": aging or None,
                    "last_payment": (r.get(col.get("last_payment", ""), "") or "").strip() or None})
    return out


def parse_report(text):
    """Rows like 'Edge on Seventeen 4,518.66 TA ACCOUNT HOLD' (spaces, tabs or CSV) -> list of dicts.
    A CSV with a header row (Customer, Balance, Rep, Terms, 30/60/90...) is read by column name."""
    first = next((ln for ln in (text or "").splitlines() if ln.strip()), "")
    if "," in first and any(w in first.lower() for w in ("customer", "account", "name", "property")):
        rows = parse_csv(text)
        if rows:
            return rows
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
        aging = json.dumps(r["aging"]) if r.get("aging") else None
        if old:
            x("""UPDATE account_holds SET name=?, balance=?, rep=?, terms=?, customer_id=coalesce(?, customer_id),
                 days_late=coalesce(?, days_late), aging=coalesce(?, aging), last_payment=coalesce(?, last_payment),
                 updated=? WHERE id=?""", (r["name"], r["balance"], r["rep"], r["terms"], cid, r.get("days_late"),
                                         aging, r.get("last_payment"), now(), old["id"]))
            updated += 1
        else:
            x("""INSERT INTO account_holds(name, name_key, customer_id, balance, rep, terms, status, since, updated,
                 days_late, aging, last_payment) VALUES (?,?,?,?,?,?, 'hold', ?, ?, ?, ?, ?)""",
              (r["name"], key, cid, r["balance"], r["rep"], r["terms"], now()[:10], now(), r.get("days_late"), aging,
               r.get("last_payment")))
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


def exposure():
    """For each account on hold: its open orders and open quotes in the app (what shouldn't ship / be sold yet)."""
    from .db import OPEN_STATUSES
    out = {}
    for h in active():
        if not h["customer_id"]:
            continue
        orders = q(f"""SELECT id, title, po, status, order_date FROM orders WHERE customer_id=? AND status IN
                       ({','.join('?' * len(OPEN_STATUSES))}) ORDER BY id DESC""", [h["customer_id"]] + OPEN_STATUSES)
        quotes = q("""SELECT id, number, status FROM quotes WHERE customer_id=? AND status IN ('Draft','Sent','Approved')
                      ORDER BY id DESC""", (h["customer_id"],))
        last = q("SELECT MAX(coalesce(order_date, created)) d FROM orders WHERE customer_id=?", (h["customer_id"],),
                 one=True)
        out[h["id"]] = {"orders": orders, "quotes": quotes, "last_order": (last["d"] or "")[:10] if last else ""}
    return out


def open_orders_on_hold():
    return sum(len(v["orders"]) for v in exposure().values())


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
