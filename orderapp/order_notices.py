"""Order-received notices: emails saying an order came in (order received / confirmation / acknowledgement).

Each one is matched to an open order by PO # or sales order # (same matching as the morning delivery list) and
that order moves forward to "Confirmed". A sales order # in the email is saved on the order when it has none.
Name-only matches are never automatic - they're listed for you to confirm. Never sends anything.
"""
import re
from datetime import datetime

from . import deliveries as DL
from .db import add_status, dumps, now, q, setting, x

TARGET = "Confirmed"
# how far along an order is - an email never moves an order backwards
RANK = {"Draft": 0, "Ready": 1, "Sent": 2, "Confirmed": 3, "Backordered": 4, "Shipped": 5, "Delivered": 6,
        "Installed": 7, "Completed": 8}
_SO = re.compile(r"(?:sales\s*order|order\s*(?:no|number|#)|s\.?o\.?)\s*[#:.]?\s*([A-Z]{0,3}-?\d{4,})", re.I)

DEFAULT_WORDS = "Order Received, Order Confirmation, Order Acknowledgement, Acknowledgment, Order Entered"


def words():
    raw = setting("notice_subject_words")
    raw = DEFAULT_WORDS if raw is None else raw
    return [w.strip() for w in raw.split(",") if w.strip()]


def is_notice(subject):
    s = (subject or "").lower()
    return any(w.lower() in s for w in words())


def apply(results, text=""):
    """Move the sure matches to Confirmed (only forward). Returns how many moved."""
    so = None
    m = _SO.search(text or "")
    if m:
        so = m.group(1).upper()
    n = 0
    for r in results:
        if not r["sure"]:
            continue
        o = q("SELECT status, vendor_ref FROM orders WHERE id=?", (r["order_id"],), one=True)
        if not o or o["status"] in ("On Hold", "Cancelled"):
            continue
        if so and not (o["vendor_ref"] or "").strip() and r["how"] == "PO #":
            x("UPDATE orders SET vendor_ref=?, updated=? WHERE id=?", (so, now(), r["order_id"]))
        if RANK.get(o["status"], 99) >= RANK[TARGET]:
            continue
        x("UPDATE orders SET status=?, updated=? WHERE id=?", (TARGET, now(), r["order_id"]))
        add_status(r["order_id"], TARGET, f"Order-received email {datetime.now():%m/%d/%Y}"
                                          + (f" (SO {so})" if so else ""))
        n += 1
    return n


def record(text, source, subject="", received="", msg_id=None, auto_apply=True):
    res = DL.match(text)
    applied = apply(res, text) if auto_apply else 0
    cid = x("""INSERT INTO delivery_checks(msg_id, source, subject, received, body, results, applied, created, kind)
               VALUES (?,?,?,?,?,?,?,?,'received')""",
            (msg_id, source, subject, received, (text or "")[:200000], dumps(res),
             dumps([r["order_id"] for r in res if r["sure"]] if auto_apply else []), now()))
    return cid, res, applied


def check_email(prov=None, days=3):
    """Read recent order-received emails from Outlook and confirm the orders they name."""
    from .emailer import provider
    prov = prov or provider(setting)
    out = {"found": 0, "new": 0, "matched": 0, "applied": 0, "error": ""}
    if not words():
        return out
    if prov.name == "eml":
        out["error"] = "Email isn't connected (Settings -> Email)."
        return out
    ok, why = prov.available()
    if not ok:
        out["error"] = why
        return out
    seen = {r["msg_id"] for r in q("SELECT msg_id FROM delivery_checks WHERE msg_id IS NOT NULL")}
    auto = (setting("notice_auto") or "1") == "1"
    msgs = []
    for w in words():
        try:
            msgs += prov.list_messages(days=days, limit=30, subject_word=w)
        except Exception as e:  # noqa: BLE001
            out["error"] = f"Couldn't read the inbox: {e}"
            return out
    uniq = {m["msg_id"]: m for m in msgs}
    out["found"] = len(uniq)
    for mid, m in uniq.items():
        if mid in seen:
            continue
        try:
            full = prov.get_message(mid)
        except Exception:  # noqa: BLE001
            continue
        text = (full.get("subject") or "") + "\n" + (full.get("body") or "") + "\n" + \
            DL.attachment_text(full.get("attachments"))
        _, res, applied = record(text, "email", full.get("subject", ""), full.get("received", ""), mid, auto)
        out["new"] += 1
        out["matched"] += len(res)
        out["applied"] += applied
    return out
